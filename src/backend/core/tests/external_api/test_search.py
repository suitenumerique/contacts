"""GET /external_api/v1.0/search/: the user's contacts and groups, ranked."""

import datetime
from unittest import mock

from django.db import OperationalError
from django.utils import timezone

import pytest

from core import factories, models
from core.services import search as search_service

from .conftest import contact, group

pytestmark = pytest.mark.django_db

URL = "/external_api/v1.0/search/"


def _names(response):
    assert response.status_code == 200, response.content
    return [row["display_name"] for row in response.json()["results"]]


def _pick(user, row, *, days_ago=0, times=1):
    """Record *times* picks of *row*, *days_ago* days ago."""
    now = timezone.now() - datetime.timedelta(days=days_ago)
    kind = "group" if isinstance(row, models.ContactGroup) else "contact"
    for _ in range(times):
        models.Interaction.record(user, now=now, **{kind: row})


# Shape


def test_contacts_and_groups_come_in_one_list(api, user):
    """Contacts and groups come in one list."""
    felix = contact(
        user,
        full_name="Félix Marchand",
        emails=("felix.marchand@brigny.example", "felix@home.example"),
        company="Mairie de Brigny",
        job_title="Conseiller municipal",
        photo="https://cdn.example/felix.png",
    )
    team = group(
        org=user.organization, name="Marchés publics", email="marches@brigny.example"
    )
    group(user, name="Équipe")

    response = api.get(URL, {"q": "mar"})

    assert response.status_code == 200
    assert response.json() == {
        "results": [
            {
                "type": "contact",
                "id": str(felix.pk),
                "display_name": "Félix Marchand",
                "email": "felix.marchand@brigny.example",
                "emails": [
                    {"type": "work", "value": "felix.marchand@brigny.example"},
                    {"type": "work", "value": "felix@home.example"},
                ],
                "company": "Mairie de Brigny",
                "job_title": "Conseiller municipal",
                "photo": "https://cdn.example/felix.png",
                "is_org": False,
                "source": "",
            },
            {
                "type": "group",
                "id": str(team.pk),
                "display_name": "Marchés publics",
                "email": "marches@brigny.example",
                "members_count": 0,
                "is_org": True,
                "source": "",
            },
        ]
    }


def test_empty_fields_are_null_and_inline_photos_left_out(api, user):
    """Empty fields are null and inline photos left out."""
    contact(user, full_name="Ada", photo="data:image/png;base64,iVBORw0KGgo=")
    group(user, name="Adas")

    rows = api.get(URL, {"q": "ada"}).json()["results"]

    assert rows[0]["company"] is None
    assert rows[0]["job_title"] is None
    assert rows[0]["photo"] is None
    assert rows[1]["email"] is None


def test_members_count_counts_visible_members(api, user):
    """Members count counts visible members."""
    other = factories.UserFactory(organization=user.organization)
    members = [contact(user, full_name=f"M{i}") for i in range(3)]
    trashed = contact(user, full_name="Gone")
    trashed.deleted_at = timezone.now()
    trashed.save()
    hidden = contact(other, full_name="Someone else's")
    group(user, name="Team", members=[*members, trashed, hidden])

    rows = api.get(URL, {"q": "team"}).json()["results"]

    assert rows[0]["members_count"] == 3


# Visibility


def test_only_what_the_user_sees_in_contacts(api, user):
    """Only what the user sees in contacts."""
    colleague = factories.UserFactory(organization=user.organization)
    outsider = factories.UserFactory()
    contact(user, full_name="Marie mine")
    contact(org=user.organization, full_name="Marie shared")
    contact(colleague, full_name="Marie colleague's")
    contact(org=outsider.organization, full_name="Marie elsewhere")
    trashed = contact(user, full_name="Marie trashed")
    trashed.deleted_at = timezone.now()
    trashed.save()
    group(colleague, name="Marie's group")

    assert sorted(_names(api.get(URL, {"q": "marie"}))) == [
        "Marie mine",
        "Marie shared",
    ]


