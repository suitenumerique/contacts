"""Tests for the channels API and API-key authentication."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument,too-many-arguments

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core import factories, models
from core.entitlements import EntitlementsUnavailableError

pytestmark = pytest.mark.django_db

CHANNELS_URL = "/api/v1.0/channels/"
CONTACTS_URL = "/api/v1.0/contacts/"


@pytest.fixture
def user():
    return factories.UserFactory()


@pytest.fixture
def client(user):
    client = APIClient()
    client.force_login(user)
    return client


def test_create_channel_returns_key_once(client, user):
    response = client.post(
        CHANNELS_URL,
        {"name": "My integration", "scopes": ["contacts:read"]},
        format="json",
    )
    assert response.status_code == 201, response.content
    data = response.json()
    assert data["scope_level"] == "user"
    assert data["scopes"] == ["contacts:read"]
    api_key = data["api_key"]
    assert len(api_key) > 30

    # the key is not retrievable afterwards
    detail = client.get(f"{CHANNELS_URL}{data['id']}/").json()
    assert "api_key" not in detail
    # and the stored value is a hash, not the key
    channel = models.Channel.objects.get(pk=data["id"])
    assert api_key not in str(channel.encrypted_settings)

    # the key actually authenticates
    response = APIClient().get(
        CONTACTS_URL,
        {"q": "xx"},
        HTTP_X_CHANNEL_ID=data["id"],
        HTTP_X_API_KEY=api_key,
    )
    assert response.status_code == 200


def test_cannot_create_global_channel_via_api(client):
    response = client.post(
        CHANNELS_URL,
        {"name": "Evil", "scope_level": "global", "scopes": ["contacts:read"]},
        format="json",
    )
    assert response.status_code == 403


def test_unknown_scope_rejected(client):
    response = client.post(
        CHANNELS_URL,
        {"name": "Writer", "scopes": ["contacts:write"]},
        format="json",
    )
    # contacts:write is not a scope this app exposes: validation rejects it.
    assert response.status_code == 400


def test_list_only_own_channels(client, user):
    factories.ChannelFactory(user=user, name="Mine")
    factories.ChannelFactory(name="Someone else's")
    names = [c["name"] for c in client.get(CHANNELS_URL).json()]
    assert names == ["Mine"]


def test_regenerate_api_key_invalidates_old(client, user):
    channel = factories.ChannelFactory(user=user)
    old_key = channel.raw_api_key

    response = client.post(f"{CHANNELS_URL}{channel.id}/regenerate-api-key/")
    assert response.status_code == 200
    new_key = response.json()["api_key"]
    assert new_key != old_key

    channel.refresh_from_db()
    assert channel.verify_api_key(new_key)
    assert not channel.verify_api_key(old_key)


def test_update_and_delete(client, user):
    channel = factories.ChannelFactory(user=user)
    response = client.patch(
        f"{CHANNELS_URL}{channel.id}/",
        {"name": "Renamed", "is_active": False, "scopes": ["groups:read"]},
        format="json",
    )
    assert response.status_code == 200
    data = response.json()
    assert (data["name"], data["is_active"], data["scopes"]) == (
        "Renamed",
        False,
        ["groups:read"],
    )

    assert client.delete(f"{CHANNELS_URL}{channel.id}/").status_code == 204
    assert not models.Channel.objects.filter(pk=channel.id).exists()


def test_invalid_credentials_rejected(user):
    channel = factories.ChannelFactory(user=user)
    client = APIClient()

    response = client.get(
        CONTACTS_URL,
        {"q": "x"},
        HTTP_X_CHANNEL_ID=str(channel.id),
        HTTP_X_API_KEY="wrong-key",
    )
    assert response.status_code == 401

    response = client.get(
        CONTACTS_URL,
        {"q": "x"},
        HTTP_X_CHANNEL_ID="not-a-uuid",
        HTTP_X_API_KEY=channel.raw_api_key,
    )
    assert response.status_code == 401


def test_inactive_channel_rejected(user):
    channel = factories.ChannelFactory(user=user)
    models.Channel.objects.filter(pk=channel.pk).update(is_active=False)
    response = APIClient().get(
        CONTACTS_URL,
        {"q": "x"},
        HTTP_X_CHANNEL_ID=str(channel.id),
        HTTP_X_API_KEY=channel.raw_api_key,
    )
    assert response.status_code == 401


def test_expired_key_rejected(user):
    channel = factories.ChannelFactory(user=user)
    expires = (timezone.now() - timedelta(hours=1)).isoformat()
    models.Channel.objects.filter(pk=channel.pk).update(
        settings={"scopes": ["contacts:read"], "expires_at": expires}
    )
    response = APIClient().get(
        CONTACTS_URL,
        {"q": "x"},
        HTTP_X_CHANNEL_ID=str(channel.id),
        HTTP_X_API_KEY=channel.raw_api_key,
    )
    assert response.status_code == 401


def _org_channel(user):
    """An organization-scoped contacts:read channel for *user*'s org."""
    return factories.ChannelFactory(
        user=None,
        organization=user.organization,
        scope_level="organization",
        settings={"scopes": ["contacts:read"]},
    )


