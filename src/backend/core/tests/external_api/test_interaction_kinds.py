"""Interaction kinds: what happened, and what it weighs."""

import pytest

from core import models
from core.services import interactions

from .conftest import contact

pytestmark = pytest.mark.django_db

URL = "/external_api/v1.0/interactions/"


def _post(client, kind, rows):
    body = {"items": [{"type": "contact", "id": str(row.pk)} for row in rows]}
    if kind is not None:
        body["kind"] = kind
    return client.post(URL, body, format="json")


def _score(user, row):
    return models.Interaction.objects.get(user=user, contact=row).score


def test_a_call_without_kind_is_a_share(api, user):
    """A call without kind is a share (the first version of the API)."""
    felix = contact(user, full_name="Félix")

    assert _post(api, None, [felix]).status_code == 204
    assert _score(user, felix) == pytest.approx(1, abs=1e-3)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("email_sent", 1.0), ("email_received", 0.2)],
)
def test_each_kind_adds_its_weight(messages_api, user, kind, expected):
    """Each kind adds its weight."""
    bob = contact(user, full_name="Bob")

    assert _post(messages_api, kind, [bob]).status_code == 204
    assert _score(user, bob) == pytest.approx(expected, abs=1e-3)


def test_weights_come_from_the_settings(messages_api, user, settings):
    """Weights come from the settings."""
    settings.INTERACTION_KINDS = {
        **settings.INTERACTION_KINDS,
        "email_sent": {"weight": 3},
    }
    bob = contact(user, full_name="Bob")

    _post(messages_api, "email_sent", [bob])

    assert _score(user, bob) == pytest.approx(3, abs=1e-3)


def test_an_unknown_kind_is_refused(api):
    """An unknown kind is refused."""
    response = api.post(
        URL,
        {
            "kind": "telepathy",
            "items": [
                {"type": "contact", "id": "79aa8714-94af-4ea5-8fc3-55673153af3e"}
            ],
        },
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    ("client_name", "kind"),
    [("api", "email_sent"), ("api", "mention"), ("messages_api", "share")],
)
def test_an_app_may_only_report_its_own_kinds(request, user, client_name, kind):
    """An app may only report its own kinds (Drive can't report emails)."""
    client = request.getfixturevalue(client_name)
    felix = contact(user, full_name="Félix")

    assert _post(client, kind, [felix]).status_code == 403
    assert not models.Interaction.objects.exists()


def test_an_app_without_kinds_reports_nothing(api, user, settings):
    """An allowed app with no kinds configured can't report interactions."""
    settings.EXTERNAL_API = {**settings.EXTERNAL_API, "interactions": {"enabled": True}}
    felix = contact(user, full_name="Félix")

    assert _post(api, "share", [felix]).status_code == 403


def test_a_mass_event_weighs_less_per_person(api, user, settings):
    """Past the threshold, each person weighs threshold / n."""
    settings.INTERACTION_MASS_THRESHOLD = 10
    people = [contact(user, full_name=f"P{i}") for i in range(20)]

    _post(api, "share", people)

    assert _score(user, people[0]) == pytest.approx(0.5, abs=1e-3)


def test_a_small_event_keeps_the_full_weight(api, user):
    """Up to the threshold, each person gets the full weight."""
    people = [contact(user, full_name=f"P{i}") for i in range(10)]

    _post(api, "share", people)

    assert _score(user, people[9]) == pytest.approx(1, abs=1e-3)


def test_a_row_named_twice_counts_once(api, user):
    """A row named twice counts once, and as one person."""
    felix = contact(user, full_name="Félix")

    _post(api, "share", [felix, felix])

    interaction = models.Interaction.objects.get(user=user)
    assert interaction.count == 1
    assert interaction.score == pytest.approx(1, abs=1e-3)


@pytest.mark.parametrize(("people", "expected"), [(1, 1.0), (10, 1.0), (40, 0.25)])
def test_weight(people, expected, settings):
    """weight() applies the mass threshold."""
    settings.INTERACTION_MASS_THRESHOLD = 10

    assert interactions.weight("share", people=people) == pytest.approx(expected)
