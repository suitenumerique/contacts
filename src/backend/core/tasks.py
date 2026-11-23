"""Background tasks for the contacts core application."""

# pylint: disable=import-outside-toplevel

import logging

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction

from core.enums import Scope
from core.task_utils import register_task, set_task_progress
from core.validators import broken_value_rule, validate_photo

logger = logging.getLogger(__name__)

# How many rejected entries the result describes individually. The summary
# still counts them all; this only bounds what travels back to the browser.
MAX_ERROR_DETAILS = 20

# How many characters of a refused value an error detail quotes.
MAX_QUOTED_VALUE_LENGTH = 100

# How long an uploaded file waits in the cache for the worker (seconds).
IMPORT_FILE_TTL = 60 * 60

# Hard limit on one import run (milliseconds).
IMPORT_TIME_LIMIT = 15 * 60 * 1000


def dedup_keys(card, uid=""):
    """Keys identifying a card, used to spot one that is already there.

    *card* holds the emails, the phones, the given, family and full name.
    The first rule that applies gives the keys:

    1. Each email address is a key.
    2. The name and the digits of the first phone that has digits.
    3. The name and *uid*, for a card that has both. A name alone does not
       tell two persons apart.
    4. No key. The card matches nothing.

    The name is the given and family names, or the full name when the card
    has neither. Then "Dr Jean Dupont" in FN matches the "Jean Dupont" that
    the API derives. Case and extra spaces in the name are ignored.
    """
    keys = {
        f"email:{(entry.get('value') or '').strip().lower()}"
        for entry in card["emails"] or []
        if (entry.get("value") or "").strip()
    }
    if keys:
        return keys
    name = " ".join(f"{card['given_name']} {card['family_name']}".split()) or " ".join(
        (card["full_name"] or "").split()
    )
    name = name.casefold()
    for entry in card["phones"] or []:
        digits = "".join(ch for ch in (entry.get("value") or "") if ch.isdigit())
        if digits:
            return {f"name:{name}|{digits}"}
    return {f"name:{name}|uid:{uid}"} if name and uid else set()


def _rejection(fields):
    """Why this card cannot become a contact, as ``(reason, value)``.

    Checks two of the rules that ``ContactSerializer`` applies to a create:
    the card has a name, and its emails, phones, addresses and URLs obey
    the value rules of ``core.validators``. Returns ``("", "")`` when the
    card breaks neither. The creation of the contact can still fail for
    another reason.

    The parsers already fill ``full_name`` from the name parts (and a vCard
    from its first email, as vCard requires FN), so the name check is on
    ``full_name``. The parsers give safe types and no blank value, so these
    two value rules need no check.
    """
    if not fields["full_name"].strip():
        return "no_name", ""
    for field in ("emails", "phones", "addresses", "urls"):
        reason, value = broken_value_rule(field, fields.get(field, []))
        if reason:
            return reason, value
    return "", ""


def _quoted(value):
    """*value* as an error detail quotes it. A long value is cut."""
    if len(value) <= MAX_QUOTED_VALUE_LENGTH:
        return value
    return f"{value[:MAX_QUOTED_VALUE_LENGTH]}…"


# Google Contacts exports its system groups among a contact's labels: in
# the vCard CATEGORIES ("myContacts,starred") and in the CSV "Labels"
# column ("* myContacts ::: * starred"). Like Messages does with Gmail's
# labels, they are not imported as labels: "starred" is a favorite, and
# "myContacts" (every contact of the account) means nothing here.
GOOGLE_SYSTEM_LABELS = {"mycontacts": None, "starred": "favorite"}


def _in_scope(model, user, scope):
    """The rows of *model* an import into *scope* writes to and matches."""
    if scope == Scope.ORGANIZATION:
        return model.objects.filter(
            scope=Scope.ORGANIZATION, organization_id=user.organization_id
        )
    return model.objects.filter(scope=Scope.PERSONAL, owner=user)


def _create_contact(user, fields, scope=Scope.PERSONAL):
    """Create one contact, attaching (and creating) its labels.

    An organization contact carries no labels, and a star is per user.
    """
    from core.models import Contact, ContactLabel  # noqa: PLC0415

    names = fields.pop("label_names", [])
    label_names = [
        name for name in names if name.casefold() not in GOOGLE_SYSTEM_LABELS
    ]
    favorite = any(
        GOOGLE_SYSTEM_LABELS.get(name.casefold()) == "favorite" for name in names
    )
    if fields.get("photo"):
        try:
            validate_photo(fields["photo"])
        except ValidationError:
            # An unusable photo is not a reason to lose the card.
            fields["photo"] = ""
    personal = scope == Scope.PERSONAL
    with transaction.atomic():
        contact = Contact.objects.create(
            scope=scope,
            owner=user if personal else None,
            organization=user.organization,
            created_by=user,
            updated_by=user,
            **fields,
        )
        if personal and label_names:
            contact.labels.add(*ContactLabel.for_names(user, label_names))
        if personal and favorite:
            contact.favorited_by.add(user)
    return contact


