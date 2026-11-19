"""Tests for the entitlements module."""

import json
from unittest import mock

from django.core.cache import cache
from django.test import override_settings

import pytest
import requests
import responses
from rest_framework.test import APIClient

from core import factories, models
from core.api.serializers import UserMeSerializer
from core.authentication import channel as channel_auth
from core.authentication.backends import OIDCAuthenticationBackend
from core.entitlements import (
    EntitlementsUnavailableError,
    get_user_entitlements,
    user_is_admin,
)
from core.entitlements.backends.deploycenter import DeployCenterEntitlementsBackend
from core.entitlements.backends.local import LocalEntitlementsBackend
from core.entitlements.factory import get_entitlements_backend

from carddav import auth as carddav_auth

# -- LocalEntitlementsBackend --


def test_local_backend_grants_access_but_not_admin():
    """The local backend lets everyone in and makes no one an administrator."""
    backend = LocalEntitlementsBackend()
    result = backend.get_user_entitlements("sub-123", "user@example.com")
    assert result == {"can_access": True, "is_admin": False}


def test_local_backend_ignores_parameters():
    """The local backend should work regardless of parameters passed."""
    backend = LocalEntitlementsBackend()
    result = backend.get_user_entitlements(
        "sub-123",
        "user@example.com",
        user_info={"some": "claim"},
        force_refresh=True,
    )
    assert result == {"can_access": True, "is_admin": False}


# -- Factory --


@override_settings(
    ENTITLEMENTS_BACKEND="core.entitlements.backends.local.LocalEntitlementsBackend",
    ENTITLEMENTS_BACKEND_PARAMETERS={},
)
def test_factory_returns_local_backend():
    """The factory should instantiate the configured backend."""
    get_entitlements_backend.cache_clear()
    backend = get_entitlements_backend()
    assert isinstance(backend, LocalEntitlementsBackend)
    get_entitlements_backend.cache_clear()


@override_settings(
    ENTITLEMENTS_BACKEND="core.entitlements.backends.local.LocalEntitlementsBackend",
    ENTITLEMENTS_BACKEND_PARAMETERS={},
)
def test_factory_singleton():
    """The factory should return the same instance on repeated calls."""
    get_entitlements_backend.cache_clear()
    backend1 = get_entitlements_backend()
    backend2 = get_entitlements_backend()
    assert backend1 is backend2
    get_entitlements_backend.cache_clear()


# -- get_user_entitlements public API --


@override_settings(
    ENTITLEMENTS_BACKEND="core.entitlements.backends.local.LocalEntitlementsBackend",
    ENTITLEMENTS_BACKEND_PARAMETERS={},
)
def test_get_user_entitlements_with_local_backend():
    """The public API should delegate to the configured backend."""
    get_entitlements_backend.cache_clear()
    result = get_user_entitlements(factories.UserFactory())
    assert result["can_access"] is True
    get_entitlements_backend.cache_clear()


# -- DeployCenterEntitlementsBackend --

DC_URL = "https://deploy.example.com/api/v1.0/entitlements/"
DC_SERVICE_ID = 7
SIRET = "12345678900011"
OTHER_SIRET = "98765432100012"
CLAIMS = {"siret": SIRET}


def dc_answer(can_access=True, is_admin=False):
    """Build the answer of DeployCenter for an organization that it knows."""
    organization = {
        "id": "5c9f3c0e-7f0b-4a6e-9d3a-2f6d1f4f8a11",
        "type": "commune",
        "name": "Mairie de Test",
        "oidc_valid": None,
    }
    if not can_access:
        return {
            "organization": organization,
            "operator": None,
            "entitlements": {"can_access": False, "can_access_reason": "not_activated"},
            "metrics": {},
            "potentialOperators": [],
        }
    entitlements = {"can_access": True, "is_admin": is_admin}
    if is_admin:
        entitlements["is_admin_resolve_level"] = "organization"
    return {
        "organization": organization,
        "operator": {
            "id": "b2c0a6a4-1e56-4f0f-8a43-9b1c5a3d7e22",
            "name": "Operateur de Test",
            "siret": "11122233300044",
            "url": "https://operator.example.com",
            "config": {},
        },
        "entitlements": entitlements,
        "metrics": {},
    }