def _channel_get(channel, params):
    return APIClient().get(
        CONTACTS_URL,
        params,
        HTTP_X_CHANNEL_ID=str(channel.id),
        HTTP_X_API_KEY=channel.raw_api_key,
    )


def test_on_behalf_of_resolves_active_user_by_email(user):
    channel = _org_channel(user)
    response = _channel_get(channel, {"q": "x", "on_behalf_of": user.email})
    assert response.status_code == 200


def test_on_behalf_of_resolves_active_user_by_sub(user):
    channel = _org_channel(user)
    response = _channel_get(channel, {"q": "x", "on_behalf_of": user.sub})
    assert response.status_code == 200


def test_on_behalf_of_inactive_user_rejected(user):
    channel = _org_channel(user)
    models.User.objects.filter(pk=user.pk).update(is_active=False)
    response = _channel_get(channel, {"q": "x", "on_behalf_of": user.email})
    assert response.status_code == 403


def test_on_behalf_of_ambiguous_email_rejected(user):
    """An email shared by several active users must not resolve to any."""
    channel = _org_channel(user)
    factories.UserFactory(email=user.email, organization=user.organization)
    response = _channel_get(channel, {"q": "x", "on_behalf_of": user.email})
    assert response.status_code == 403


def test_on_behalf_of_ignores_inactive_duplicate(user):
    """An inactive duplicate does not make an active email ambiguous."""
    channel = _org_channel(user)
    factories.UserFactory(
        email=user.email, organization=user.organization, is_active=False
    )
    response = _channel_get(channel, {"q": "x", "on_behalf_of": user.email})
    assert response.status_code == 200


def test_mark_used_updates_last_used_at(user):
    channel = factories.ChannelFactory(user=user)
    assert channel.last_used_at is None
    APIClient().get(
        CONTACTS_URL,
        {"q": "x"},
        HTTP_X_CHANNEL_ID=str(channel.id),
        HTTP_X_API_KEY=channel.raw_api_key,
    )
    channel.refresh_from_db()
    assert channel.last_used_at is not None


def test_create_carddav_channel_returns_ready_password(client, user):
    response = client.post(
        CHANNELS_URL,
        {"name": "Phone", "type": "carddav", "scopes": ["carddav:write"]},
        format="json",
    )
    assert response.status_code == 201, response.content
    data = response.json()
    assert data["type"] == "carddav"
    # The device gets one opaque string: <channel id hex><api key>.
    assert data["carddav_password"].startswith(data["id"].replace("-", ""))
    assert data["carddav_password"].endswith(data["api_key"])


def test_create_carddav_channel_refused_when_feature_disabled(client, user, settings):
    settings.FEATURE_CARDDAV = False
    response = client.post(
        CHANNELS_URL,
        {"name": "Phone", "type": "carddav", "scopes": ["carddav:write"]},
        format="json",
    )
    assert response.status_code == 403
    assert not models.Channel.objects.filter(user=user).exists()

    # REST channels are unaffected.
    response = client.post(
        CHANNELS_URL, {"name": "Svc", "scopes": ["contacts:read"]}, format="json"
    )
    assert response.status_code == 201


def test_rest_channel_has_no_carddav_password(client, user):
    response = client.post(
        CHANNELS_URL, {"name": "Svc", "scopes": ["contacts:read"]}, format="json"
    )
    assert response.status_code == 201
    assert response.json()["carddav_password"] is None


def test_carddav_scopes_rejected_on_rest_channel(client, user):
    response = client.post(
        CHANNELS_URL, {"name": "Mixed", "scopes": ["carddav:write"]}, format="json"
    )
    assert response.status_code == 400


def test_rest_scopes_rejected_on_carddav_channel(client, user):
    response = client.post(
        CHANNELS_URL,
        {"name": "Mixed", "type": "carddav", "scopes": ["contacts:read"]},
        format="json",
    )
    assert response.status_code == 400


def test_patch_cannot_cross_scope_families(client, user):
    """The carddav/api_key scope split holds on update, not just on create."""
    rest = factories.ChannelFactory(user=user)
    response = client.patch(
        f"{CHANNELS_URL}{rest.id}/", {"scopes": ["carddav:write"]}, format="json"
    )
    assert response.status_code == 400

    carddav = factories.ChannelFactory(
        user=user, type="carddav", settings={"scopes": ["carddav:read"]}
    )
    response = client.patch(
        f"{CHANNELS_URL}{carddav.id}/", {"scopes": ["contacts:read"]}, format="json"
    )
    assert response.status_code == 400
    response = client.patch(
        f"{CHANNELS_URL}{carddav.id}/", {"scopes": []}, format="json"
    )
    assert response.status_code == 400
    carddav.refresh_from_db()
    assert carddav.scopes == ["carddav:read"]


