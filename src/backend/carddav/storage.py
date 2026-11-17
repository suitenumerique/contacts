"""Address books, cards, ETags and sync tokens over contacts and groups.

Two collections are exposed per user:

``personal``
    The user's own contacts. Read/write. A card here maps 1:1 to a
    ``Contact`` row owned by the user.
``directory``
    The organization-wide rows (``owner`` NULL) plus anything provisioned
    from an external ``source``. Read-only, because ``Contact.is_writable``
    says so.

Devices whose credential asks for groups (``Channel.settings["groups"]``)
also get the groups of each book as group cards: the user's own groups in
``personal``, the organization's in ``directory``. A group card only lists
the members that are cards of the same book.

A card created by a client keeps the name the client chose (see
:class:`carddav.models.CardName`); every other card is ``<uuid>.vcf``.
"""

import hashlib
import uuid
from urllib.parse import quote

from django.conf import settings
from django.db.models import Max, OuterRef, Prefetch, Q, Subquery

from core.enums import Scope
from core.models import Contact, ContactGroup, ContactLabel
from core.services.vcf_service import (
    card_uid,
    ref_uids,
    serialize_contact,
    serialize_group,
)

from carddav.models import CardName, ContactChange, PurgeMark, SyncCounter
from carddav.xml import strip_non_xml_chars

PERSONAL = "personal"
DIRECTORY = "directory"
ADDRESS_BOOKS = (PERSONAL, DIRECTORY)

DISPLAY_NAMES = {
    PERSONAL: "My contacts",
    DIRECTORY: "Organization directory",
}

MAX_NAME_LENGTH = 255


def max_resource_size():
    """Largest card body PUT accepts, in octets.

    The VCF import ceiling, capped by what Django will read into memory at
    all, so the value advertised in ``max-resource-size`` is the one
    enforced.
    """
    return min(
        settings.CONTACTS_VCF_MAX_FILE_SIZE, settings.DATA_UPLOAD_MAX_MEMORY_SIZE
    )


def book_href(base, book):
    """URL of an address book collection."""
    return f"{base}addressbooks/{book}/"


def card_href(base, book, name):
    """URL of the card called *name* inside *book*."""
    return f"{book_href(base, book)}{quote(name)}"


def card_name(card):
    """The resource name of *card* (a contact or a group) in its book."""
    return getattr(card, "card_name", None) or f"{card.pk}.vcf"


def is_group(card):
    """Whether *card* is a group card rather than a contact."""
    return isinstance(card, ContactGroup)


def pk_from_name(name):
    """The card id a ``<uuid>.vcf`` name stands for, or None.

    Only the exact form this server emits counts: hrefs are compared as
    strings, so ``<UUID>.vcf`` in upper case is a different resource.
    """
    stem, dot, extension = name.rpartition(".")
    if not dot or extension != "vcf":
        return None
    try:
        contact_id = uuid.UUID(stem)
    except ValueError:
        return None
    return contact_id if str(contact_id) == stem else None


def visible_contacts(user):
    """Every contact row *user* may see, trashed ones excluded.

    Mirrors ``ContactViewSet.get_queryset``: own rows plus org-wide rows in
    the same organization, with a row the user has overlaid hidden in
    favour of the overlay so the client sees one merged card, not two.
    """
    return (
        Contact.objects.visible_to(user)
        .filter(deleted_at__isnull=True)
        .exclude(overlays__owner=user)
        .select_related("overlay_of")
    )


def _book_filter(user, book):
    """Which of the visible rows (contacts or groups) belong to *book*.

    ``personal``: the user's own writable rows. ``directory``: the
    organization's, and the user's own rows provisioned by a source.
    """
    if book == PERSONAL:
        return Q(scope=Scope.PERSONAL, owner=user, source="")
    return Q(scope=Scope.ORGANIZATION) | ~Q(source="")


def _annotated(queryset, user, named):
    """*queryset* with each card's journal number, and its card name."""
    queryset = queryset.annotate(
        card_seq=Subquery(
            ContactChange.objects.filter(
                organization_id=OuterRef("organization_id"), contact_id=OuterRef("pk")
            ).values("seq")[:1]
        )
    )
    if not named:
        return queryset
    return queryset.annotate(
        card_name=Subquery(
            CardName.objects.filter(owner=user, contact_id=OuterRef("pk")).values(
                "name"
            )[:1]
        )
    )


def _with_labels(queryset, user):
    return queryset.prefetch_related(
        Prefetch("labels", queryset=ContactLabel.objects.filter(owner=user))
    )


