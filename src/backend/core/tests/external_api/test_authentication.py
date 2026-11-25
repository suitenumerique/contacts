"""Who may call /external_api/: a user token, issued to an allowed app."""

import time
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings

import pytest
import responses
from requests.exceptions import ConnectTimeout
from rest_framework.test import APIClient

from core import factories
from core.authentication.resource_server import introspection_cache_key

from .conftest import TOKEN, introspection

pytestmark = pytest.mark.django_db

URL = "/external_api/v1.0/search/"


def _client(token=TOKEN):
    # 5xx answers fire got_request_exception (for Sentry), which the test
    # client would re-raise instead of returning the response.
    client = APIClient(raise_request_exception=False)
    if token:
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


@responses.activate
def test_a_token_of_an_allowed_app_is_served(user):
    """A token of an allowed app is served."""
    responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json=introspection(user),
    )

    response = _client().get(URL)

    assert response.status_code == 200
    assert response.json() == {"results": []}
    sent = responses.calls[0].request.body
    assert f"token={TOKEN}" in sent
    assert f"client_id={settings.OIDC_RS_CLIENT_ID}" in sent


def test_no_token_is_refused():
    """No token is refused."""
    assert _client(token=None).get(URL).status_code == 401


@responses.activate
def test_a_token_of_another_app_is_refused(user):
    """A token of another app is refused."""
    responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json=introspection(user, audience="some-other-app"),
    )

    assert _client().get(URL).status_code == 403


@responses.activate
def test_an_inactive_token_gets_401_not_a_400_page():
    """An inactive token gets 401 not a 400 page."""
    responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json={"active": False},
    )

    response = _client().get(URL)

    assert response.status_code == 401
    assert response.json()["errors"][0]["detail"] == "Invalid or expired access token."


@responses.activate
def test_a_token_without_the_required_scope_is_refused(user):
    """A token without the required scope is refused."""
    responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json=introspection(user, scope="email"),
    )

    assert _client().get(URL).status_code == 401


@responses.activate
def test_an_unknown_user_is_refused():
    """An unknown user is refused."""
    stranger = factories.UserFactory.build(sub="never-logged-in")
    responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json=introspection(stranger),
    )

    assert _client().get(URL).status_code == 401


@responses.activate
def test_an_inactive_user_is_refused():
    """An inactive user is refused."""
    responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json=introspection(factories.UserFactory(is_active=False)),
    )

    assert _client().get(URL).status_code == 401


@responses.activate
def test_an_unentitled_user_is_refused(user, monkeypatch):
    """An unentitled user is refused."""
    responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json=introspection(user),
    )
    monkeypatch.setattr(
        "core.api.permissions.get_user_entitlements",
        lambda _user: {"can_access": False},
    )

    assert _client().get(URL).status_code == 403


def test_a_browser_session_is_not_enough(user):
    """A browser session is not enough."""
    client = APIClient()
    client.force_login(user)

    assert client.get(URL).status_code == 401


def test_a_channel_key_is_not_enough(user):
    """A channel key is not enough."""
    channel = factories.ChannelFactory(user=user)
    raw_key = channel.generate_api_key()
    channel.save()

    response = APIClient().get(
        URL, HTTP_X_CHANNEL_ID=str(channel.pk), HTTP_X_API_KEY=raw_key
    )

    assert response.status_code == 401


@responses.activate
def test_an_unreachable_idp_gets_503():
    """An unreachable idp gets 503."""
    responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        body=ConnectTimeout("too slow"),
    )

    assert _client().get(URL).status_code == 503


# Introspection cache


@responses.activate
def test_keystrokes_reuse_the_introspection(user):
    """Keystrokes reuse the introspection."""
    responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json=introspection(user),
    )
    client = _client()

    for query in ("m", "ma", "mar"):
        assert client.get(URL, {"q": query}).status_code == 200

    assert len(responses.calls) == 1


@responses.activate
def test_the_cache_key_is_a_hash_of_the_token(user):
    """The cache key is a hash of the token."""
    responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json=introspection(user),
    )

    _client().get(URL)

    key = introspection_cache_key(TOKEN)
    assert TOKEN not in key
    assert cache.get(key)["sub"] == user.sub


@responses.activate
def test_each_token_is_introspected(user):
    """Each token is introspected."""
    responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json=introspection(user),
    )

    _client("token-1").get(URL)
    _client("token-2").get(URL)

    assert len(responses.calls) == 2


@override_settings(OIDC_RS_INTROSPECTION_CACHE_TIMEOUT=0)
@responses.activate
def test_the_cache_can_be_turned_off(user):
    """The cache can be turned off."""
    responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json=introspection(user),
    )
    client = _client()

    client.get(URL)
    client.get(URL)

    assert len(responses.calls) == 2


@responses.activate
def test_a_refused_token_is_not_cached(user):
    """A refused token is not cached."""
    responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json=introspection(user, audience="some-other-app"),
    )
    responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json={"active": False},
    )
    client = _client()

    client.get(URL)  # 403: the app isn't allowed (checked after the cache)
    assert client.get(URL).status_code == 403

    inactive = _client("inactive-token")
    inactive.get(URL)
    inactive.get(URL)

    assert cache.get(introspection_cache_key("inactive-token")) is None


@pytest.mark.parametrize(
    ("expires_in", "expected_timeout"),
    [(3600, 60), (10, 10), (-5, None)],
)
@responses.activate
def test_a_cached_introspection_never_outlives_its_token(
    user, expires_in, expected_timeout
):
    """A cached introspection never outlives its token."""
    responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json=introspection(user, exp=int(time.time()) + expires_in),
    )

    with mock.patch(
        "core.authentication.resource_server.cache.set", wraps=cache.set
    ) as cache_set:
        response = _client().get(URL)

    ours = [
        call.args
        for call in cache_set.call_args_list
        if call.args[0] == introspection_cache_key(TOKEN)
    ]
    if expected_timeout is None:
        assert response.status_code == 401
        assert not ours
    else:
        assert response.status_code == 200
        assert expected_timeout - 2 <= ours[0][2] <= expected_timeout
