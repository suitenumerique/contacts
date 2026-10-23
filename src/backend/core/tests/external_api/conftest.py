"""Helpers for /external_api/ tests: a user token, introspected by a fake IdP."""

import time

from django.conf import settings

import pytest
import responses
from rest_framework.test import APIClient

from core import factories, models
from core.enums import Scope

TOKEN = "drive-access-token"


def introspection(
    user, *, audience="drive", active=True, scope="openid email", **extra
):
    """An RFC 7662 introspection response for *user*'s token."""
    return {
        "active": active,
        "sub": user.sub,
        "email": user.email,
        "client_id": audience,
        "scope": scope,
        "exp": int(time.time()) + 300,
        **extra,
    }


def mock_introspection(user, **kwargs):
    """Answer every introspection with *user*'s token info; return the mock."""
    return responses.add(
        responses.POST,
        settings.OIDC_OP_INTROSPECTION_ENDPOINT,
        json=introspection(user, **kwargs),
    )


@pytest.fixture(name="user")
def fixture_user():
    """A user of an organization."""
    return factories.UserFactory(email="alice@brigny.example")


@pytest.fixture(name="api")
def fixture_api(user):
    """A client calling as Drive with *user*'s token."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.add(
            responses.POST,
            settings.OIDC_OP_INTROSPECTION_ENDPOINT,
            json=introspection(user),
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        client.idp = mock
        yield client


def contact(owner=None, *, org=None, emails=("x@example.org",), **fields):
    """A personal contact of *owner*, or an organization one of *org*."""
    if org is not None:
        fields.update(owner=None, organization=org, scope=Scope.ORGANIZATION)
    else:
        fields["owner"] = owner
    return factories.ContactFactory(
        emails=[{"type": "work", "value": email} for email in emails], **fields
    )


def group(owner=None, *, org=None, members=(), **fields):
    """A personal group of *owner*, or an organization one of *org*."""
    if org is not None:
        fields.update(owner=None, organization=org, scope=Scope.ORGANIZATION)
    else:
        fields["owner"] = owner
    row = factories.ContactGroupFactory(**fields)
    for member in members:
        models.ContactGroupMembership.objects.create(group=row, contact=member)
    return row
