"""POST /external_api/v1.0/interactions/ and the decayed counter behind it."""

import datetime
import uuid

from django.utils import timezone

import pytest

from core import factories, models

from .conftest import contact, group

pytestmark = pytest.mark.django_db

URL = "/external_api/v1.0/interactions/"


def _post(api, *items):
    """Post interactions with the cards of *items*, (kind, row id) pairs.

    The kind is only there to read the tests: a uid names a contact or a
    group alike.
    """
    return api.post(
        URL,
        {"items": [{"uid": f"urn:uuid:{row_id}"} for _kind, row_id in items]},
        format="json",
    )


def test_picks_are_counted(api, user):
    """Picks are counted."""
    felix = contact(user, full_name="Félix")
    team = group(org=user.organization, name="Team")

    response = _post(api, ("contact", felix.pk), ("group", team.pk))

    assert response.status_code == 204
    rows = models.Interaction.objects.filter(user=user)
    assert {(row.contact_id, row.group_id, row.count) for row in rows} == {
        (felix.pk, None, 1),
        (None, team.pk, 1),
    }


def test_a_second_pick_adds_to_the_same_row(api, user):
    """A second pick adds to the same row."""
    felix = contact(user, full_name="Félix")

    _post(api, ("contact", felix.pk))
    _post(api, ("contact", felix.pk))

    interaction = models.Interaction.objects.get(user=user)
    assert interaction.count == 2
    assert interaction.score == pytest.approx(2, abs=1e-3)


def test_a_pick_of_an_overlay_is_stored_on_the_row_it_covers(api, user):
    """A pick of an overlay is stored on the row it covers."""
    synced = contact(
        org=user.organization, full_name="Claire", source="ldap", external_id="1"
    )
    overlay = models.Contact(
        owner=user, organization=user.organization, overlay_of=synced
    )
    overlay.save()

    _post(api, ("contact", overlay.pk))

    assert models.Interaction.objects.get(user=user).contact_id == synced.pk


def test_rows_the_user_cannot_see_are_ignored_silently(api, user):
    """Rows the user cannot see are ignored silently."""
    colleague = factories.UserFactory(organization=user.organization)
    hidden = contact(colleague, full_name="Not mine")
    elsewhere = group(org=factories.OrganizationFactory(), name="Other org")

    response = _post(
        api,
        ("contact", hidden.pk),
        ("group", elsewhere.pk),
        ("contact", uuid.uuid4()),
    )

    assert response.status_code == 204
    assert not models.Interaction.objects.exists()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"items": []},
        {"items": [{}]},
        {"items": [{"uid": "urn:uuid:not-a-uuid"}]},
        {"items": [{"uid": f"urn:uuid:{uuid.uuid4()}", "email": "a@b.org"}]},
        {"items": [{"uid": f"urn:uuid:{uuid.uuid4()}"}] * 51},
    ],
)
def test_bad_bodies_are_refused(api, body):
    """Bad bodies are refused."""
    assert api.post(URL, body, format="json").status_code == 400


def test_each_users_picks_are_their_own(api, user):
    """Each users picks are their own."""
    felix = contact(org=user.organization, full_name="Félix")
    colleague = factories.UserFactory(organization=user.organization)
    models.Interaction.record(colleague, contact=felix)

    _post(api, ("contact", felix.pk))

    assert models.Interaction.objects.get(user=user).count == 1
    assert models.Interaction.objects.get(user=colleague).count == 1


# The decayed counter


def test_a_pick_halves_every_half_life(user, settings):
    """A pick halves every half life."""
    settings.INTERACTION_HALF_LIFE_DAYS = 30
    felix = contact(user, full_name="Félix")
    start = timezone.now() - datetime.timedelta(days=60)

    models.Interaction.record(user, contact=felix, now=start)
    models.Interaction.record(
        user, contact=felix, now=start + datetime.timedelta(days=30)
    )

    interaction = models.Interaction.objects.get(user=user)
    # 1 picked 30 days before the second: 0.5 + 1.
    assert interaction.score == pytest.approx(1.5)
    assert interaction.count == 2
    # 30 more days to now: 1.5 / 2.
    ranked = models.Interaction.objects.with_rank().get(pk=interaction.pk)
    assert ranked.rank == pytest.approx(0.75, rel=1e-3)


def test_a_row_points_at_one_target_only(user):
    """A row points at one target only."""
    felix = contact(user, full_name="Félix")
    team = group(user, name="Team")

    with pytest.raises(Exception):  # noqa: B017  # IntegrityError or ValidationError
        models.Interaction.objects.create(
            user=user,
            contact=felix,
            group=team,
            last_interaction_at=timezone.now(),
        )


# Interaction rows follow the canonical row


@pytest.mark.parametrize("notes", ["private", ""])
def test_sharing_a_card_with_the_organization_moves_its_interaction(user, notes):
    """Sharing a card with the organization moves its Interaction row.

    With notes the card stays as the owner's overlay; without, it is
    deleted. Either way the row moves to the shared card, with its count.
    """
    zelda = contact(user, full_name="Zelda", notes=notes)
    models.Interaction.record(user, contact=zelda)
    models.Interaction.record(user, contact=zelda)

    shared = zelda.share_with_organization(by=user)

    interaction = models.Interaction.objects.get(user=user)
    assert interaction.contact_id == shared.pk
    assert interaction.count == 2


def test_deleting_an_organization_card_moves_interactions_to_overlays(user):
    """Deleting an organization card hands each overlay owner their row."""
    colleague = factories.UserFactory(organization=user.organization)
    card = contact(org=user.organization, full_name="Claire")
    overlay = models.Contact(
        owner=user, organization=user.organization, overlay_of=card
    )
    overlay.save()
    models.Interaction.record(user, contact=overlay)
    models.Interaction.record(colleague, contact=card)

    card.delete()

    overlay.refresh_from_db()
    assert overlay.overlay_of_id is None
    assert models.Interaction.objects.get(user=user).contact_id == overlay.pk
    # The colleague had no overlay: their row goes with the card.
    assert not models.Interaction.objects.filter(user=colleague).exists()


def test_one_share_records_its_picks_at_the_same_time(api, user):
    """One share records its picks at the same time, so they rank equal."""
    zoe = contact(user, full_name="Zoé")
    adam = contact(user, full_name="Adam")

    _post(api, ("contact", zoe.pk), ("contact", adam.pk))

    rows = models.Interaction.objects.filter(user=user)
    assert len({row.last_interaction_at for row in rows}) == 1
    assert len({row.score for row in rows}) == 1
