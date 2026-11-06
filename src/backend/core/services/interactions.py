"""Interactions other Suite apps report: shares, emails, invitations...

Each kind weighs what ``INTERACTION_KINDS`` says. One call is one event (a
share, a message, an invitation) and its items are the people or groups in
it: past ``INTERACTION_MASS_THRESHOLD`` of them, each one weighs less, since
a message to 200 people says little about any of them.

An item names a contact or group by the uid of its card (an app that picked
it from the search) or by email address (an app that only knows addresses,
like Messages): ``resolve_email`` finds the row behind an address. A uid
doesn't say whether it is a contact or a group, and doesn't need to: ids
are random UUIDs, so one exists in at most one of the two tables.
"""

from django.conf import settings
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone

from core import models
from core.utils import fold

CONTACT = "contact"
GROUP = "group"
UID = "uid"
EMAIL = "email"
ITEM_TYPES = (UID, EMAIL)
# Contacts sharing the searched address considered by resolve_email.
MAX_EMAIL_CANDIDATES = 20


def kinds():
    """The interaction kinds this instance knows."""
    return tuple(settings.INTERACTION_KINDS)


def weight(kind, people=1):
    """What one interaction of *kind*, in an event with *people*, adds."""
    base = float(settings.INTERACTION_KINDS[kind]["weight"])
    threshold = settings.INTERACTION_MASS_THRESHOLD
    if people > threshold:
        return base * threshold / people
    return base


def _personal_first(user):
    return Case(
        When(owner_id=user.pk, then=Value(0)),
        default=Value(1),
        output_field=IntegerField(),
    )


def is_own_address(user, address):
    """Whether *address* is *user*'s own."""
    return (address or "").strip().lower() == (user.email or "").strip().lower()


def find_contact(user, address):
    """The contact *user* knows under *address*, or None.

    The user's own cards (their overlay of an organization card included)
    before the organization's, oldest first. Every address is a word of
    ``search_text``, so the trigram index finds the candidates; the exact
    address is checked on them.
    """
    address = (address or "").strip().lower()
    if not address:
        return None
    candidates = (
        models.Contact.objects.visible_to(user)
        .filter(deleted_at__isnull=True, search_text__contains=f" {fold(address)}")
        .select_related("overlay_of")
        .order_by(_personal_first(user), "created_at", "pk")[:MAX_EMAIL_CANDIDATES]
    )
    # The filter above only checks that a word *starts* with the address,
    # anywhere in search_text: it also matches longer addresses
    # (bob@example.org.uk) and an address typed in another field (company,
    # nickname). Keep the first contact whose own addresses include it.
    for contact in candidates:
        if address in contact.email_values:
            return contact
    return None


def resolve_email(user, address):
    """The contact or group *user* knows under *address*, or None.

    A contact first (``find_contact``), then a group with that address (a
    shared mailbox). The user's own address is never resolved.
    """
    address = (address or "").strip().lower()
    if not address or is_own_address(user, address):
        return None
    if (contact := find_contact(user, address)) is not None:
        return contact
    return (
        models.ContactGroup.objects.visible_to(user)
        .filter(email__iexact=address)
        .order_by(_personal_first(user), "created_at", "pk")
        .first()
    )


def _visible(user, items):
    """The contacts and groups *items* name by uid that *user* can see.

    Each uid is looked up in both tables: it matches in at most one.
    """
    ids = {item["id"] for item in items}
    contacts = (
        models.Contact.objects.visible_to(user)
        .filter(pk__in=ids, deleted_at__isnull=True)
        .select_related("overlay_of")
    )
    groups = models.ContactGroup.objects.visible_to(user).filter(pk__in=ids)
    return list(contacts), list(groups)


def _target(row):
    """The key the score of *row* is stored under: a contact's canonical row."""
    if isinstance(row, models.ContactGroup):
        return (GROUP, row.pk)
    return (CONTACT, row.overlay_of_id or row.pk)


def _item_key(item):
    if item["type"] == EMAIL:
        return (EMAIL, item["value"].strip().lower())
    return (UID, str(item["id"]))


def record(user, kind, items):
    """Record one event of *kind* between *user* and *items*.

    Items the user can't see, and addresses matching nothing they know, are
    ignored (but still count as people in the event); so is the user's own
    address. A row named twice, by id or by address, counts once, on the
    row its overlay covers. Returns how many rows were bumped.
    """
    unique = {_item_key(item): item for item in items}
    contacts, groups = _visible(
        user, [item for item in unique.values() if item["type"] != EMAIL]
    )
    targets = {_target(row): row for row in [*contacts, *groups]}
    people = len(unique)
    for key, item in unique.items():
        if key[0] != EMAIL:
            continue
        if is_own_address(user, key[1]):
            people -= 1  # the user themself (a copy to self)
            continue
        if (row := resolve_email(user, item["value"])) is not None:
            if _target(row) in targets:
                people -= 1  # also named by id
            targets[_target(row)] = row

    amount = weight(kind, people=max(people, 1))
    # One timestamp for the whole event: the people in it get the same
    # rank, so the name orders them, not microseconds apart.
    now = timezone.now()
    for (target_type, _pk), row in targets.items():
        models.Interaction.record(user, **{target_type: row}, weight=amount, now=now)
    return len(targets)
