"""Tests for the CardDAV change journal itself.

The sync-collection behaviour it enables is covered in
``test_carddav.py``; this file covers the journal's own invariants.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name

import threading
import time
from datetime import timedelta

from django.core.management import call_command
from django.db import connection
from django.utils import timezone

import pytest

from core import factories, models

from carddav import storage
from carddav.models import CardName, ContactChange, PurgeMark, SyncCounter
from carddav.signals import _next_seq, record_change

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return factories.UserFactory(email="user@example.org")


def journal(contact):
    return ContactChange.objects.get(contact_id=contact.pk)


# --- numbering -------------------------------------------------------


def test_numbers_are_allocated_in_order_per_organization(user):
    other = factories.UserFactory()
    first = _next_seq(user.organization_id)
    second = _next_seq(user.organization_id)
    assert second == first + 1
    # A second organization counts on its own, so unrelated writes never
    # queue behind each other.
    assert _next_seq(other.organization_id) == 1


def test_every_write_advances_the_contact_number(user):
    contact = factories.ContactFactory(owner=user)
    created = journal(contact).seq

    contact.full_name = "Renamed"
    contact.save()
    edited = journal(contact).seq
    assert edited > created

    contact.deleted_at = timezone.now()
    contact.save()
    assert journal(contact).seq > edited


def test_one_row_per_contact_however_often_it_changes(user):
    contact = factories.ContactFactory(owner=user)
    for index in range(5):
        contact.full_name = f"Name {index}"
        contact.save()
    assert ContactChange.objects.filter(contact_id=contact.pk).count() == 1


def test_journal_copies_the_fields_a_tombstone_will_need(user):
    org_row = factories.ContactFactory(owner=None, organization=user.organization)
    synced = factories.ContactFactory(
        owner=user, source="directory", external_id="ext-1"
    )
    assert journal(org_row).owner_id is None
    assert journal(org_row).from_source is False
    assert journal(synced).owner_id == user.pk
    assert journal(synced).from_source is True


@pytest.mark.django_db(transaction=True)
def test_a_number_is_not_handed_out_before_the_previous_one_lands(monkeypatch):
    """The invariant the whole design rests on.

    If a writer could take a number while a lower one had not landed yet,
    a client could pick up the higher one as its token and never be told
    about the row underneath it, the miss a timestamp-based token has.
    What prevents it is that the number and the row it stamps are written
    in one transaction: the counter row stays locked until both are in, so
    a second writer waits there rather than racing ahead.

    So the block is staged *between* those two writes, which is the only
    window where the difference shows.
    """
    user = factories.UserFactory()
    first = factories.ContactFactory(owner=user)
    second = factories.ContactFactory(owner=user)

    numbered = threading.Event()
    release = threading.Event()
    results = {}
    original = ContactChange.objects.bulk_create

    def pause_after_numbering(objs, **kwargs):
        stamping_first = any(row.contact_id == first.pk for row in objs)
        if stamping_first and not numbered.is_set():
            numbered.set()
            release.wait(10)
        return original(objs, **kwargs)

    monkeypatch.setattr(ContactChange.objects, "bulk_create", pause_after_numbering)

    def hold():
        try:
            record_change(first)
        finally:
            connection.close()

    def follow():
        try:
            numbered.wait(10)
            record_change(second)
            results["followed"] = journal(second).seq
        finally:
            connection.close()

    holder = threading.Thread(target=hold)
    follower = threading.Thread(target=follow)
    holder.start()
    follower.start()

    numbered.wait(10)
    time.sleep(0.3)
    # The follower is parked on the counter row, not merely slow.
    assert "followed" not in results

    release.set()
    holder.join(10)
    follower.join(10)

    assert results["followed"] == journal(first).seq + 1

    models.Contact.objects.all().delete()
    models.User.objects.all().delete()


# --- deletions -------------------------------------------------------


def test_a_hard_delete_leaves_a_tombstone(user):
    contact = factories.ContactFactory(owner=user)
    contact_id = contact.pk
    contact.delete()

    assert not models.Contact.objects.filter(pk=contact_id).exists()
    assert ContactChange.objects.filter(contact_id=contact_id).exists()


def test_deleting_an_owner_does_not_journal_the_collateral(user):
    """A cascade has no address book left to sync, and journaling one
    would point a row at a parent the same transaction is removing."""
    doomed = factories.UserFactory(organization=user.organization)
    contact = factories.ContactFactory(owner=doomed)
    contact_id = contact.pk

    doomed.delete()

    assert not models.Contact.objects.filter(pk=contact_id).exists()
    assert not ContactChange.objects.filter(contact_id=contact_id).exists()


def test_deleting_an_organization_does_not_journal_the_collateral(user):
    organization = user.organization
    org_row = factories.ContactFactory(owner=None, organization=organization)
    contact_id = org_row.pk
    ContactChange.objects.all().delete()

    user.delete()  # User.organization is PROTECTed, so it has to go first
    organization.delete()

    assert not ContactChange.objects.filter(contact_id=contact_id).exists()
    assert not SyncCounter.objects.filter(organization_id=organization.pk).exists()


def test_purging_the_corbeille_leaves_the_tombstone_behind(user):
    """The purge happens long after the trashing, but a client that never
    heard about either still has to be told the card is gone."""
    contact = factories.ContactFactory(owner=user)
    contact_id = contact.pk
    contact.deleted_at = timezone.now() - timedelta(days=400)
    contact.save()

    call_command("purge_trashed_contacts")

    assert not models.Contact.objects.filter(pk=contact_id).exists()
    assert ContactChange.objects.filter(contact_id=contact_id).exists()


# --- pruning ---------------------------------------------------------


def test_prune_drops_only_tombstones_past_the_resync_window(user):
    live = factories.ContactFactory(owner=user)
    gone = factories.ContactFactory(owner=user)
    gone_id = gone.pk
    gone.delete()
    recent = factories.ContactFactory(owner=user)
    recent_id = recent.pk
    recent.delete()

    old = timezone.now() - timedelta(days=400)
    ContactChange.objects.filter(contact_id__in=[live.pk, gone_id]).update(
        updated_at=old
    )

    call_command("purge_carddav_changes")

    # Past the window and the contact is gone: nothing may still ask.
    assert not ContactChange.objects.filter(contact_id=gone_id).exists()
    # Old but the contact still exists: it is this card's only marker.
    assert ContactChange.objects.filter(contact_id=live.pk).exists()
    # Gone but still inside the window: a live token may still ask.
    assert ContactChange.objects.filter(contact_id=recent_id).exists()


def test_a_pruned_tombstone_cannot_drag_the_book_number_backwards(user):
    """Pruning the book's newest row must not lower its high-water mark,
    or a client holding that number would be sent backwards."""
    factories.ContactFactory(owner=user)
    gone = factories.ContactFactory(owner=user)
    gone_id = gone.pk  # Model.delete() clears the instance's pk
    gone.delete()
    newest = storage.book_sync_seq(user, storage.PERSONAL)
    assert journal_seq(gone_id) == newest

    ContactChange.objects.filter(contact_id=gone_id).update(
        updated_at=timezone.now() - timedelta(days=400)
    )
    call_command("purge_carddav_changes")
    assert not ContactChange.objects.filter(contact_id=gone_id).exists()
    assert storage.book_sync_seq(user, storage.PERSONAL) == newest
    assert storage.purged_seq(user, storage.PERSONAL) == newest


def test_purge_marks_only_the_books_it_touched(user):
    colleague = factories.UserFactory(organization=user.organization)
    gone = factories.ContactFactory(owner=colleague)
    gone_id = gone.pk
    gone.delete()
    ContactChange.objects.filter(contact_id=gone_id).update(
        updated_at=timezone.now() - timedelta(days=400)
    )
    call_command("purge_carddav_changes")

    assert storage.purged_seq(user, storage.PERSONAL) == 0
    assert storage.purged_seq(user, storage.DIRECTORY) == 0
    assert storage.purged_seq(colleague, storage.PERSONAL) > 0


def test_purge_keeps_the_highest_mark(user):
    """A later purge of older numbers must not lower a mark."""
    marks = []
    for age in (400, 500):
        gone = factories.ContactFactory(owner=user)
        gone_id = gone.pk
        gone.delete()
        ContactChange.objects.filter(contact_id=gone_id).update(
            updated_at=timezone.now() - timedelta(days=age)
        )
        marks.append(journal_seq(gone_id))
    newest = factories.ContactFactory(owner=user)
    newest_id = newest.pk
    newest.delete()
    call_command("purge_carddav_changes")
    assert PurgeMark.objects.get(owner=user).seq == max(marks)

    ContactChange.objects.filter(contact_id=newest_id).update(
        updated_at=timezone.now() - timedelta(days=400)
    )
    call_command("purge_carddav_changes")
    assert PurgeMark.objects.get(owner=user).seq > max(marks)


def test_purge_drops_card_names_with_their_tombstone(user):
    contact = factories.ContactFactory(owner=user)
    contact_id = contact.pk
    CardName.objects.create(owner=user, contact_id=contact_id, name="Phone.vcf")
    contact.delete()

    call_command("purge_carddav_changes")
    assert CardName.objects.filter(contact_id=contact_id).exists()  # still reported

    ContactChange.objects.filter(contact_id=contact_id).update(
        updated_at=timezone.now() - timedelta(days=400)
    )
    call_command("purge_carddav_changes")
    assert not CardName.objects.filter(contact_id=contact_id).exists()


# --- tokens ----------------------------------------------------------


def journal_seq(contact_id):
    return ContactChange.objects.get(contact_id=contact_id).seq


def test_token_round_trips(user):
    contact = factories.ContactFactory(owner=user)
    seq = journal_seq(contact.pk)
    token = storage.make_sync_token(user, seq)
    assert token == f"urn:contacts:sync:{user.organization_id}:{seq}"
    assert storage.parse_sync_token(user, token) == seq


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        ":",
        ":x",
        ":-1",
        ":1.5",
        f":{2**70}",
        ":2",  # ahead of the organization's counter
    ],
)
def test_unusable_tokens_parse_to_nothing(user, suffix):
    factories.ContactFactory(owner=user)  # counter at 1
    token = f"urn:contacts:sync:{user.organization_id}{suffix}"
    assert storage.parse_sync_token(user, token) is None


@pytest.mark.parametrize("token", ["", "garbage", "urn:contacts:sync:1:1"])
def test_foreign_tokens_parse_to_nothing(user, token):
    assert storage.parse_sync_token(user, token) is None