def add_dc_answer(answer):
    """Register an answer of DeployCenter, which refuses a call without a siret."""

    def callback(request):
        if "siret" not in request.params:
            return (400, {}, json.dumps({"siret": ["This field is required."]}))
        return (200, {}, json.dumps(answer))

    responses.add_callback(
        responses.GET, DC_URL, callback=callback, content_type="application/json"
    )


def dc_backend():
    """Build the backend with the parameters that DeployCenter needs."""
    return DeployCenterEntitlementsBackend(
        base_url=DC_URL,
        service_id=DC_SERVICE_ID,
        api_key="test-key",
        oidc_claims=["siret"],
    )


@responses.activate
def test_deploycenter_backend_grants_access():
    """DeployCenter backend should return can_access from API response."""
    add_dc_answer(dc_answer())

    backend = dc_backend()
    result = backend.get_user_entitlements("sub-123", "user@example.com", CLAIMS)
    assert result == {
        "can_access": True,
        "is_admin": False,
        "organization_name": "Mairie de Test",
    }

    # Verify request was made with correct params and header
    assert len(responses.calls) == 1
    request = responses.calls[0].request
    assert request.params == {
        "service_id": str(DC_SERVICE_ID),
        "account_type": "user",
        "account_email": "user@example.com",
        "siret": SIRET,
    }
    assert request.headers["X-Service-Auth"] == "Bearer test-key"


@responses.activate
def test_deploycenter_backend_reads_is_admin():
    """The backend returns the is_admin of the DeployCenter answer."""
    add_dc_answer(dc_answer(is_admin=True))

    result = dc_backend().get_user_entitlements("sub-123", "user@example.com", CLAIMS)
    assert result["is_admin"] is True


@responses.activate
def test_deploycenter_backend_denies_access():
    """DeployCenter backend should return can_access=False when API says so."""
    add_dc_answer(dc_answer(can_access=False))

    backend = dc_backend()
    result = backend.get_user_entitlements("sub-123", "user@example.com", CLAIMS)
    assert result == {
        "can_access": False,
        "is_admin": False,
        "organization_name": "Mairie de Test",
    }


@responses.activate
def test_deploycenter_backend_denies_access_to_an_unknown_organization():
    """DeployCenter answers without an organization for a siret it does not know."""
    add_dc_answer(
        {
            "organization": None,
            "operator": None,
            "entitlements": {
                "can_access": False,
                "can_access_reason": "no_organization",
            },
            "metrics": {},
        }
    )

    result = dc_backend().get_user_entitlements("sub-123", "user@example.com", CLAIMS)
    assert result == {"can_access": False, "is_admin": False}


@responses.activate
@override_settings(ENTITLEMENTS_CACHE_TIMEOUT=300)
def test_deploycenter_backend_uses_cache():
    """DeployCenter should use cached results when not force_refresh."""
    add_dc_answer(dc_answer())

    backend = dc_backend()

    # First call hits the API
    result1 = backend.get_user_entitlements("sub-123", "user@example.com", CLAIMS)
    assert result1["can_access"] is True
    assert len(responses.calls) == 1

    # Second call should use cache
    result2 = backend.get_user_entitlements("sub-123", "user@example.com", CLAIMS)
    assert result2["can_access"] is True
    assert len(responses.calls) == 1  # No additional API call


@responses.activate
@override_settings(ENTITLEMENTS_CACHE_TIMEOUT=300)
def test_deploycenter_backend_force_refresh_bypasses_cache():
    """force_refresh=True should bypass cache and hit the API."""
    add_dc_answer(dc_answer())
    add_dc_answer(dc_answer(can_access=False))

    backend = dc_backend()

    result1 = backend.get_user_entitlements("sub-123", "user@example.com", CLAIMS)
    assert result1["can_access"] is True

    result2 = backend.get_user_entitlements(
        "sub-123", "user@example.com", CLAIMS, force_refresh=True
    )
    assert result2["can_access"] is False
    assert len(responses.calls) == 2