@pytest.mark.parametrize(
    "expires_at,expired",
    [
        ("not a date", True),  # unparsable: fail closed
        ("2026-13-45T00:00:00Z", True),  # well-formed but invalid
        ("2000-01-01T00:00:00", True),  # naive: read as UTC, in the past
        ("2999-01-01T00:00:00", False),
    ],
)
def test_expires_at_parsing(user, expires_at, expired):
    channel = factories.ChannelFactory(user=user)
    models.Channel.objects.filter(pk=channel.pk).update(
        settings={"scopes": ["contacts:read"], "expires_at": expires_at}
    )
    channel.refresh_from_db()
    assert channel.is_expired is expired


def test_deactivated_user_loses_rest_channel_access(user):
    """A user-scoped channel must stop resolving once its user is off."""
    channel = factories.ChannelFactory(
        user=user, settings={"scopes": ["contacts:read"]}
    )
    response = APIClient().get(
        CONTACTS_URL,
        HTTP_X_CHANNEL_ID=str(channel.id),
        HTTP_X_API_KEY=channel.raw_api_key,
    )
    assert response.status_code == 200

    models.User.objects.filter(pk=user.pk).update(is_active=False)
    response = APIClient().get(
        CONTACTS_URL,
        HTTP_X_CHANNEL_ID=str(channel.id),
        HTTP_X_API_KEY=channel.raw_api_key,
    )
    assert response.status_code == 403


# --- scope levels and types --------------------------------------------------


@pytest.mark.parametrize("scope_level", ["organization", "global"])
def test_only_user_channels_carry_a_user(user, scope_level):
    channel = models.Channel(
        name="Svc",
        scope_level=scope_level,
        user=user,
        organization=user.organization if scope_level == "organization" else None,
        settings={"scopes": ["contacts:read"]},
    )
    with pytest.raises(ValidationError) as excinfo:
        channel.full_clean()
    assert "user" in excinfo.value.message_dict


@pytest.mark.parametrize("scope_level", ["organization", "global"])
def test_database_refuses_a_user_on_non_user_channels(user, scope_level):
    channel = factories.ChannelFactory(
        user=None,
        scope_level=scope_level,
        organization=user.organization if scope_level == "organization" else None,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        models.Channel.objects.filter(pk=channel.pk).update(user=user)


def test_global_channels_have_no_organization(user):
    channel = models.Channel(
        name="Svc",
        scope_level="global",
        organization=user.organization,
        settings={"scopes": ["contacts:read"]},
    )
    with pytest.raises(ValidationError) as excinfo:
        channel.full_clean()
    assert "organization" in excinfo.value.message_dict


@pytest.mark.parametrize(
    "channel_type,scopes",
    [
        ("api_key", ["carddav:write"]),
        ("carddav", ["contacts:read"]),
        ("api_key", []),
        ("webhook", ["contacts:read"]),
    ],
)
def test_model_keeps_scope_families_apart(user, channel_type, scopes):
    """Admin and CLI go through the model, so the rule lives there too."""
    channel = models.Channel(
        name="Mixed",
        type=channel_type,
        user=user,
        organization=user.organization,
        settings={"scopes": scopes},
    )
    with pytest.raises(ValidationError):
        channel.full_clean()


def test_channel_api_lists_only_user_channels(client, user):
    factories.ChannelFactory(user=user, name="Mine")
    factories.ChannelFactory(
        user=None,
        scope_level="organization",
        organization=user.organization,
        name="Org",
    )
    assert [c["name"] for c in client.get(CHANNELS_URL).json()] == ["Mine"]


def test_channel_list_is_a_plain_array(client, user):
    factories.ChannelFactory(user=user)
    assert isinstance(client.get(CHANNELS_URL).json(), list)


def test_malformed_channel_id_is_404(client):
    assert client.get(f"{CHANNELS_URL}not-a-uuid/").status_code == 404


# --- entitlements on user channels ---------------------------------------------


def _deny_entitlements(monkeypatch, unavailable=False):
    def fake(*_args, **_kwargs):
        if unavailable:
            raise EntitlementsUnavailableError("down")
        return {"can_access": False}

    monkeypatch.setattr("core.authentication.channel.get_user_entitlements", fake)


@pytest.mark.parametrize("unavailable", [False, True])
def test_user_channel_stops_working_without_entitlement(user, monkeypatch, unavailable):
    """A user's own API key is revoked with their access to the app."""
    channel = factories.ChannelFactory(user=user)
    factories.ContactFactory(owner=user)
    _deny_entitlements(monkeypatch, unavailable=unavailable)

    response = _channel_get(channel, {})

    assert response.status_code == 403


# --- trash --------------------------------------------------------------------


def test_channels_never_see_the_corbeille(user):
    channel = factories.ChannelFactory(user=user)
    kept = factories.ContactFactory(owner=user)
    trashed = factories.ContactFactory(owner=user, deleted_at=timezone.now())

    listed = _channel_get(channel, {}).json()["results"]
    assert [row["id"] for row in listed] == [str(kept.id)]

    in_trash = _channel_get(channel, {"trash": "true"}).json()["results"]
    assert [row["id"] for row in in_trash] == [str(kept.id)]

    response = APIClient().get(
        f"{CONTACTS_URL}{trashed.id}/",
        HTTP_X_CHANNEL_ID=str(channel.id),
        HTTP_X_API_KEY=channel.raw_api_key,
    )
    assert response.status_code == 404
