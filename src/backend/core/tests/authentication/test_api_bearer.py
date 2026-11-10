"""Bearer access tokens on the main API."""

import pytest
from rest_framework.test import APIClient

from core import factories
from core.authentication.backends import OIDCAuthenticationBackend

pytestmark = pytest.mark.django_db


def test_api_refuses_a_bearer_access_token(monkeypatch):
    """An access token is not a login here, whatever the identity provider says.

    The userinfo endpoint answers for a token issued to any client, so it
    cannot tell which application presents the token. Other applications
    use /external_api/, which checks the audience.
    """
    user = factories.UserFactory()
    userinfo_calls = []

    def get_userinfo_mocked(*args):
        userinfo_calls.append(args)
        return {"sub": user.sub, "email": user.email}

    monkeypatch.setattr(OIDCAuthenticationBackend, "get_userinfo", get_userinfo_mocked)

    client = APIClient()
    response = client.get(
        "/api/v1.0/users/me/", HTTP_AUTHORIZATION="Bearer a-valid-access-token"
    )

    assert response.status_code == 401
    assert not userinfo_calls