@responses.activate
@override_settings(ENTITLEMENTS_CACHE_TIMEOUT=300)
def test_deploycenter_backend_fallback_to_stale_cache():
    """When API fails, should return stale cached value if available."""
    add_dc_answer(dc_answer())

    backend = dc_backend()

    # Populate cache
    backend.get_user_entitlements("sub-123", "user@example.com", CLAIMS)

    # Now API fails
    responses.replace(
        responses.GET,
        DC_URL,
        body=requests.ConnectionError("Connection error"),
    )

    # force_refresh to hit API, but should fall back to cache
    result = backend.get_user_entitlements(
        "sub-123", "user@example.com", CLAIMS, force_refresh=True
    )
    assert result["can_access"] is True


@responses.activate
def test_deploycenter_backend_raises_when_no_cache():
    """When API fails and no cache exists, should raise."""
    responses.add(
        responses.GET,
        DC_URL,
        body=requests.ConnectionError("Connection error"),
    )

    backend = dc_backend()

    with pytest.raises(EntitlementsUnavailableError):
        backend.get_user_entitlements("sub-123", "user@example.com", CLAIMS)


@responses.activate
@override_settings(ENTITLEMENTS_CACHE_TIMEOUT=300)
def test_deploycenter_backend_stale_copy_outlives_the_fresh_one():
    """The fallback still answers once the short-lived entry has expired."""
    add_dc_answer(dc_answer())
    backend = dc_backend()
    backend.get_user_entitlements("sub-123", "user@example.com", CLAIMS)

    # pylint: disable-next=protected-access
    cache.delete(backend._cache_key("sub-123", CLAIMS))
    responses.replace(
        responses.GET, DC_URL, body=requests.ConnectionError("Connection error")
    )

    result = backend.get_user_entitlements("sub-123", "user@example.com", CLAIMS)
    assert result["can_access"] is True


@responses.activate
@override_settings(ENTITLEMENTS_CACHE_TIMEOUT=300)
def test_deploycenter_backend_does_not_serve_the_answer_of_another_organization():
    """An answer for one siret, fresh or stale, is not valid for another siret."""
    add_dc_answer(dc_answer(is_admin=True))
    backend = dc_backend()
    backend.get_user_entitlements("sub-123", "user@example.com", CLAIMS)

    responses.replace(
        responses.GET, DC_URL, body=requests.ConnectionError("Connection error")
    )

    with pytest.raises(EntitlementsUnavailableError):
        backend.get_user_entitlements(
            "sub-123", "user@example.com", user_info={"siret": OTHER_SIRET}
        )


@responses.activate
def test_deploycenter_backend_does_not_share_a_cache_entry_between_subless_users():
    """Users without a sub must not read each other's answer."""
    add_dc_answer(dc_answer())
    add_dc_answer(dc_answer(can_access=False))
    backend = dc_backend()

    first = backend.get_user_entitlements(None, "a@example.com", CLAIMS)
    second = backend.get_user_entitlements(None, "b@example.com", CLAIMS)
    assert first["can_access"] is True
    assert second["can_access"] is False
    assert len(responses.calls) == 2


@responses.activate
def test_deploycenter_backend_sends_oidc_claims():
    """DeployCenter should forward configured OIDC claims."""
    add_dc_answer(dc_answer())

    backend = dc_backend()

    backend.get_user_entitlements(
        "sub-123",
        "user@example.com",
        user_info={"siret": SIRET, "other": "ignored"},
    )

    request = responses.calls[0].request
    assert request.params["siret"] == SIRET
    assert "other" not in request.params


@responses.activate
def test_deploycenter_backend_fails_without_the_organization_claim():
    """DeployCenter refuses a call without a siret, and the backend fails closed."""
    add_dc_answer(dc_answer())

    with pytest.raises(EntitlementsUnavailableError):
        dc_backend().get_user_entitlements("sub-123", "user@example.com")


# -- Auth backend integration --


pytestmark = pytest.mark.django_db