def test_an_overlay_stands_for_the_contact_it_covers(api, user):
    """An overlay stands for the contact it covers."""
    synced = contact(
        org=user.organization,
        full_name="Claire Dubois",
        emails=("claire.dubois@brigny.example",),
        source="ldap",
        external_id="cn=claire",
    )
    overlay = models.Contact(
        owner=user,
        organization=user.organization,
        overlay_of=synced,
        full_name="Claire D. (annuaire)",
        overridden_fields=["full_name"],
    )
    overlay.save()

    rows = api.get(URL, {"q": "claire"}).json()["results"]

    assert [(row["id"], row["display_name"], row["email"]) for row in rows] == [
        (str(overlay.pk), "Claire D. (annuaire)", "claire.dubois@brigny.example")
    ]


def test_contacts_without_email_are_left_out(api, user):
    """Contacts without email are left out."""
    contact(user, full_name="Paul with", emails=("paul@example.org",))
    contact(
        user,
        full_name="Paul without",
        emails=(),
        phones=[{"type": "", "value": "0601020304"}],
    )

    assert _names(api.get(URL, {"q": "paul"})) == ["Paul with"]


def test_the_users_own_card_is_left_out(api, user):
    """The users own card is left out."""
    contact(user, full_name="Alice me", emails=("Alice@Brigny.example",))
    contact(user, full_name="Alice other")

    assert _names(api.get(URL, {"q": "alice"})) == ["Alice other"]


# Matching


def test_word_starts_come_before_matches_inside_words(api, user):
    """Word starts come before matches inside words."""
    contact(user, full_name="Anne Dumarais")
    contact(user, full_name="Zoé Marchand")
    contact(user, full_name="Bob Martin")

    assert _names(api.get(URL, {"q": "mar"})) == [
        "Bob Martin",
        "Zoé Marchand",
        "Anne Dumarais",
    ]


def test_short_queries_only_match_the_start_of_words(api, user):
    """Short queries only match the start of words."""
    contact(user, full_name="Anne Dumarais")
    contact(user, full_name="Zoé Marchand")

    assert _names(api.get(URL, {"q": "ma"})) == ["Zoé Marchand"]


def test_every_word_must_match(api, user):
    """Every word must match."""
    contact(user, full_name="Marie Curie")
    contact(user, full_name="Marie Dupont")

    assert _names(api.get(URL, {"q": "mar cu"})) == ["Marie Curie"]


def test_accents_and_case_are_ignored(api, user):
    """Accents and case are ignored."""
    contact(user, full_name="Élodie Garnier")

    assert _names(api.get(URL, {"q": "ELODIE"})) == ["Élodie Garnier"]


def test_pieces_of_an_address_start_words(api, user):
    """Pieces of an address start words."""
    contact(user, full_name="C. D.", emails=("claire.dubois@brigny.example",))

    assert _names(api.get(URL, {"q": "dubois"})) == ["C. D."]
    assert _names(api.get(URL, {"q": "du"})) == ["C. D."]


def test_phone_numbers_are_found_by_digits(api, user):
    """Phone numbers are found by digits."""
    contact(
        user,
        full_name="Phone owner",
        phones=[{"type": "cell", "value": "06 39 98 63 09"}],
    )

    assert _names(api.get(URL, {"q": "06 39 98"})) == ["Phone owner"]


def test_groups_match_on_name_and_address(api, user):
    """Groups match on name and address."""
    group(user, name="Service technique", email="technique@brigny.example")

    assert _names(api.get(URL, {"q": "tech"})) == ["Service technique"]
    assert _names(api.get(URL, {"q": "technique@"})) == ["Service technique"]


# Ranking


def test_picked_rows_come_first_by_rank(api, user):
    """Picked rows come first by rank."""
    often = contact(user, full_name="Zed Often")
    once = contact(user, full_name="Yan Once")
    team = group(user, name="Xavier's team")
    contact(user, full_name="Abel Never")
    _pick(user, often, times=3)
    _pick(user, once)
    _pick(user, team, times=2)

    assert _names(api.get(URL, {"q": ""})) == ["Zed Often", "Xavier's team", "Yan Once"]


