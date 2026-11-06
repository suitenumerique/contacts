"""Interactions reported by a service channel, for events without a user session."""

import pytest
from rest_framework.test import APIClient

from core import factories, models
from core.external_api.throttles import ExternalRateThrottle

from .conftest import contact

pytestmark = pytest.mark.django_db

URL = "/external_api/v1.0/interactions/"


def _channel(user, scopes=("interactions:write",), level="organization"):
    if level == "organization":
        return factories.ChannelFactory(
            user=None,
            organization=user.organization,
            scope_level=level,
            settings={"scopes": list(scopes)},
        )
    return factories.ChannelFactory(
        user=user, scope_level=level, settings={"scopes": list(scopes)}
    )


def _post(channel, body):
    return APIClient().post(
        URL,
        body,
        format="json",
        HTTP_X_CHANNEL_ID=str(channel.pk),
        HTTP_X_API_KEY=channel.raw_api_key,
    )


def _received(user, *addresses, **extra):
    return {
        "kind": "email_received",
        "on_behalf_of": user.email,
        "items": [{"type": "email", "value": address} for address in addresses],
        **extra,
    }


def test_a_channel_reports_an_email_received_for_a_user(user):
    """A channel reports an email received, for the on_behalf_of user."""
    bob = contact(user, full_name="Bob", emails=("bob@example.org",))

    response = _post(_channel(user), _received(user, "bob@example.org"))

    assert response.status_code == 204
    interaction = models.Interaction.objects.get(user=user)
    assert interaction.contact_id == bob.pk
    assert interaction.score == pytest.approx(0.2, abs=1e-3)


def test_on_behalf_of_is_required_for_an_organization_channel(user):
    """An organization channel must name the user."""
    body = _received(user, "bob@example.org")
    del body["on_behalf_of"]

    assert _post(_channel(user), body).status_code == 403


def test_a_user_outside_the_channels_organization_is_refused(user):
    """An organization channel can't act for another organization's user."""
    outsider = factories.UserFactory()

    assert (
        _post(_channel(user), _received(outsider, "bob@example.org")).status_code == 403
    )


def test_a_user_channel_acts_for_its_user(user):
    """A user channel acts for its own user, without on_behalf_of."""
    bob = contact(user, full_name="Bob", emails=("bob@example.org",))
    body = _received(user, "bob@example.org")
    del body["on_behalf_of"]

    assert _post(_channel(user, level="user"), body).status_code == 204
    assert models.Interaction.objects.get(user=user).contact_id == bob.pk


def test_a_channel_without_the_scope_is_refused(user):
    """A channel needs the interactions:write scope."""
    channel = _channel(user, scopes=("contacts:read",))

    assert _post(channel, _received(user, "bob@example.org")).status_code == 403


def test_a_channel_may_only_report_channel_kinds(user):
    """A channel reports the channel kinds only (not shares)."""
    response = _post(_channel(user), _received(user, "bob@example.org", kind="share"))

    assert response.status_code == 403


def test_a_channel_may_send_up_to_500_items(user):
    """A channel may send up to 500 items, a user token 50."""
    channel = _channel(user)
    many = [f"p{i}@example.org" for i in range(500)]

    assert _post(channel, _received(user, *many)).status_code == 204
    assert (
        _post(channel, _received(user, *many, "one-more@example.org")).status_code
        == 400
    )


def test_a_user_token_may_send_up_to_50_items(messages_api):
    """A user token may send up to 50 items."""
    items = [{"type": "email", "value": f"p{i}@example.org"} for i in range(51)]

    response = messages_api.post(
        URL, {"kind": "email_sent", "items": items}, format="json"
    )

    assert response.status_code == 400


def test_on_behalf_of_is_ignored_with_a_user_token(messages_api, user):
    """A user token always acts for its own user."""
    colleague = factories.UserFactory(organization=user.organization)
    bob = contact(user, full_name="Bob", emails=("bob@example.org",))

    messages_api.post(
        URL,
        {
            "kind": "email_sent",
            "on_behalf_of": colleague.email,
            "items": [{"type": "email", "value": "bob@example.org"}],
        },
        format="json",
    )

    assert models.Interaction.objects.get(contact=bob).user_id == user.pk


def test_channels_are_limited_per_channel(user, monkeypatch):
    """Channel calls count against the channel's own limit."""
    # DRF reads the rates once, at import: patch the class, not settings.
    monkeypatch.setattr(
        ExternalRateThrottle,
        "THROTTLE_RATES",
        {
            **ExternalRateThrottle.THROTTLE_RATES,
            "external_interactions_channel": "2/minute",
        },
    )
    channel = _channel(user)
    body = _received(user, "bob@example.org")

    statuses = [_post(channel, body).status_code for _ in range(3)]
    other = _post(_channel(user), body).status_code

    assert statuses == [204, 204, 429]
    assert other == 204