def _known_contacts(user, scope=Scope.PERSONAL):
    """Map each dedup key of the contacts in *scope* to the ID of its contact.

    Trashed rows are included so a re-imported card restores them rather
    than creating a copy.
    """
    from core.models import Contact  # noqa: PLC0415
    from core.services.vcf_service import stored_uid  # noqa: PLC0415

    known = {}
    rows = (
        _in_scope(Contact, user, scope)
        .filter(overlay_of__isnull=True)
        .values(
            "pk",
            "emails",
            "given_name",
            "family_name",
            "full_name",
            "phones",
            "vcard_extra__UID",
        )
    )
    for row in rows:
        # The UID that an export writes on the card of the row.
        uid = stored_uid({"UID": row["vcard_extra__UID"] or []}) or str(row["pk"])
        for key in dedup_keys(row, uid):
            known.setdefault(key, row["pk"])
    return known


def _restore_trashed(pks):
    """Restore the first trashed contact among *pks*; whether there was one."""
    from core.models import Contact  # noqa: PLC0415

    restored = Contact.objects.filter(pk__in=pks, deleted_at__isnull=False).first()
    if restored is None:
        return False
    restored.deleted_at = None
    with transaction.atomic():
        restored.save(update_fields=["deleted_at", "updated_at"])
    return True


def import_contacts(user, file_format, data, scope=Scope.PERSONAL):  # pylint: disable=too-many-locals
    """Import contacts from VCF or CSV bytes for *user*.

    Into the user's own address book, or with *scope* ORGANIZATION into
    their organization's (the caller checks the user administers it).

    A card matching one of the contacts already there (see
    ``dedup_keys``) is skipped. When the match is in the corbeille, the
    contact is restored instead and counted as imported.

    The group cards of a vCard file are imported after the contacts (see
    ``_import_groups``).

    Returns ``{imported, skipped, errors, total, groups, error_details}``.
    Parser exceptions propagate to the caller.
    """
    from core.services.csv_service import parse_csv  # noqa: PLC0415
    from core.services.vcf_service import (  # noqa: PLC0415
        parse_vcf_file,
        stored_uid,
    )

    set_task_progress(0, {"message": "Parsing file..."})

    if file_format == "csv":
        cards, parse_errors = parse_csv(data)
        groups = []
    else:
        cards, groups, parse_errors = parse_vcf_file(data)

    known = _known_contacts(user, scope)
    # The UIDs of the file's cards -> the contact each became or matched,
    # for the members of its groups.
    uids = {}
    counts = {"imported": 0, "skipped": 0, "rejected": 0}
    total = len(cards)
    # What went wrong, per rejected entry, so the user can fix the file
    # instead of being told only how many rows failed. Capped: the point is
    # to show the user a list, not to ship the whole file back.
    details = []
    if parse_errors:
        details.append({"entry": None, "name": "", "reason": "unreadable"})

    for index, fields in enumerate(cards):
        uid = stored_uid(fields.get("vcard_extra") or {})
        outcome, contact_id, reason, value = _import_card(
            user, fields, uid, known, scope
        )
        counts[outcome] += 1
        if outcome == "rejected":
            if len(details) < MAX_ERROR_DETAILS:
                details.append(
                    {
                        "entry": index + 1,
                        "name": fields["full_name"],
                        "reason": reason,
                        "value": _quoted(value),
                    }
                )
        elif uid:
            uids[uid] = contact_id

        if total and index % 20 == 0:
            set_task_progress(
                int(90 * (index + 1) / total),
                {"message": f"Imported {counts['imported']}/{total} contacts..."},
            )

    result = {
        "imported": counts["imported"],
        "skipped": counts["skipped"],
        "errors": parse_errors + counts["rejected"],
        "total": total,
        "groups": _import_groups(user, groups, uids, scope),
        "error_details": details,
    }
    set_task_progress(100, {"message": "Import complete", **result})
    return result