def test_auth_backend_warms_cache_on_login():
    """post_get_or_create_user should call get_user_entitlements with force_refresh."""
    user = factories.UserFactory()
    backend = OIDCAuthenticationBackend()

    with mock.patch(
        "core.authentication.backends.get_user_entitlements",
        return_value={"can_access": True},
    ) as mock_ent:
        backend.post_get_or_create_user(user, {"sub": "x"}, is_new_user=False)
        mock_ent.assert_called_once_with(
            user, user_info={"sub": "x"}, force_refresh=True
        )


def test_auth_backend_login_succeeds_when_access_denied():
    """Login should succeed even when can_access is False (the API gates access)."""
    user = factories.UserFactory()
    backend = OIDCAuthenticationBackend()

    with mock.patch(
        "core.authentication.backends.get_user_entitlements",
        return_value={"can_access": False},
    ):
        # Should not raise: user logs in, the API gates access
        backend.post_get_or_create_user(user, {}, is_new_user=False)


def test_auth_backend_login_succeeds_when_entitlements_unavailable():
    """Login should succeed when entitlements service is unavailable."""
    user = factories.UserFactory()
    backend = OIDCAuthenticationBackend()

    with mock.patch(
        "core.authentication.backends.get_user_entitlements",
        side_effect=EntitlementsUnavailableError("unavailable"),
    ):
        # Should not raise
        backend.post_get_or_create_user(user, {}, is_new_user=False)


# -- UserMeSerializer (can_access field) --


def test_user_me_serializer_includes_can_access_true():
    """UserMeSerializer should include can_access=True when entitled."""
    user = factories.UserFactory()
    with mock.patch(
        "core.api.serializers.get_user_entitlements",
        return_value={"can_access": True},
    ):
        data = UserMeSerializer(user).data
    assert data["can_access"] is True


def test_user_me_serializer_includes_can_access_false():
    """UserMeSerializer should include can_access=False when not entitled."""
    user = factories.UserFactory()
    with mock.patch(
        "core.api.serializers.get_user_entitlements",
        return_value={"can_access": False},
    ):
        data = UserMeSerializer(user).data
    assert data["can_access"] is False


def test_user_me_serializer_can_access_fail_closed():
    """UserMeSerializer should return can_access=False when entitlements unavailable."""
    user = factories.UserFactory()
    with mock.patch(
        "core.api.serializers.get_user_entitlements",
        side_effect=EntitlementsUnavailableError("unavailable"),
    ):
        data = UserMeSerializer(user).data
    assert data["can_access"] is False


@pytest.fixture
def deploycenter(settings):
    """Use the DeployCenter backend, with organizations from the siret claim."""
    settings.ENTITLEMENTS_BACKEND = (
        "core.entitlements.backends.deploycenter.DeployCenterEntitlementsBackend"
    )
    settings.ENTITLEMENTS_BACKEND_PARAMETERS = {
        "base_url": DC_URL,
        "service_id": DC_SERVICE_ID,
        "api_key": "test-key",
        "oidc_claims": ["siret"],
    }
    settings.OIDC_USERINFO_ORGANIZATION_CLAIM = "siret"
    get_entitlements_backend.cache_clear()
    yield
    get_entitlements_backend.cache_clear()


@responses.activate
@pytest.mark.usefixtures("deploycenter")
def test_check_outside_login_sends_the_organization_claim():
    """A check for a stored user sends the siret of the organization of the user."""
    add_dc_answer(dc_answer())
    user = factories.UserFactory(organization__external_id=SIRET)

    assert get_user_entitlements(user)["can_access"] is True
    assert responses.calls[0].request.params == {
        "service_id": str(DC_SERVICE_ID),
        "account_type": "user",
        "account_email": user.email,
        "siret": SIRET,
    }


@responses.activate
@pytest.mark.usefixtures("deploycenter")
def test_deploycenter_administrator_administers_the_organization():
    """An administrator in DeployCenter can write the rows of the organization."""
    add_dc_answer(dc_answer(is_admin=True))
    user = factories.UserFactory(organization__external_id=SIRET)
    group = factories.ContactGroupFactory(owner=None, organization=user.organization)

    assert user_is_admin(user) is True
    assert group.is_writable(user) is True


