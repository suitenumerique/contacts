"""Interactions other Suite apps report: shares, emails, invitations...

Each kind weighs what ``INTERACTION_KINDS`` says. One call is one event (a
share, a message, an invitation) and its items are the people or groups in
it: past ``INTERACTION_MASS_THRESHOLD`` of them, each one weighs less, since
a message to 200 people says little about any of them.
"""

from django.conf import settings
from django.utils import timezone

from core import models

CONTACT = "contact"
GROUP = "group"
ITEM_TYPES = (CONTACT, GROUP)


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


def _visible(user, items):
    """The contacts and groups *items* name that *user* can see."""
    wanted = {CONTACT: set(), GROUP: set()}
    for item in items:
        wanted[item["type"]].add(item["id"])
    contacts = (
        models.Contact.objects.visible_to(user)
        .filter(pk__in=wanted[CONTACT], deleted_at__isnull=True)
        .select_related("overlay_of")
    )
    groups = models.ContactGroup.objects.visible_to(user).filter(pk__in=wanted[GROUP])
    return list(contacts), list(groups)


def record(user, kind, items):
    """Record one event of *kind* between *user* and *items*.

    Items the user can't see are ignored, and so is a row named twice. A
    contact counts once with the row its overlay covers. Returns how many
    rows were bumped.
    """
    unique = {(item["type"], str(item["id"])): item for item in items}
    contacts, groups = _visible(user, unique.values())
    # Each person once, on the row the score is stored on.
    targets = {
        ("contact", contact.overlay_of_id or contact.pk): contact
        for contact in contacts
    }
    targets.update({("group", group.pk): group for group in groups})

    amount = weight(kind, people=len(unique))
    # One timestamp for the whole event: the people in it get the same
    # rank, so the name orders them, not microseconds apart.
    now = timezone.now()
    for (target_type, _pk), row in targets.items():
        models.Interaction.record(user, **{target_type: row}, weight=amount, now=now)
    return len(targets)
