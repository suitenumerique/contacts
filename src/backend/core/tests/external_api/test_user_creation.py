"""The first call of a person who never logged in to Contacts."""

from unittest import mock

from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError

import pytest
import responses
from lasuite.oidc_resource_server.backend import ResourceServerBackend
from requests.exceptions import ConnectTimeout
from rest_framework.test import APIClient

from core import factories, models

from .conftest import TOKEN, introspection

pytestmark = pytest.mark.django_db

URL = "/external_api/v1.0/search/"
USERINFO_URL = "https://oidc.test/realms/test/userinfo"
SUB = "never-logged-in"
EMAIL = "bob@brigny.example"


@pytest.fixture(autouse=True)
def fixture_create_users(settings):
    """Turn the creation of users on, with a fake userinfo endpoint."""
    settings.OIDC_RS_CREATE_USER = True
    settings.OIDC_OP_USER_ENDPOINT = USERINFO_URL


def _client():
    # 5xx answers fire got_request_exception (for Sentry), which the test
    # client would re-raise instead of returning the response.
    client = APIClient(raise_request_exception=False)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
    return client


def _mock_token(audience="drive"):
    """Introspect the token as the token of a person unknown to Contacts."""
    stranger = factories.UserFactory.build(sub=SUB, email=EMAIL)
    responses.add(
        responses.POST,
        django_settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json=introspection(stranger, audience=audience),
    )


@responses.activate
def test_the_first_call_creates_the_user():
    """The first call creates the user."""
    _mock_token()
    responses.add(responses.GET, USERINFO_URL, json={"sub": SUB, "email": EMAIL})
    client = _client()

    assert client.get(URL).status_code == 200
    assert client.get(URL).status_code == 200

    user = models.User.objects.get(sub=SUB)
    assert user.email == EMAIL
    assert user.organization.external_id == "brigny.example"
    assert user.contact_labels.count() == len(models.DEFAULT_LABELS)
    responses.assert_call_count(USERINFO_URL, 1)


@responses.activate
def test_an_app_that_is_not_allowed_creates_no_user():
    """An app that is not allowed creates no user."""
    _mock_token(audience="some-other-app")

    assert _client().get(URL).status_code == 401
    assert not models.User.objects.filter(sub=SUB).exists()


@responses.activate
def test_a_person_without_an_organization_is_refused(settings):
    """A person without an organization is refused."""
    settings.OIDC_USERINFO_ORGANIZATION_CLAIM = "siret"
    _mock_token()
    responses.add(responses.GET, USERINFO_URL, json={"sub": SUB, "email": EMAIL})

    assert _client().get(URL).status_code == 401
    assert not models.User.objects.filter(sub=SUB).exists()


@responses.activate
def test_an_unreachable_userinfo_gets_503():
    """An unreachable userinfo gets 503."""
    _mock_token()
    responses.add(responses.GET, USERINFO_URL, body=ConnectTimeout("too slow"))

    assert _client().get(URL).status_code == 503
    assert not models.User.objects.filter(sub=SUB).exists()


@pytest.mark.parametrize(
    "error",
    [ValidationError("User with this Sub already exists."), IntegrityError("sub")],
)
@responses.activate
def test_a_concurrent_first_call_is_served(user, error):
    """A concurrent first call is served."""
    responses.add(
        responses.POST,
        django_settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json=introspection(user),
    )

    # The other call created the user after the lookup of this call. The
    # creation then fails on the unique sub, in the validation or in the insert.
    with mock.patch.object(
        ResourceServerBackend, "get_or_create_user", side_effect=error
    ):
        assert _client().get(URL).status_code == 200