def test_old_picks_fade(api, user):
    """Old picks fade."""
    old = contact(user, full_name="Old friend")
    new = contact(user, full_name="New colleague")
    # 8 picks 4 months ago weigh 8 / 2^4 = 0.5; 1 pick today weighs 1.
    _pick(user, old, days_ago=120, times=8)
    _pick(user, new)

    assert _names(api.get(URL, {"q": ""})) == ["New colleague", "Old friend"]


def test_ranked_rows_still_have_to_match(api, user):
    """Ranked rows still have to match."""
    picked = contact(user, full_name="Zoé Picked")
    contact(user, full_name="Anne Other")
    _pick(user, picked, times=5)

    assert _names(api.get(URL, {"q": "anne"})) == ["Anne Other"]


def test_favorites_come_after_picks_and_before_other_matches(api, user):
    """Favorites come after picks and before other matches."""
    contact(user, full_name="Anne Plain")
    starred = contact(user, full_name="Zoé Anne Starred")
    starred.favorited_by.add(user)
    picked = contact(user, full_name="Yves Anne Picked")
    _pick(user, picked)
    starred_group = group(user, name="Anne's group")
    starred_group.favorited_by.add(user)

    assert _names(api.get(URL, {"q": "anne"})) == [
        "Yves Anne Picked",
        "Anne's group",
        "Zoé Anne Starred",
        "Anne Plain",
    ]


def test_a_pick_of_an_overlay_ranks_the_overlay(api, user):
    """A pick of an overlay ranks the overlay."""
    synced = contact(
        org=user.organization, full_name="Claire Dubois", source="ldap", external_id="1"
    )
    overlay = models.Contact(
        owner=user, organization=user.organization, overlay_of=synced
    )
    overlay.save()
    contact(user, full_name="Abel Claire")
    _pick(user, overlay)

    rows = api.get(URL, {"q": "claire"}).json()["results"]

    assert [row["id"] for row in rows][0] == str(overlay.pk)


def test_an_empty_query_lists_only_picked_rows(api, user):
    """An empty query lists only picked rows."""
    contact(user, full_name="Never picked")

    assert _names(api.get(URL)) == []


# Parameters


def test_types_filters_the_kinds(api, user):
    """Types filters the kinds."""
    contact(user, full_name="Team member")
    group(user, name="Team")

    assert _names(api.get(URL, {"q": "team", "types": "group"})) == ["Team"]
    assert _names(api.get(URL, {"q": "team", "types": "contact"})) == ["Team member"]


def test_limit_caps_the_results_across_kinds(api, user):
    """Limit caps the results across kinds."""
    for index in range(4):
        contact(user, full_name=f"Team member {index}")
        group(user, name=f"Team {index}")

    assert len(_names(api.get(URL, {"q": "team", "limit": 5}))) == 5


@pytest.mark.parametrize(
    "params",
    [
        {"types": "people"},
        {"types": " , "},
        {"limit": 0},
        {"limit": 51},
        {"q": "x" * 201},
    ],
)
def test_bad_parameters_are_refused(api, params):
    """Bad parameters are refused."""
    assert api.get(URL, params).status_code == 400


# Performance


def test_a_search_runs_a_fixed_number_of_queries(
    api, user, django_assert_max_num_queries
):
    """A search runs a fixed number of queries."""
    for index in range(30):
        row = contact(user, full_name=f"Marie {index}")
        if index % 3 == 0:
            _pick(user, row)
        if index % 5 == 0:
            row.favorited_by.add(user)
    for index in range(5):
        group(user, name=f"Marie's team {index}")

    # Auth (user, entitlements) + interactions + 2 books and groups for
    # each step + member counts: never one query per row.
    with django_assert_max_num_queries(20):
        assert len(_names(api.get(URL, {"q": "mar", "limit": 50}))) == 35


def test_a_search_over_its_time_budget_answers_503(api):
    """A search over its time budget answers 503."""
    # A 5xx fires got_request_exception, which the test client re-raises.
    api.raise_request_exception = False
    with mock.patch.object(
        search_service, "search", side_effect=OperationalError("canceling statement")
    ):
        response = api.get(URL, {"q": "mar"})

    assert response.status_code == 503