def book_queryset(user, book):
    """The contacts belonging to *book*, carrying their card names."""
    queryset = _with_labels(visible_contacts(user), user)
    return _annotated(
        queryset.filter(_book_filter(user, book)), user, named=book == PERSONAL
    )


def book_groups(user, book):
    """The groups belonging to *book*, carrying their card names."""
    groups = ContactGroup.objects.visible_to(user).filter(_book_filter(user, book))
    return _annotated(groups, user, named=book == PERSONAL)


def book_cards(user, book, with_groups):
    """Every card of *book*: its contacts, then its groups when asked for."""
    cards = list(book_queryset(user, book))
    if with_groups:
        cards += list(book_groups(user, book))
    return cards


def personal_rows(user):
    """The user's own writable rows, trashed ones included (for PUT)."""
    queryset = Contact.objects.filter(
        owner=user, organization=user.organization, source=""
    ).select_related("overlay_of")
    return _annotated(_with_labels(queryset, user), user, named=True)


def find_cards(user, book, names, queryset=None, groups=None):
    """Map each of *names* found in *book* to its card, in a few queries.

    *groups* is the group queryset to look in too, None for no groups.
    """
    if queryset is None:
        queryset = book_queryset(user, book)
    ids = {}
    if book == PERSONAL:
        ids = dict(
            CardName.objects.filter(owner=user, name__in=names).values_list(
                "name", "contact_id"
            )
        )
    for name in names:
        if name not in ids and (card_id := pk_from_name(name)) is not None:
            ids[name] = card_id
    if not ids:
        return {}
    cards = {card.pk: card for card in queryset.filter(pk__in=ids.values())}
    if groups is not None and (missing := set(ids.values()) - set(cards)):
        cards |= {group.pk: group for group in groups.filter(pk__in=missing)}
    return {
        name: cards[card_id]
        for name, card_id in ids.items()
        # A name is only found under its own card name, so a named card is
        # not also reachable as <uuid>.vcf.
        if card_id in cards and card_name(cards[card_id]) == name
    }


def find_card(user, book, name, queryset=None, groups=None):
    """The card called *name* in *book*, or None."""
    return find_cards(user, book, [name], queryset, groups).get(name)


def removed_names(user, book, card_ids):
    """Card names for *card_ids*, which may no longer exist."""
    names = {}
    if book == PERSONAL:
        names = dict(
            CardName.objects.filter(owner=user, contact_id__in=card_ids).values_list(
                "contact_id", "name"
            )
        )
    return [names.get(card_id, f"{card_id}.vcf") for card_id in card_ids]


_UNKNOWN = object()


def etag_for(card):
    """A strong ETag for *card*, a contact or a group.

    Derived from the card's journal number: every write that changes a
    rendered card journals it (see :mod:`carddav.signals`), including the
    ones that leave its row alone (a label, a member, an overlay's
    parent). Cheap enough for every row of a PROPFIND, which reads the
    number with the rows.

    A row written around the signals has no number yet; its stamps stand
    in until its next journaled write.
    """
    seq = getattr(card, "card_seq", _UNKNOWN)
    if seq is _UNKNOWN:
        seq = (
            ContactChange.objects.filter(
                organization_id=card.organization_id, contact_id=card.pk
            )
            .values_list("seq", flat=True)
            .first()
        )
    if seq is not None:
        marker = f"seq:{seq}"
    else:
        stamps = [card.updated_at]
        if getattr(card, "overlay_of_id", None) and card.overlay_of is not None:
            stamps.append(card.overlay_of.updated_at)
        marker = ":".join(stamp.isoformat() for stamp in stamps if stamp)
    digest = hashlib.sha256(f"{card.pk}:{marker}".encode()).hexdigest()[:32]
    return f'"{digest}"'


def group_member_uids(user, book, group):
    """The UIDs of *group*'s members that are cards of *book*.

    Memberships are on canonical rows; a member the user overlaid shows
    as its overlay, in the personal book.
    """
    member_ids = list(group.memberships.values_list("contact_id", flat=True))
    if not member_ids:
        return []
    rows = visible_contacts(user).filter(
        Q(pk__in=member_ids) | Q(overlay_of_id__in=member_ids),
        _book_filter(user, book),
    )
    return [card_uid(row) for row in rows]


def resolve_member_refs(user, refs):
    """Map each MEMBER value of *refs* to the canonical contact it names.

    Only cards of the personal book count, since that is where a client
    sends group cards. A ref naming no such card is left out.
    """
    wanted = {uid: ref for ref in refs for uid in ref_uids(ref)}
    if not wanted:
        return {}
    ids = []
    for uid in wanted:
        try:
            ids.append(uuid.UUID(uid))
        except ValueError:
            pass
    lines = [[f"UID:{uid}"] for uid in wanted]
    clause = Q(pk__in=ids)
    for line in lines:
        clause |= Q(vcard_extra__UID__contains=line)
        clause |= Q(overlay_of__vcard_extra__UID__contains=line)
    rows = book_queryset(user, PERSONAL).filter(clause)
    return {
        wanted[uid]: row.overlay_of_id or row.pk
        for row in rows
        if (uid := card_uid(row)) in wanted
    }


