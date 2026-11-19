"""Tests for the organizations feature."""

from django.test import override_settings

import pytest
from rest_framework.status import HTTP_200_OK
from rest_framework.test import APIClient

from core import factories, models
from core.authentication.backends import resolve_organization
from core.entitlements.factory import get_entitlements_backend
from core.models import Organization

# -- Organization model --


@pytest.mark.django_db
def test_organization_str_with_name():
    """Organization.__str__ returns the name when set."""
    org = factories.OrganizationFactory(name="Acme Corp", external_id="acme")
    assert str(org) == "Acme Corp"


@pytest.mark.django_db
def test_organization_str_without_name():
    """Organization.__str__ falls back to external_id when name is empty."""
    org = factories.OrganizationFactory(name="", external_id="acme")
    assert str(org) == "acme"


@pytest.mark.django_db
def test_organization_unique_external_id():
    """external_id must be unique."""
    factories.OrganizationFactory(external_id="org-1")
    with pytest.raises(Exception):  # noqa: B017
        factories.OrganizationFactory(external_id="org-1")


# -- Org resolution on login --


@pytest.mark.django_db
def test_resolve_org_from_email_domain():
    """Without OIDC_USERINFO_ORGANIZATION_CLAIM, org is derived from email domain."""
    user = factories.UserFactory(email="alice@ministry.example")

    resolve_organization(user, claims={}, entitlements={})

    user.refresh_from_db()
    assert user.organization is not None
    assert user.organization.external_id == "ministry.example"
    # No name from the entitlements: none is made up, str() falls back.
    assert user.organization.name == ""
    assert str(user.organization) == "ministry.example"


@pytest.mark.django_db
@override_settings(OIDC_USERINFO_ORGANIZATION_CLAIM="siret")
def test_resolve_org_from_oidc_claim():
    """With OIDC_USERINFO_ORGANIZATION_CLAIM, org is derived from the claim."""
    user = factories.UserFactory(email="alice@ministry.example")

    resolve_organization(
        user,
        claims={"siret": "12345678900011"},
        entitlements={"organization_name": "Ministere X"},
    )

    user.refresh_from_db()
    assert user.organization is not None
    assert user.organization.external_id == "12345678900011"
    assert user.organization.name == "Ministere X"


@pytest.mark.django_db
@override_settings(OIDC_USERINFO_ORGANIZATION_CLAIM="siret")
def test_resolve_org_strict_no_fallback_when_claim_missing():
    """When the claim is configured but absent from OIDC userinfo, no
    fallback to email domain. ``resolve_organization`` leaves the user's
    organization untouched and the caller is expected to fail closed.

    Opting into the claim is opting into strict org identity: silently
    falling back to the email domain would attach users to the wrong org.
    """
    org = factories.OrganizationFactory(external_id="ministry.example")
    user = factories.UserFactory(email="alice@ministry.example", organization=org)

    resolve_organization(user, claims={}, entitlements={})

    user.refresh_from_db()
    # No new organization was created from the email domain.
    assert Organization.objects.filter(external_id="ministry.example").count() == 1
    # And the user's existing org is unchanged.
    assert user.organization_id == org.id


@pytest.mark.django_db
@override_settings(OIDC_USERINFO_ORGANIZATION_CLAIM="siret")
def test_resolve_org_strict_no_fallback_when_claim_empty_string():
    """An empty-string claim value is the same as missing: no fallback."""
    user = factories.UserFactory(email="alice@ministry.example")
    initial_org_id = user.organization_id

    resolve_organization(user, claims={"siret": ""}, entitlements={})

    user.refresh_from_db()
    assert user.organization_id == initial_org_id


@pytest.mark.django_db
def test_resolve_org_updates_name():
    """Org name is updated from entitlements on subsequent logins."""
    org = factories.OrganizationFactory(external_id="example.com", name="Old Name")
    user = factories.UserFactory(email="alice@example.com", organization=org)

    resolve_organization(
        user,
        claims={},
        entitlements={"organization_name": "New Name"},
    )

    org.refresh_from_db()
    assert org.name == "New Name"


@pytest.mark.django_db
@override_settings(OIDC_USERINFO_ORGANIZATION_CLAIM="siret")
def test_resolve_org_keeps_the_name_when_entitlements_have_none():
    """The local backend returns no name: the org name must not become the id."""
    org = factories.OrganizationFactory(
        external_id="12345678900011",
        name="Mairie de Brigny",
    )
    user = factories.UserFactory(organization=org)

    resolve_organization(user, claims={"siret": "12345678900011"}, entitlements={})

    org.refresh_from_db()
    assert org.name == "Mairie de Brigny"


