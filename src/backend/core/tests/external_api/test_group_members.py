"""GET /external_api/v1.0/groups/{id}/members/: expand a group into people."""

import uuid

from django.utils import timezone

import pytest

from core import factories, models

from .conftest import contact, group

pytestmark = pytest.mark.django_db


def _url(group_id):
    return f"/external_api/v1.0/groups/{group_id}/members/"


def test_members_are_listed_alphabetically_with_the_group(api, user):
    """Members are listed alphabetically with the group."""
    zoe = contact(
        user, full_name="Zoé", family_name="Zola", emails=("zoe@example.org",)
    )
    adam = contact(
        user, full_name="Adam", family_name="Abel", emails=("adam@example.org",)
    )
    team = group(user, name="Team", members=[zoe, adam])

    response = api.get(_url(team.pk))

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2
    assert data["next"] is None
    assert data["group"] == {
        "type": "group",
        "id": str(team.pk),
        "display_name": "Team",
        "email": None,
        "members_count": 2,
        "is_org": False,
        "source": "",
    }
    assert [
        (row["type"], row["display_name"], row["email"]) for row in data["results"]
    ] == [
        ("contact", "Adam", "adam@example.org"),
        ("contact", "Zoé", "zoe@example.org"),
    ]


def test_members_without_email_are_kept_with_null(api, user):
    """Members without email are kept with null."""
    nomail = contact(user, full_name="No mail", emails=())
    team = group(user, name="Team", members=[nomail])

    rows = api.get(_url(team.pk)).json()["results"]

    assert rows == [
        {
            "type": "contact",
            "id": str(nomail.pk),
            "display_name": "No mail",
            "email": None,
            "emails": [],
            "company": None,
            "job_title": None,
            "photo": None,
            "is_org": False,
            "source": "",
        }
    ]


def test_an_overlaid_member_is_listed_as_the_overlay(api, user):
    """An overlaid member is listed as the overlay."""
    synced = contact(
        org=user.organization, full_name="Claire Dubois", source="ldap", external_id="1"
    )
    team = group(org=user.organization, name="Annuaire", members=[synced])
    overlay = models.Contact(
        owner=user,
        organization=user.organization,
        overlay_of=synced,
        full_name="Claire D.",
        overridden_fields=["full_name"],
    )
    overlay.save()

    rows = api.get(_url(team.pk)).json()["results"]

    assert [(row["id"], row["display_name"]) for row in rows] == [
        (str(overlay.pk), "Claire D.")
    ]


def test_hidden_and_trashed_members_are_left_out(api, user):
    """Hidden and trashed members are left out."""
    colleague = factories.UserFactory(organization=user.organization)
    shared = contact(org=user.organization, full_name="Shared")
    theirs = contact(colleague, full_name="Theirs")
    gone = contact(org=user.organization, full_name="Gone")
    gone.deleted_at = timezone.now()
    gone.save()
    team = group(org=user.organization, name="Team", members=[shared, theirs, gone])

    data = api.get(_url(team.pk)).json()

    assert [row["display_name"] for row in data["results"]] == ["Shared"]
    assert data["group"]["members_count"] == 1


def test_large_groups_are_paginated(api, user):
    """Large groups are paginated."""
    team = group(
        user,
        name="Big",
        members=[contact(user, full_name=f"M{i:02}") for i in range(5)],
    )

    first = api.get(_url(team.pk), {"page_size": 2}).json()
    last = api.get(_url(team.pk), {"page_size": 2, "page": 3}).json()

    assert first["count"] == 5
    assert [row["display_name"] for row in first["results"]] == ["M00", "M01"]
    assert first["next"].endswith("page=2&page_size=2")
    assert [row["display_name"] for row in last["results"]] == ["M04"]
    assert last["next"] is None


@pytest.mark.parametrize("kind", ["colleague", "other_org", "unknown"])
def test_a_group_the_user_cannot_see_is_404(api, user, kind):
    """A group the user cannot see is 404."""
    if kind == "colleague":
        group_id = group(
            factories.UserFactory(organization=user.organization), name="X"
        ).pk
    elif kind == "other_org":
        group_id = group(org=factories.OrganizationFactory(), name="X").pk
    else:
        group_id = uuid.uuid4()

    assert api.get(_url(group_id)).status_code == 404
