"""A contact write and the CardDAV change journal commit together or not at all."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core import factories, models

pytestmark = pytest.mark.django_db

CONTACTS_URL = "/api/v1.0/contacts/"


class JournalDownError(Exception):
    """Stands for any failure while writing the change journal."""


@pytest.fixture
def user():
    return factories.UserFactory()


@pytest.fixture
def client(user):
    client = APIClient()
    client.force_login(user)
    return client


@pytest.fixture
def break_journal(monkeypatch):
    """Call it once the test data exists: from then on, journaling fails."""

    def fail(*_args, **_kwargs):
        raise JournalDownError()

    return lambda: monkeypatch.setattr("carddav.signals.record_change", fail)


def test_create_is_rolled_back(client, user, break_journal):
    break_journal()
    with pytest.raises(JournalDownError):
        client.post(
            CONTACTS_URL,
            {
                "given_name": "Jeanne",
                "family_name": "Martin",
                "emails": [{"value": "jeanne@example.org"}],
            },
            format="json",
        )
    assert not models.Contact.objects.filter(owner=user).exists()


def test_update_is_rolled_back(client, user, break_journal):
    contact = factories.ContactFactory(owner=user, full_name="Before")
    break_journal()

    with pytest.raises(JournalDownError):
        client.patch(
            f"{CONTACTS_URL}{contact.id}/", {"full_name": "After"}, format="json"
        )

    contact.refresh_from_db()
    assert contact.full_name == "Before"


def test_trashing_is_rolled_back(client, user, break_journal):
    contact = factories.ContactFactory(owner=user)
    break_journal()

    with pytest.raises(JournalDownError):
        client.delete(f"{CONTACTS_URL}{contact.id}/")

    contact.refresh_from_db()
    assert contact.deleted_at is None


def test_restore_is_rolled_back(client, user, break_journal):
    contact = factories.ContactFactory(owner=user, deleted_at=timezone.now())
    break_journal()

    with pytest.raises(JournalDownError):
        client.post(f"{CONTACTS_URL}{contact.id}/restore/")

    contact.refresh_from_db()
    assert contact.deleted_at is not None