@pytest.mark.django_db
def test_resolve_org_reuses_existing():
    """Existing org is reused, not duplicated."""
    org = factories.OrganizationFactory(external_id="example.com")
    user = factories.UserFactory(email="bob@example.com")

    resolve_organization(user, claims={}, entitlements={})

    user.refresh_from_db()
    assert user.organization_id == org.id
    assert Organization.objects.filter(external_id="example.com").count() == 1


# -- User API: /users/me/ --


@pytest.mark.django_db
@override_settings(
    ENTITLEMENTS_BACKEND="core.entitlements.backends.local.LocalEntitlementsBackend",
    ENTITLEMENTS_BACKEND_PARAMETERS={},
)
def test_users_me_includes_organization():
    """GET /users/me/ includes the user's organization."""
    get_entitlements_backend.cache_clear()
    org = factories.OrganizationFactory(name="Test Org", external_id="test")
    user = factories.UserFactory(organization=org)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get("/api/v1.0/users/me/")

    assert response.status_code == HTTP_200_OK
    data = response.json()
    assert data["organization"]["id"] == str(org.id)
    assert data["organization"]["name"] == "Test Org"
    get_entitlements_backend.cache_clear()


@pytest.mark.django_db
@override_settings(
    ENTITLEMENTS_BACKEND="core.entitlements.backends.local.LocalEntitlementsBackend",
    ENTITLEMENTS_BACKEND_PARAMETERS={},
)
def test_users_me_includes_is_admin():
    """GET /users/me/ includes is_admin from entitlements."""
    get_entitlements_backend.cache_clear()
    user = factories.UserFactory()

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get("/api/v1.0/users/me/")

    assert response.status_code == HTTP_200_OK
    data = response.json()
    assert "is_admin" in data
    # The local backend makes no one an administrator.
    assert data["is_admin"] is False
    get_entitlements_backend.cache_clear()


# -- Organization change --


@pytest.mark.django_db
def test_resolve_org_email_domain_is_case_insensitive():
    """Two spellings of one domain land in one organization."""
    upper = factories.UserFactory(email="alice@Ministry.EXAMPLE")
    lower = factories.UserFactory(email="bob@ministry.example")

    resolve_organization(upper, claims={}, entitlements={})
    resolve_organization(lower, claims={}, entitlements={})

    upper.refresh_from_db()
    lower.refresh_from_db()
    assert upper.organization.external_id == "ministry.example"
    assert upper.organization_id == lower.organization_id


@pytest.mark.django_db
def test_org_change_moves_the_users_address_book():
    """Personal rows follow the user; links to the old org's rows are dropped."""
    old_org = factories.OrganizationFactory(external_id="old.example")
    user = factories.UserFactory(email="alice@new.example", organization=old_org)
    colleague = factories.UserFactory(organization=old_org)

    own = factories.ContactFactory(owner=user)
    org_row = factories.ContactFactory(owner=None, organization=old_org)
    overlay = models.Contact.objects.create(
        owner=user, organization=old_org, overlay_of=org_row
    )
    group = factories.ContactGroupFactory(owner=user)
    models.ContactGroupMembership.objects.create(group=group, contact=own)
    models.ContactGroupMembership.objects.create(group=group, contact=org_row)
    org_group = factories.ContactGroupFactory(owner=None, organization=old_org)
    models.ContactGroupMembership.objects.create(group=org_group, contact=own)
    org_row.favorited_by.add(user)
    label = models.ContactLabel.objects.create(owner=user, name="Kept")
    own.labels.add(label)
    channel = factories.ChannelFactory(user=user)
    theirs = factories.ContactFactory(owner=colleague)

    resolve_organization(user, claims={}, entitlements={})

    user.refresh_from_db()
    new_org = user.organization
    assert new_org.external_id == "new.example"
    own.refresh_from_db()
    assert own.organization_id == new_org.id
    assert list(own.labels.all()) == [label]
    group.refresh_from_db()
    assert group.organization_id == new_org.id
    assert list(group.members.all()) == [own]
    assert not org_group.members.exists()
    assert not models.Contact.objects.filter(pk=overlay.pk).exists()
    assert not org_row.favorited_by.exists()
    channel.refresh_from_db()
    assert channel.organization_id == new_org.id
    theirs.refresh_from_db()
    assert theirs.organization_id == old_org.id

    # The moved contacts are visible in the new organization.
    client = APIClient()
    client.force_login(user)
    listed = client.get("/api/v1.0/contacts/").json()["results"]
    assert [row["id"] for row in listed] == [str(own.pk)]
