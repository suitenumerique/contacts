"""Tests for the entitlements module."""

from unittest import mock

from django.core.cache import cache
from django.test import override_settings

import pytest
import requests
import responses

from core import factories
from core.api.serializers import UserMeSerializer
from core.authentication.backends import OIDCAuthenticationBackend
from core.entitlements import EntitlementsUnavailableError, get_user_entitlements
from core.entitlements.backends.deploycenter import DeployCenterEntitlementsBackend
from core.entitlements.backends.local import LocalEntitlementsBackend
from core.entitlements.factory import get_entitlements_backend

# -- LocalEntitlementsBackend --


def test_local_backend_grants_access_but_not_admin():
    """The local backend lets everyone in and makes no one an administrator."""
    backend = LocalEntitlementsBackend()
    result = backend.get_user_entitlements("sub-123", "user@example.com")
    assert result == {"can_access": True, "can_admin": False}


def test_local_backend_ignores_parameters():
    """The local backend should work regardless of parameters passed."""
    backend = LocalEntitlementsBackend()
    result = backend.get_user_entitlements(
        "sub-123",
        "user@example.com",
        user_info={"some": "claim"},
        force_refresh=True,
    )
    assert result == {"can_access": True, "can_admin": False}


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
    result = get_user_entitlements("sub-123", "user@example.com")
    assert result["can_access"] is True
    get_entitlements_backend.cache_clear()


# -- DeployCenterEntitlementsBackend --

DC_URL = "https://deploy.example.com/api/v1.0/entitlements/"


@responses.activate
def test_deploycenter_backend_grants_access():
    """DeployCenter backend should return can_access from API response."""
    responses.add(
        responses.GET,
        DC_URL,
        json={"entitlements": {"can_access": True, "can_admin": True}},
        status=200,
    )

    backend = DeployCenterEntitlementsBackend(
        base_url=DC_URL,
        service_id="contacts",
        api_key="test-key",
    )
    result = backend.get_user_entitlements("sub-123", "user@example.com")
    assert result == {"can_access": True, "can_admin": True}

    # Verify request was made with correct params and header
    assert len(responses.calls) == 1
    request = responses.calls[0].request
    assert "service_id=contacts" in request.url
    assert "account_email=user%40example.com" in request.url
    assert request.headers["X-Service-Auth"] == "Bearer test-key"


@responses.activate
def test_deploycenter_backend_denies_access():
    """DeployCenter backend should return can_access=False when API says so."""
    responses.add(
        responses.GET,
        DC_URL,
        json={"entitlements": {"can_access": False}},
        status=200,
    )

    backend = DeployCenterEntitlementsBackend(
        base_url=DC_URL,
        service_id="contacts",
        api_key="test-key",
    )
    result = backend.get_user_entitlements("sub-123", "user@example.com")
    assert result == {"can_access": False, "can_admin": False}


@responses.activate
@override_settings(ENTITLEMENTS_CACHE_TIMEOUT=300)
def test_deploycenter_backend_uses_cache():
    """DeployCenter should use cached results when not force_refresh."""
    responses.add(
        responses.GET,
        DC_URL,
        json={"entitlements": {"can_access": True}},
        status=200,
    )

    backend = DeployCenterEntitlementsBackend(
        base_url=DC_URL,
        service_id="contacts",
        api_key="test-key",
    )

    # First call hits the API
    result1 = backend.get_user_entitlements("sub-123", "user@example.com")
    assert result1["can_access"] is True
    assert len(responses.calls) == 1

    # Second call should use cache
    result2 = backend.get_user_entitlements("sub-123", "user@example.com")
    assert result2["can_access"] is True
    assert len(responses.calls) == 1  # No additional API call


