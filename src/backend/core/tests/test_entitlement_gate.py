"""The API refuses a user the entitlements backend does not let in.

Hiding the SPA is not enough: every endpoint enforces the entitlement too.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument

import base64

from django.test import Client

import pytest
from rest_framework.test import APIClient

from core import factories, models
from core.entitlements import EntitlementsUnavailableError
from core.enums import ChannelScope

pytestmark = pytest.mark.django_db

GATED_URLS = [
    "/api/v1.0/contacts/",
    "/api/v1.0/groups/",
    "/api/v1.0/labels/",
    "/api/v1.0/channels/",
]


@pytest.fixture
def user():
    return factories.UserFactory(email="gated@example.org")


def deny(monkeypatch, entitlements=None, unavailable=False):
    """Make every entitlement lookup refuse, or blow up."""

    def fake(*_args, **_kwargs):
        if unavailable:
            raise EntitlementsUnavailableError("down")
        return entitlements if entitlements is not None else {"can_access": False}

    monkeypatch.setattr("core.api.permissions.get_user_entitlements", fake)
    monkeypatch.setattr("carddav.auth.get_user_entitlements", fake)


@pytest.mark.parametrize("url", GATED_URLS)
def test_unentitled_user_is_refused(url, user, monkeypatch):
    deny(monkeypatch)
    client = APIClient()
    client.force_login(user)

    assert client.get(url).status_code == 403


@pytest.mark.parametrize("url", GATED_URLS)
def test_entitled_user_is_served(url, user, monkeypatch):
    deny(monkeypatch, entitlements={"can_access": True})
    client = APIClient()
    client.force_login(user)

    assert client.get(url).status_code == 200


@pytest.mark.parametrize("url", GATED_URLS)
def test_entitlements_outage_fails_closed(url, user, monkeypatch):
    deny(monkeypatch, unavailable=True)
    client = APIClient()
    client.force_login(user)

    assert client.get(url).status_code == 403


def test_unentitled_user_cannot_write_either(user, monkeypatch):
    deny(monkeypatch)
    client = APIClient()
    client.force_login(user)

    response = client.post(
        "/api/v1.0/contacts/",
        {"given_name": "A", "family_name": "B", "emails": [{"value": "a@b.example"}]},
        format="json",
    )

    assert response.status_code == 403
    assert not models.Contact.objects.filter(owner=user).exists()


def carddav_headers(user):
    """Basic Auth for a CardDAV credential belonging to *user*."""
    channel = models.Channel(
        name="Phone",
        type="carddav",
        scope_level="user",
        user=user,
        organization=user.organization,
        settings={"scopes": [ChannelScope.CARDDAV_WRITE.value]},
    )
    raw_key = channel.generate_api_key()
    channel.save()
    token = base64.b64encode(
        f"{user.email}:{channel.pk.hex}{raw_key}".encode()
    ).decode()
    return {"HTTP_AUTHORIZATION": f"Basic {token}"}


def propfind(headers):
    """PROPFIND the CardDAV root with *headers*."""
    return Client().generic(
        "PROPFIND", "/carddav/", data=b"", content_type="application/xml", **headers
    )


def test_carddav_is_gated_too(user, monkeypatch):
    """A sync password minted earlier must not outlive the entitlement."""
    headers = carddav_headers(user)
    deny(monkeypatch)

    assert propfind(headers).status_code == 403


def test_carddav_serves_an_entitled_user(user, monkeypatch):
    headers = carddav_headers(user)
    deny(monkeypatch, entitlements={"can_access": True})

    assert propfind(headers).status_code == 207