@responses.activate
@pytest.mark.usefixtures("deploycenter")
def test_api_serves_a_deploycenter_user_outside_login():
    """The API gate and /users/me/ get an answer without a login before them."""
    add_dc_answer(dc_answer(is_admin=True))
    user = factories.UserFactory(organization__external_id=SIRET)
    client = APIClient()
    client.force_login(user)

    assert client.get("/api/v1.0/contacts/").status_code == 200
    me = client.get("/api/v1.0/users/me/").json()
    assert me["can_access"] is True
    assert me["is_admin"] is True
    assert len(responses.calls) == 1


@responses.activate
@pytest.mark.usefixtures("deploycenter")
@pytest.mark.parametrize(
    "check_entitled",
    # pylint: disable-next=protected-access
    [carddav_auth._check_entitled, channel_auth._check_entitled],
    ids=["carddav", "user_channel"],
)
def test_device_and_api_key_checks_send_the_organization_claim(check_entitled):
    """A CardDAV device and a user API key get an answer from DeployCenter."""
    add_dc_answer(dc_answer())
    user = factories.UserFactory(organization__external_id=SIRET)

    check_entitled(user)

    assert responses.calls[0].request.params["siret"] == SIRET


@responses.activate
@pytest.mark.usefixtures("deploycenter")
def test_cached_checks_read_the_organization_one_time(django_assert_num_queries):
    """Many checks for one user object cost one query and one call."""
    add_dc_answer(dc_answer(is_admin=True))
    stored = factories.UserFactory(organization__external_id=SIRET)
    user = models.User.objects.get(pk=stored.pk)

    with django_assert_num_queries(1):
        assert [user_is_admin(user) for _ in range(3)] == [True, True, True]
    assert len(responses.calls) == 1


def test_local_backend_does_not_read_the_organization(
    settings, django_assert_num_queries
):
    """A backend that does not use the claims costs no query."""
    settings.OIDC_USERINFO_ORGANIZATION_CLAIM = "siret"
    stored = factories.UserFactory()
    user = models.User.objects.get(pk=stored.pk)

    with django_assert_num_queries(0):
        assert user_is_admin(user) is False


@responses.activate
@pytest.mark.usefixtures("deploycenter")
def test_deploycenter_fails_closed_for_an_email_domain_organization(settings):
    """An organization from the email domain has no siret to send."""
    settings.OIDC_USERINFO_ORGANIZATION_CLAIM = ""
    add_dc_answer(dc_answer(is_admin=True))
    user = factories.UserFactory()

    with pytest.raises(EntitlementsUnavailableError):
        get_user_entitlements(user)
    assert user_is_admin(user) is False


@responses.activate
@pytest.mark.usefixtures("deploycenter")
def test_login_reads_the_entitlements_of_the_new_organization():
    """The login sends the siret from the identity provider, then the user moves."""
    add_dc_answer(dc_answer(is_admin=True))
    user = factories.UserFactory(organization__external_id=OTHER_SIRET)

    OIDCAuthenticationBackend().post_get_or_create_user(
        user, {"sub": user.sub, "siret": SIRET}, is_new_user=False
    )

    assert responses.calls[0].request.params["siret"] == SIRET
    assert user.organization.external_id == SIRET
    assert user.organization.name == "Mairie de Test"
    # The answer from the login serves the next check.
    assert user_is_admin(user) is True
    assert len(responses.calls) == 1


@responses.activate
@pytest.mark.usefixtures("deploycenter")
def test_login_does_not_reuse_the_answer_of_the_previous_organization():
    """The user moves while DeployCenter is down: the old answer does not follow."""
    add_dc_answer(dc_answer(is_admin=True))
    user = factories.UserFactory(organization__external_id=OTHER_SIRET)
    assert user_is_admin(user) is True

    responses.replace(
        responses.GET, DC_URL, body=requests.ConnectionError("Connection error")
    )
    OIDCAuthenticationBackend().post_get_or_create_user(
        user, {"sub": user.sub, "siret": SIRET}, is_new_user=False
    )

    assert user.organization.external_id == SIRET
    assert user.organization.name == ""
    assert user_is_admin(user) is False