def render_card(user, book, card):
    """Serialize one card to a vCard, honouring overlay merging.

    Leave out the characters that XML 1.0 does not allow. The card is then
    the same text in a vCard body and in an XML document.
    """
    if is_group(card):
        text = serialize_group(card, group_member_uids(user, book, card))
    else:
        text = serialize_contact(card)
    return strip_non_xml_chars(text)


# --- sync-collection -------------------------------------------------

# ``urn:contacts:sync:<organization id>:<seq>``, where seq is a
# change-journal number (see carddav.models). Journal numbers are counted
# per organization, so the token names the organization it counts in.
SYNC_TOKEN_PREFIX = "urn:contacts:sync:"  # noqa: S105  (a URN, not a secret)

# A number over the journal's bigint column would make the delta query
# raise instead of returning nothing.
MAX_SYNC_SEQ = 2**63 - 1


def make_sync_token(user, seq):
    """The token standing for journal number *seq* in *user*'s organization."""
    return f"{SYNC_TOKEN_PREFIX}{user.organization_id}:{seq}"


def parse_sync_token(user, token):
    """The journal number in *token*, or None when it is not usable here.

    Refused: anything malformed, a token from another organization, and a
    number this organization has not reached (it was not issued here).
    """
    if not token or not token.startswith(SYNC_TOKEN_PREFIX):
        return None
    organization_id, _, raw_seq = token[len(SYNC_TOKEN_PREFIX) :].rpartition(":")
    if organization_id != str(user.organization_id):
        return None
    try:
        seq = int(raw_seq)
    except ValueError:
        return None
    if not 0 <= seq <= MAX_SYNC_SEQ:
        return None
    current = (
        SyncCounter.objects.filter(organization_id=user.organization_id)
        .values_list("seq", flat=True)
        .first()
    )
    return seq if seq <= (current or 0) else None


def book_change_filter(user, book):
    """Journal rows (and purge marks) belonging to *book*, as *user* sees it.

    Same visibility rule as the books themselves, decided from the columns
    the journal copies rather than from the contact, which may be gone.
    Other users' rows must not surface here, not even as bare hrefs.
    """
    if book == PERSONAL:
        return Q(owner=user, from_source=False)
    return (Q(owner__isnull=True) | Q(owner=user)) & (
        Q(owner__isnull=True) | Q(from_source=True)
    )


def _latest(model, user, book):
    return (
        model.objects.filter(organization_id=user.organization_id)
        .filter(book_change_filter(user, book))
        .aggregate(latest=Max("seq"))["latest"]
        or 0
    )


def purged_seq(user, book):
    """The newest journal number a purge dropped from *book*, or 0."""
    return _latest(PurgeMark, user, book)


def book_sync_seq(user, book):
    """The newest journal number affecting *book*, or 0 when there is none.

    Scoped to the book rather than to the organization so an unchanged
    collection keeps returning the same ctag: a colleague editing their own
    contacts is not a reason for this client to re-read anything. Purge
    marks count too, so dropping a tombstone never lowers the value.
    """
    return max(_latest(ContactChange, user, book), purged_seq(user, book))


def changes_since(user, book, since_seq, until_seq, with_groups=False):
    """Return ``(changed, removed_ids)`` for *book* in ``(since, until]``.

    The journal says which cards moved; the books themselves say what they
    are now. Anything the journal names that the book no longer holds is
    reported as removed (trashed, hidden behind a new overlay, or erased
    outright), so the three cases need no separate bookkeeping. Group
    cards are only considered *with_groups*.
    """
    rows = list(
        ContactChange.objects.filter(
            organization_id=user.organization_id,
            seq__gt=since_seq,
            seq__lte=until_seq,
        )
        .filter(book_change_filter(user, book))
        .values_list("contact_id", "is_group")
    )
    contact_ids = {pk for pk, group in rows if not group}
    group_ids = {pk for pk, group in rows if group} if with_groups else set()

    changed = []
    if contact_ids:
        changed += list(book_queryset(user, book).filter(pk__in=contact_ids))
    if group_ids:
        changed += list(book_groups(user, book).filter(pk__in=group_ids))
    present = {card.pk for card in changed}
    return changed, sorted((contact_ids | group_ids) - present, key=str)
