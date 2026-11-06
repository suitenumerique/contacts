"""Interactions by email address: for apps that only know addresses."""

import pytest

from core import factories, models
from core.services.interactions import resolve_email

from .conftest import contact, group

pytestmark = pytest.mark.django_db

URL = "/external_api/v1.0/interactions/"


def _send(client, *items, kind="email_sent"):
    return client.post(URL, {"kind": kind, "items": list(items)}, format="json")


def _email(address):
    return {"type": "email", "value": address}


def test_an_address_counts_for_the_contact_behind_it(messages_api, user):
    """An address counts for the contact behind it."""
    bob = contact(user, full_name="Bob", emails=("bob.martin@example.org",))

    assert _send(messages_api, _email("Bob.Martin@Example.org")).status_code == 204

    assert models.Interaction.objects.get(user=user).contact_id == bob.pk


def test_an_unknown_address_is_ignored(messages_api):
    """An unknown address is ignored: nothing is created."""
    assert _send(messages_api, _email("stranger@example.org")).status_code == 204

    assert not models.Interaction.objects.exists()
    assert not models.Contact.objects.exists()


def test_the_users_own_address_is_ignored(messages_api, user):
    """The user's own address is ignored, even on a card of theirs."""
    contact(user, full_name="Me", emails=(user.email,))

    _send(messages_api, _email(user.email.upper()))

    assert not models.Interaction.objects.exists()


def test_the_users_own_card_comes_before_the_organizations(user):
    """The user's own card comes before the organization's."""
    contact(org=user.organization, full_name="Bob (org)", emails=("bob@example.org",))
    mine = contact(user, full_name="Bob (mine)", emails=("bob@example.org",))

    assert resolve_email(user, "bob@example.org") == mine


def test_an_organization_card_is_found_too(user):
    """An organization card is found when the user has none."""
    shared = contact(
        org=user.organization, full_name="Bob", emails=("bob@example.org",)
    )

    assert resolve_email(user, "bob@example.org") == shared


def test_a_card_the_user_cannot_see_is_not_found(user):
    """A colleague's card or another organization's is not found."""
    colleague = factories.UserFactory(organization=user.organization)
    contact(colleague, full_name="Bob", emails=("bob@example.org",))
    contact(
        org=factories.OrganizationFactory(),
        full_name="Bob",
        emails=("bob@example.org",),
    )

    assert resolve_email(user, "bob@example.org") is None


def test_an_address_inside_another_one_is_not_a_match(user):
    """Matching is on the exact address, not a prefix of a longer one."""
    contact(user, full_name="Bob", emails=("bob@example.org.uk",))

    assert resolve_email(user, "bob@example.org") is None


def test_an_overlay_counts_for_the_card_it_covers(messages_api, user):
    """An address on an overlaid card counts for the card it covers."""
    synced = contact(
        org=user.organization,
        full_name="Claire",
        emails=("claire@example.org",),
        source="ldap",
        external_id="1",
    )
    overlay = models.Contact(
        owner=user, organization=user.organization, overlay_of=synced
    )
    overlay.save()

    _send(messages_api, _email("claire@example.org"))

    assert models.Interaction.objects.get(user=user).contact_id == synced.pk


def test_a_shared_mailbox_counts_for_its_group(messages_api, user):
    """An address that is a group's (a shared mailbox) counts for the group."""
    team = group(org=user.organization, name="Accueil", email="accueil@example.org")

    _send(messages_api, _email("accueil@example.org"))

    assert models.Interaction.objects.get(user=user).group_id == team.pk


def test_a_contact_comes_before_a_group_with_the_same_address(user):
    """A contact comes before a group with the same address."""
    group(user, name="Bob's group", email="bob@example.org")
    bob = contact(user, full_name="Bob", emails=("bob@example.org",))

    assert resolve_email(user, "bob@example.org") == bob


def test_a_person_named_by_id_and_address_counts_once(messages_api, user):
    """A person named by id and by address counts once."""
    bob = contact(user, full_name="Bob", emails=("bob@example.org",))

    _send(
        messages_api, {"type": "contact", "id": str(bob.pk)}, _email("bob@example.org")
    )

    interaction = models.Interaction.objects.get(user=user)
    assert interaction.count == 1
    assert interaction.score == pytest.approx(1, abs=1e-3)


def test_unknown_addresses_still_count_as_people(messages_api, user, settings):
    """Addresses matching nothing still make the event a mass one."""
    settings.INTERACTION_MASS_THRESHOLD = 10
    bob = contact(user, full_name="Bob", emails=("bob@example.org",))
    strangers = [_email(f"s{i}@example.org") for i in range(19)]

    _send(messages_api, _email("bob@example.org"), *strangers)

    assert models.Interaction.objects.get(
        user=user, contact=bob
    ).score == pytest.approx(0.5, abs=1e-3)


@pytest.mark.parametrize(
    "item",
    [
        {"type": "email"},
        {"type": "email", "value": "not-an-address"},
        {"type": "contact"},
        {"type": "contact", "value": "bob@example.org"},
    ],
)
def test_an_item_needs_what_its_type_requires(messages_api, item):
    """An email item needs an address, a contact or group item an id."""
    assert _send(messages_api, item).status_code == 400
