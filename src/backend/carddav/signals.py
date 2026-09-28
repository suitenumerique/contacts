"""Writing the change journal, on every write that changes a card.

Attached to ``core.models`` from the CardDAV app rather than from
``core``: the contacts domain does not know this journal exists, and
removing the package removes the receivers with it.

A contact's card shows its owner's labels (CATEGORIES), and a group's
card lists its members' UIDs, so label and membership writes change cards
too.

Nothing here decides *visibility*. It only records that a card's rendered
content changed; which address book that matters to, and whether the
asking user may see it at all, is decided at read time in
:mod:`carddav.storage`.
"""

from django.db import connection, transaction
from django.db.models import Q
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete
from django.dispatch import receiver

from core.models import Contact, ContactGroup, ContactGroupMembership, ContactLabel

from carddav.models import ContactChange


def _next_seq(organization_id):
    """Allocate the next change number for *organization_id*.

    Upsert-and-return in one statement, run inside the caller's
    transaction. The row lock it takes is held until that transaction
    commits, so a concurrent writer blocks here rather than racing ahead:
    numbers are handed out in commit order. Without that, a number could
    become visible while a lower one was still in flight, and a client
    that took the higher one as its token would never be told about the
    row underneath it.

    Table name is spelled out because this is raw SQL; it is pinned by
    ``SyncCounter.Meta.db_table``.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO carddav_sync_counter (organization_id, seq) VALUES (%s, 1) "
            "ON CONFLICT (organization_id) "
            "DO UPDATE SET seq = carddav_sync_counter.seq + 1 "
            "RETURNING seq",
            [organization_id],
        )
        return cursor.fetchone()[0]


def _group_targets(contact_ids):
    """The groups holding any of *contact_ids*, as journal targets."""
    return [
        (pk, owner_id, bool(source), True)
        for pk, owner_id, source in ContactGroup.objects.filter(
            memberships__contact_id__in=contact_ids
        )
        .distinct()
        .values_list("pk", "owner_id", "source")
    ]


def _targets(contact, removed):
    """Cards whose rendered content changed, as ``(id, owner_id, synced, group)``.

    An overlay presents fields read live from its parent, so the two move
    together: editing the parent changes the overlay's card, and creating
    or dropping an overlay changes whether the parent shows at all.
    Recording only the row that was written would leave the other one
    stale in a client that syncs by delta.

    The groups holding the contact move with it too: their cards name the
    member by UID, and only while it shows in their book.
    """
    targets = [(contact.pk, contact.owner_id, bool(contact.source), False)]

    clause = Q(pk=contact.overlay_of_id) if contact.overlay_of_id else Q()
    if not removed:
        # Overlays of a deleted row are cascade-deleted and fire their own
        # signal, so they only need looking up while the row still lives.
        clause = clause | Q(overlay_of_id=contact.pk)
    if clause:
        targets += [
            (pk, owner_id, bool(source), False)
            for pk, owner_id, source in Contact.objects.filter(clause)
            .exclude(pk=contact.pk)
            .values_list("pk", "owner_id", "source")
        ]
    canonical = (
        [contact.pk, contact.overlay_of_id] if contact.overlay_of_id else [contact.pk]
    )
    return targets + _group_targets(canonical)


def _record(organization_id, targets):
    """Journal *targets* of one organization under one new number."""
    if not targets:
        return
    with transaction.atomic():
        seq = _next_seq(organization_id)
        ContactChange.objects.bulk_create(
            [
                ContactChange(
                    organization_id=organization_id,
                    contact_id=pk,
                    is_group=is_group,
                    seq=seq,
                    owner_id=owner_id,
                    from_source=synced,
                )
                for pk, owner_id, synced, is_group in targets
            ],
            update_conflicts=True,
            update_fields=["seq", "is_group", "owner", "from_source", "updated_at"],
            unique_fields=["organization", "contact_id"],
        )


def record_change(contact, *, removed=False):
    """Journal *contact* (and whatever its change drags along) as dirty."""
    _record(contact.organization_id, _targets(contact, removed))


def record_contacts(contact_ids):
    """Journal the cards of *contact_ids* alone (a label change)."""
    rows = Contact.objects.filter(pk__in=list(contact_ids)).values_list(
        "pk", "organization_id", "owner_id", "source"
    )
    by_organization = {}
    for pk, organization_id, owner_id, source in rows:
        by_organization.setdefault(organization_id, []).append(
            (pk, owner_id, bool(source), False)
        )
    for organization_id, targets in by_organization.items():
        _record(organization_id, targets)


def record_group(group):
    """Journal *group*'s card as dirty (or gone, once deleted)."""
    _record(
        group.organization_id, [(group.pk, group.owner_id, bool(group.source), True)]
    )


