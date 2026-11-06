"""POST /external_api/v1.0/contacts/: "Add as contact" buttons in other apps."""

import pytest

from core import factories, models
from core.enums import Scope

from .conftest import contact

pytestmark = pytest.mark.django_db

URL = "/external_api/v1.0/contacts/"


def _add(api, email, name=None):
    body = {"email": email}
    if name is not None:
        body["name"] = name
    return api.post(URL, body, format="json")


def test_an_unknown_address_becomes_a_personal_contact(api, user):
    """An unknown address becomes a personal contact of the user."""
    response = _add(api, "bob.martin@example.org", "Bob Martin")

    assert response.status_code == 201
    created = models.Contact.objects.get()
    assert (created.owner, created.scope, created.full_name, created.email_values) == (
        user,
        Scope.PERSONAL,
        "Bob Martin",
        ["bob.martin@example.org"],
    )
    assert created.created_by == user
    assert response.json() == {
        "type": "contact",
        "id": str(created.pk),
        "display_name": "Bob Martin",
        "email": "bob.martin@example.org",
        "emails": [{"type": "", "value": "bob.martin@example.org"}],
        "company": None,
        "job_title": None,
        "photo": None,
        "is_org": False,
        "source": "",
    }


def test_without_a_name_the_address_is_shown(api):
    """Without a name, the contact shows its address."""
    response = _add(api, "bob@example.org")

    assert response.status_code == 201
    assert response.json()["display_name"] == "bob@example.org"


def test_a_new_contact_gets_a_one_time_boost(api, user):
    """A new contact gets the added_as_contact weight, once."""
    _add(api, "bob@example.org", "Bob")
    _add(api, "bob@example.org", "Bob")

    interaction = models.Interaction.objects.get(user=user)
    assert interaction.count == 1
    assert interaction.score == pytest.approx(2, abs=1e-3)


def test_adding_twice_returns_the_same_contact(api):
    """Adding twice returns the same contact: a double click is harmless."""
    first = _add(api, "bob@example.org", "Bob")
    second = _add(api, "BOB@example.org", "Bob")

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert models.Contact.objects.count() == 1


def test_a_contact_the_user_already_has_is_returned(api, user):
    """A contact the user already has is returned, not duplicated."""
    bob = contact(user, full_name="Bob", emails=("bob@example.org",))

    response = _add(api, "bob@example.org", "Robert")

    assert response.status_code == 200
    assert response.json()["id"] == str(bob.pk)
    assert response.json()["display_name"] == "Bob"
    assert not models.Interaction.objects.exists()


def test_an_organization_contact_is_returned_too(api, user):
    """An organization contact with that address is returned too."""
    shared = contact(
        org=user.organization, full_name="Bob", emails=("bob@example.org",)
    )

    response = _add(api, "bob@example.org")

    assert response.status_code == 200
    assert response.json()["id"] == str(shared.pk)


def test_a_colleagues_contact_does_not_count(api, user):
    """A colleague's personal contact is invisible: a new one is created."""
    colleague = factories.UserFactory(organization=user.organization)
    contact(colleague, full_name="Bob", emails=("bob@example.org",))

    assert _add(api, "bob@example.org").status_code == 201
    assert models.Contact.objects.filter(owner=user).count() == 1


def test_a_trashed_contact_does_not_count(api, user):
    """A contact in the trash doesn't count: a new one is created."""
    old = contact(user, full_name="Bob", emails=("bob@example.org",))
    old.deleted_at = old.updated_at
    old.save()

    assert _add(api, "bob@example.org").status_code == 201


@pytest.mark.parametrize(
    "body",
    [{}, {"email": "not-an-address"}, {"email": "bob@example.org", "name": "x" * 256}],
)
def test_bad_bodies_are_refused(api, body):
    """Bad bodies are refused."""
    assert api.post(URL, body, format="json").status_code == 400


def test_the_users_own_address_is_refused(api, user):
    """The user's own address is refused."""
    assert _add(api, user.email.upper()).status_code == 400
    assert not models.Contact.objects.exists()
