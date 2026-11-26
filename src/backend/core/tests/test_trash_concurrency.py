"""Tests for a permanent delete and a restore of the same contact at once."""

# pylint: disable=missing-function-docstring

import threading
import time

from django.db import connection
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core import factories, models
from core.api.viewsets import ContactViewSet

CONTACTS_URL = "/api/v1.0/contacts/"


def client_for(user):
    client = APIClient()
    client.force_login(user)
    return client


def in_thread(target):
    """Run ``target`` in a thread with its own database connection."""

    def run():
        try:
            target()
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


@pytest.mark.django_db(transaction=True)
def test_permanent_delete_keeps_a_contact_restored_after_the_read(monkeypatch):
    """A restore commits after the delete reads the contact, before the lock.

    The delete must use the current state of the row, not the row it read.
    """
    user = factories.UserFactory()
    contact = factories.ContactFactory(owner=user, deleted_at=timezone.now())
    url = f"{CONTACTS_URL}{contact.id}/"
    original = ContactViewSet._check_writable  # pylint: disable=protected-access
    results = {}

    def restore_first(view, row):
        original(view, row)
        if view.action != "destroy" or "restored" in results:
            return
        in_thread(
            lambda: results.update(
                restored=client_for(user).post(f"{url}restore/").status_code
            )
        ).join(10)

    monkeypatch.setattr(ContactViewSet, "_check_writable", restore_first)

    response = client_for(user).delete(f"{url}?permanent=true")

    assert results == {"restored": 200}
    assert response.status_code == 400
    contact.refresh_from_db()
    assert contact.deleted_at is None

    models.Contact.objects.all().delete()
    models.User.objects.all().delete()


@pytest.mark.django_db(transaction=True)
def test_restore_waits_for_a_permanent_delete(monkeypatch):
    """A restore that starts during a permanent delete waits, then finds no row."""
    user = factories.UserFactory()
    contact = factories.ContactFactory(owner=user, deleted_at=timezone.now())
    url = f"{CONTACTS_URL}{contact.id}/"
    original = models.Contact.delete
    deleting = threading.Event()
    release = threading.Event()
    results = {}

    def pause_before_delete(row, *args, **kwargs):
        if row.pk == contact.pk:
            deleting.set()
            release.wait(10)
        return original(row, *args, **kwargs)

    monkeypatch.setattr(models.Contact, "delete", pause_before_delete)

    deleter = in_thread(
        lambda: results.update(
            deleted=client_for(user).delete(f"{url}?permanent=true").status_code
        )
    )
    assert deleting.wait(10)
    restorer = in_thread(
        lambda: results.update(
            restored=client_for(user).post(f"{url}restore/").status_code
        )
    )
    time.sleep(0.3)
    # The restore waits for the lock of the delete.
    assert "restored" not in results

    release.set()
    deleter.join(10)
    restorer.join(10)

    assert results == {"deleted": 204, "restored": 404}
    assert not models.Contact.objects.filter(pk=contact.pk).exists()

    models.User.objects.all().delete()


@pytest.mark.django_db(transaction=True)
def test_restore_returns_the_contact_before_a_delete_can_remove_it(monkeypatch):
    """A delete that starts during a restore waits until the restore responds."""
    user = factories.UserFactory()
    contact = factories.ContactFactory(owner=user, deleted_at=timezone.now())
    url = f"{CONTACTS_URL}{contact.id}/"
    original = ContactViewSet.get_queryset
    calls = []
    results = {}

    def delete_after_the_first_read(view):
        if view.action == "restore":
            calls.append(view)
            if len(calls) == 2:
                calls.append(
                    in_thread(
                        lambda: results.update(
                            trashed=client_for(user).delete(url).status_code,
                            deleted=client_for(user)
                            .delete(f"{url}?permanent=true")
                            .status_code,
                        )
                    )
                )
                # The delete waits for the lock of the restore.
                calls[-1].join(1)
        return original(view)

    monkeypatch.setattr(ContactViewSet, "get_queryset", delete_after_the_first_read)

    response = client_for(user).post(f"{url}restore/")
    calls[-1].join(10)

    assert response.status_code == 200
    assert response.json()["deleted_at"] is None
    assert results == {"trashed": 204, "deleted": 204}

    models.User.objects.all().delete()