@responses.activate
@override_settings(ENTITLEMENTS_CACHE_TIMEOUT=300)
def test_deploycenter_backend_force_refresh_bypasses_cache():
    """force_refresh=True should bypass cache and hit the API."""
    responses.add(
        responses.GET,
        DC_URL,
        json={"entitlements": {"can_access": True}},
        status=200,
    )
    responses.add(
        responses.GET,
        DC_URL,
        json={"entitlements": {"can_access": False}},
        status=200,
    )

    backend = DeployCenterEntitlementsBackend(
        base_url=DC_URL,
        service_id="contacts",
        api_key="test-key",
    )

    result1 = backend.get_user_entitlements("sub-123", "user@example.com")
    assert result1["can_access"] is True

    result2 = backend.get_user_entitlements(
        "sub-123", "user@example.com", force_refresh=True
    )
    assert result2["can_access"] is False
    assert len(responses.calls) == 2


@responses.activate
@override_settings(ENTITLEMENTS_CACHE_TIMEOUT=300)
def test_deploycenter_backend_fallback_to_stale_cache():
    """When API fails, should return stale cached value if available."""
    responses.add(
        responses.GET,
        DC_URL,
        json={"entitlements": {"can_access": True}},
        status=200,
    )

    backend = DeployCenterEntitlementsBackend(
        base_url=DC_URL,
        service_id="contacts",
        api_key="test-key",
    )

    # Populate cache
    backend.get_user_entitlements("sub-123", "user@example.com")

    # Now API fails
    responses.replace(
        responses.GET,
        DC_URL,
        body=requests.ConnectionError("Connection error"),
    )

    # force_refresh to hit API, but should fall back to cache
    result = backend.get_user_entitlements(
        "sub-123", "user@example.com", force_refresh=True
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

    backend = DeployCenterEntitlementsBackend(
        base_url=DC_URL,
        service_id="contacts",
        api_key="test-key",
    )

    with pytest.raises(EntitlementsUnavailableError):
        backend.get_user_entitlements("sub-123", "user@example.com")


@responses.activate
@override_settings(ENTITLEMENTS_CACHE_TIMEOUT=300)
def test_deploycenter_backend_stale_copy_outlives_the_fresh_one():
    """The fallback still answers once the short-lived entry has expired."""
    responses.add(
        responses.GET, DC_URL, json={"entitlements": {"can_access": True}}, status=200
    )
    backend = DeployCenterEntitlementsBackend(
        base_url=DC_URL, service_id="contacts", api_key="test-key"
    )
    backend.get_user_entitlements("sub-123", "user@example.com")

    cache.delete(backend._cache_key("sub-123"))  # pylint: disable=protected-access
    responses.replace(
        responses.GET, DC_URL, body=requests.ConnectionError("Connection error")
    )

    result = backend.get_user_entitlements("sub-123", "user@example.com")
    assert result["can_access"] is True


@responses.activate
def test_deploycenter_backend_does_not_share_a_cache_entry_between_subless_users():
    """Users without a sub must not read each other's answer."""
    responses.add(
        responses.GET, DC_URL, json={"entitlements": {"can_access": True}}, status=200
    )
    responses.add(
        responses.GET, DC_URL, json={"entitlements": {"can_access": False}}, status=200
    )
    backend = DeployCenterEntitlementsBackend(
        base_url=DC_URL, service_id="contacts", api_key="test-key"
    )

    assert backend.get_user_entitlements(None, "a@example.com")["can_access"] is True
    assert backend.get_user_entitlements(None, "b@example.com")["can_access"] is False
    assert len(responses.calls) == 2


@responses.activate
def test_deploycenter_backend_sends_oidc_claims():
    """DeployCenter should forward configured OIDC claims."""
    responses.add(
        responses.GET,
        DC_URL,
        json={"entitlements": {"can_access": True}},
        status=200,
    )

    backend = DeployCenterEntitlementsBackend(
        base_url=DC_URL,
        service_id="contacts",
        api_key="test-key",
        oidc_claims=["organization"],
    )

    backend.get_user_entitlements(
        "sub-123",
        "user@example.com",
        user_info={"organization": "org-42", "other": "ignored"},
    )

    request = responses.calls[0].request
    assert "organization=org-42" in request.url
    assert "other" not in request.url


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
            user_sub=user.sub,
            user_email=user.email,
            user_info={"sub": "x"},
            force_refresh=True,
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