def _import_card(user, fields, uid, known, scope):
    """Import one parsed card: ``(outcome, contact id, reason, value)``.

    *outcome* is "imported" (created, or restored from the corbeille),
    "skipped" (already there: the id is the match's) or "rejected", with
    the reason and the offending value. *known* gains the created card.
    *uid* is the UID of the card, or "".
    """
    card_keys = dedup_keys(fields, uid)
    reason, value = _rejection(fields)
    if reason:
        return "rejected", None, reason, value
    if matches := [known[key] for key in card_keys if key in known]:
        outcome = "imported" if _restore_trashed(matches) else "skipped"
        return outcome, matches[0], "", ""
    try:
        contact = _create_contact(user, fields, scope)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Failed to import a contact")
        return "rejected", None, "failed", ""
    for key in card_keys:
        known.setdefault(key, contact.pk)
    return "imported", contact.pk, "", ""


def _import_groups(user, groups, uids, scope=Scope.PERSONAL):
    """Import the group cards of a file; return how many were imported.

    A group joins the group of the same name in *scope* (so importing a
    file again adds nothing), or is created. Its members are the file's
    cards its member lines name, through *uids*, else the contacts in
    *scope* carrying that UID. A member naming neither is left out.
    """
    from core.models import ContactGroup, ContactGroupMembership  # noqa: PLC0415
    from core.services.vcf_service import ref_uids  # noqa: PLC0415

    count = 0
    for fields in groups:
        name = " ".join(fields["name"].split())[:255]
        if not name:
            continue
        wanted = {uid for ref in fields["member_refs"] for uid in ref_uids(ref)}
        member_ids = {uids[uid] for uid in wanted if uid in uids}
        member_ids |= _contacts_with_uids(user, wanted - uids.keys(), scope)
        with transaction.atomic():
            group = (
                _in_scope(ContactGroup, user, scope)
                .filter(source="", name__iexact=name)
                .first()
            )
            if group is None:
                group = ContactGroup.objects.create(
                    scope=scope,
                    owner=user if scope == Scope.PERSONAL else None,
                    organization=user.organization,
                    name=ContactGroup.free_name(user, name, scope=scope),
                    vcard_extra=fields["vcard_extra"],
                    created_by=user,
                    updated_by=user,
                )
            for pk in member_ids:
                ContactGroupMembership.objects.get_or_create(group=group, contact_id=pk)
        count += 1
    return count


def _contacts_with_uids(user, uids, scope):
    """The canonical ids of the live contacts in *scope* whose UID is in *uids*."""
    import uuid  # noqa: PLC0415

    from django.db.models import Q  # noqa: PLC0415

    from core.models import Contact  # noqa: PLC0415
    from core.services.vcf_service import card_uid  # noqa: PLC0415

    if not uids:
        return set()
    ids = []
    for uid in uids:
        try:
            ids.append(uuid.UUID(uid))
        except ValueError:
            pass
    clause = Q(pk__in=ids)
    for uid in uids:
        clause |= Q(vcard_extra__UID__contains=[f"UID:{uid}"])
        clause |= Q(overlay_of__vcard_extra__UID__contains=[f"UID:{uid}"])
    rows = _in_scope(Contact, user, scope).filter(clause, deleted_at__isnull=True)
    return {
        row.overlay_of_id or row.pk
        for row in rows.select_related("overlay_of")
        if card_uid(row) in uids
    }


def _failure(error):
    """Task result for a file that could not be imported at all."""
    return {
        "status": "FAILURE",
        "result": {
            "imported": 0,
            "skipped": 0,
            "errors": 1,
            "total": 0,
            "groups": 0,
            "error_details": [{"entry": None, "name": "", "reason": "unreadable"}],
        },
        "error": error,
    }


@register_task(queue="import", max_retries=0, time_limit=IMPORT_TIME_LIMIT)
def import_contacts_task(user_id, file_format, file_key, scope=Scope.PERSONAL.value):
    """Import the file stored in the cache under *file_key* for *user_id*.

    The view stores the upload in the cache rather than in the task
    message; the entry is dropped as soon as it is read. Not retried: a
    retry would find the file gone. The result follows the
    ``{status, result, error}`` shape ``TaskDetailView`` unpacks.
    """
    from core.models import User  # noqa: PLC0415

    data = cache.get(file_key)
    cache.delete(file_key)
    if data is None:
        return _failure("The uploaded file expired before it was processed.")

    user = User.objects.get(pk=user_id)
    try:
        result = import_contacts(user, file_format, data, Scope(scope))
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Import of a %s file failed", file_format)
        return _failure("The file could not be read.")
    return {"status": "SUCCESS", "result": result, "error": None}
