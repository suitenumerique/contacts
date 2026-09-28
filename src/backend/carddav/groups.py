"""Writing group cards: a user's own groups and their members.

A group card names its members by UID (see
``core.services.vcf_service.serialize_group``). Only cards of the personal
book can be named: that is where clients send group cards.
"""

from core.models import ContactGroup, ContactGroupMembership
from core.services.vcf_service import PENDING_MEMBERS, card_uid, ref_uids

from carddav import storage
from carddav.signals import record_group


def set_members(user, group, added, removed):
    """Add and remove the members the MEMBER refs *added* and *removed* name.

    A ref naming no card of the personal book yet is kept as a pending
    member line, served back as sent until its card arrives (see
    ``claim_pending_members``).
    """
    if not added and not removed:
        return
    resolved = storage.resolve_member_refs(user, [*added, *removed])
    pending = list(group.vcard_extra.get(PENDING_MEMBERS, []))
    new_ids = {resolved[ref] for ref in added if ref in resolved}
    pending += [f"{PENDING_MEMBERS}:{ref}" for ref in added if ref not in resolved]
    gone = [ref for ref in removed if ref in resolved]
    pending = [
        text
        for text in pending
        if not any(_line_uids(text) & ref_uids(ref) for ref in removed)
    ]
    ContactGroupMembership.objects.bulk_create(
        [ContactGroupMembership(group=group, contact_id=pk) for pk in new_ids],
        ignore_conflicts=True,
    )
    ContactGroupMembership.objects.filter(
        group=group, contact_id__in=[resolved[ref] for ref in gone]
    ).delete()
    if pending != group.vcard_extra.get(PENDING_MEMBERS, []):
        set_pending(group, pending)
        group.save(update_fields=["vcard_extra", "updated_at"])
    elif new_ids:
        # bulk_create sends no signal to journal the change.
        record_group(group)


def claim_pending_members(user, contact):
    """Add *contact* to the groups whose card named it before it existed.

    A client may send a group card before the card of a new member.
    """
    uid = card_uid(contact)
    groups = ContactGroup.objects.filter(
        owner=user, source="", vcard_extra__has_key=PENDING_MEMBERS
    )
    for group in groups:
        lines = group.vcard_extra[PENDING_MEMBERS]
        kept = [text for text in lines if uid not in _line_uids(text)]
        if len(kept) == len(lines):
            continue
        ContactGroupMembership.objects.get_or_create(
            group=group, contact_id=contact.overlay_of_id or contact.pk
        )
        set_pending(group, kept)
        group.save(update_fields=["vcard_extra", "updated_at"])


def set_pending(group, lines):
    """Store *lines* as *group*'s pending members (none: drop the key)."""
    extra = dict(group.vcard_extra or {})
    if lines:
        extra[PENDING_MEMBERS] = lines
    else:
        extra.pop(PENDING_MEMBERS, None)
    group.vcard_extra = extra


def _line_uids(text):
    """The UIDs a stored pending member line may name."""
    return ref_uids(text.partition(":")[2].strip())