def _origin_model(origin):
    return getattr(origin, "model", None) or type(origin)


@receiver(post_save, sender=Contact, dispatch_uid="carddav_contact_saved")
def contact_saved(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """Record a create, an edit, a trashing or a restore: all are writes."""
    record_change(instance)


@receiver(post_delete, sender=Contact, dispatch_uid="carddav_contact_deleted")
def contact_deleted(sender, instance, origin=None, **kwargs):  # pylint: disable=unused-argument
    """Record a hard delete as a tombstone, so the removal is reportable.

    Contacts also disappear as collateral when their owner or their
    organization is deleted. There is no address book left to sync in that
    case, and journaling would insert rows pointing at a parent the same
    transaction is about to remove, so those cascades are skipped:
    ``origin`` names what the deletion was actually asked for.
    """
    if _origin_model(origin) is not Contact:
        return
    record_change(instance, removed=True)


# --- labels ------------------------------------------------------------


@receiver(
    m2m_changed, sender=Contact.labels.through, dispatch_uid="carddav_labels_changed"
)
def labels_changed(sender, instance, action, reverse, pk_set, **kwargs):  # pylint: disable=unused-argument,too-many-arguments
    """A label put on or taken off a contact changes its CATEGORIES.

    From either side: ``contact.labels`` (reverse=False) or
    ``label.contacts``. A clear sends no ids, so they are read before it.
    """
    if action == "pre_clear":
        instance.carddav_cleared = (
            list(instance.contacts.values_list("pk", flat=True))
            if reverse
            else [instance.pk]
        )
        return
    if action == "post_clear":
        record_contacts(getattr(instance, "carddav_cleared", []))
    elif action in ("post_add", "post_remove") and pk_set:
        record_contacts(pk_set if reverse else [instance.pk])


@receiver(post_save, sender=ContactLabel, dispatch_uid="carddav_label_saved")
def label_saved(sender, instance, created, **kwargs):  # pylint: disable=unused-argument
    """A renamed label changes the card of every contact wearing it."""
    if not created:
        record_contacts(instance.contacts.values_list("pk", flat=True))


@receiver(pre_delete, sender=ContactLabel, dispatch_uid="carddav_label_deleted")
def label_deleted(sender, instance, origin=None, **kwargs):  # pylint: disable=unused-argument
    """A deleted label leaves its contacts' cards, without an m2m signal.

    Read before the delete, while the links still exist. Skipped when the
    label goes with its owner, whose contacts go too.
    """
    if _origin_model(origin) is ContactLabel:
        record_contacts(instance.contacts.values_list("pk", flat=True))


# --- groups ------------------------------------------------------------


@receiver(post_save, sender=ContactGroup, dispatch_uid="carddav_group_saved")
def group_saved(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """Record a group's creation or edit."""
    record_group(instance)


@receiver(post_delete, sender=ContactGroup, dispatch_uid="carddav_group_deleted")
def group_deleted(sender, instance, origin=None, **kwargs):  # pylint: disable=unused-argument
    """Record a deleted group as a tombstone (not when its owner goes)."""
    if _origin_model(origin) is ContactGroup:
        record_group(instance)


@receiver(post_save, sender=ContactGroupMembership, dispatch_uid="carddav_member_added")
def membership_saved(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """A new member changes the group's card."""
    record_group(instance.group)


@receiver(
    post_delete, sender=ContactGroupMembership, dispatch_uid="carddav_member_removed"
)
def membership_deleted(sender, instance, origin=None, **kwargs):  # pylint: disable=unused-argument
    """A removed member changes the group's card.

    Also when the member contact was deleted. Not when the group itself,
    its owner or its organization is going: nothing is left to show.
    """
    if _origin_model(origin) in (ContactGroupMembership, Contact):
        group = ContactGroup.objects.filter(pk=instance.group_id).first()
        if group is not None:
            record_group(group)
