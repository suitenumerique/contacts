"""Tests for the contact groups API."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument,too-many-arguments

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core import factories, models
from core.entitlements import EntitlementsUnavailableError
from core.entitlements.backends.local import LocalEntitlementsBackend

pytestmark = pytest.mark.django_db

GROUPS_URL = "/api/v1.0/groups/"


@pytest.fixture
def user():
    return factories.UserFactory()


@pytest.fixture
def client(user):
    client = APIClient()
    client.force_login(user)
    return client


def test_anonymous_cannot_list_groups():
    assert APIClient().get(GROUPS_URL).status_code == 401


def test_create_list_groups(client, user):
    response = client.post(
        GROUPS_URL,
        {"name": "Équipe projet", "description": "Le projet X"},
        format="json",
    )
    assert response.status_code == 201
    factories.ContactGroupFactory(
        owner=None, organization=user.organization, name="Org-wide"
    )
    factories.ContactGroupFactory(name="Foreign group")  # other org

    response = client.get(GROUPS_URL)
    names = {g["name"] for g in response.json()}
    assert names == {"Équipe projet", "Org-wide"}


def test_membership_lifecycle(client, user):
    group = factories.ContactGroupFactory(owner=user, name="Team")
    contact = factories.ContactFactory(owner=user, full_name="Member One")

    response = client.post(
        f"{GROUPS_URL}{group.id}/add-contact/",
        {"contact_id": str(contact.id)},
        format="json",
    )
    assert response.status_code == 204

    members = client.get(f"{GROUPS_URL}{group.id}/contacts/").json()
    assert [m["full_name"] for m in members] == ["Member One"]

    counted = client.get(GROUPS_URL).json()
    assert counted[0]["members_count"] == 1

    response = client.post(
        f"{GROUPS_URL}{group.id}/remove-contact/",
        {"contact_id": str(contact.id)},
        format="json",
    )
    assert response.status_code == 204
    assert client.get(f"{GROUPS_URL}{group.id}/contacts/").json() == []


def test_cannot_add_foreign_contact(client, user):
    group = factories.ContactGroupFactory(owner=user)
    foreign_contact = factories.ContactFactory()  # other org

    response = client.post(
        f"{GROUPS_URL}{group.id}/add-contact/",
        {"contact_id": str(foreign_contact.id)},
        format="json",
    )
    assert response.status_code == 404


@pytest.fixture
def admins(monkeypatch):
    """The subs of the users ``can_admin`` grants; everyone else is not."""
    subs = set()

    def entitlements(self, user_sub, user_email, user_info=None, force_refresh=False):  # pylint: disable=unused-argument
        return {"can_access": True, "can_admin": user_sub in subs}

    monkeypatch.setattr(LocalEntitlementsBackend, "get_user_entitlements", entitlements)
    return subs


def test_org_groups_read_only_for_non_admins(client, user, admins):
    group = factories.ContactGroupFactory(
        owner=None, organization=user.organization, name="Org-wide"
    )
    assert client.get(f"{GROUPS_URL}{group.id}/").json()["is_writable"] is False
    assert (
        client.patch(f"{GROUPS_URL}{group.id}/", {"name": "Renamed"}, format="json")
    ).status_code == 403
    assert client.delete(f"{GROUPS_URL}{group.id}/").status_code == 403
    contact = factories.ContactFactory(owner=user)
    response = client.post(
        f"{GROUPS_URL}{group.id}/add-contact/",
        {"contact_id": str(contact.id)},
        format="json",
    )
    assert response.status_code == 403
    response = client.post(
        GROUPS_URL, {"name": "Shared", "is_org_group": True}, format="json"
    )
    assert response.status_code == 403
    assert not models.ContactGroup.objects.filter(name="Shared").exists()


def test_admin_creates_and_edits_org_groups(client, user, admins):
    admins.add(user.sub)
    colleague = factories.UserFactory(organization=user.organization)
    colleague_client = APIClient()
    colleague_client.force_login(colleague)

    response = client.post(
        GROUPS_URL, {"name": "Direction", "is_org_group": True}, format="json"
    )
    assert response.status_code == 201, response.content
    data = response.json()
    assert (data["is_org_group"], data["is_writable"]) == (True, True)
    group = models.ContactGroup.objects.get(pk=data["id"])
    assert (group.owner_id, group.organization_id) == (None, user.organization_id)

    url = f"{GROUPS_URL}{group.id}/"
    assert (
        client.patch(url, {"name": "Direction générale"}, format="json").status_code
        == 200
    )
    # Colleagues see it, read-only.
    seen = colleague_client.get(url).json()
    assert (seen["name"], seen["is_writable"]) == ("Direction générale", False)

    assert client.delete(url).status_code == 204


def test_admin_cannot_edit_another_organization_group(client, user, admins):
    admins.add(user.sub)
    elsewhere = factories.ContactGroupFactory(
        owner=None, organization=factories.OrganizationFactory()
    )
    assert elsewhere.is_writable(user) is False
    response = client.patch(
        f"{GROUPS_URL}{elsewhere.id}/", {"name": "Taken"}, format="json"
    )
    assert response.status_code == 404


def test_org_group_takes_only_shared_contacts(client, user, admins):
    admins.add(user.sub)
    group = factories.ContactGroupFactory(owner=None, organization=user.organization)
    org_contact = factories.ContactFactory(owner=None, organization=user.organization)
    overlay = models.Contact.objects.create(
        owner=user, organization=user.organization, overlay_of=org_contact
    )
    personal = factories.ContactFactory(owner=user)

    def add(contact):
        return client.post(
            f"{GROUPS_URL}{group.id}/add-contact/",
            {"contact_id": str(contact.id)},
            format="json",
        )

    assert add(personal).status_code == 400
    assert add(overlay).status_code == 204
    assert list(group.members.all()) == [org_contact]


def test_group_names_are_unique_where_they_are_listed(client, user, admins):
    admins.add(user.sub)
    client.post(GROUPS_URL, {"name": "Équipe", "is_org_group": True}, format="json")

    shared = client.post(
        GROUPS_URL, {"name": "équipe", "is_org_group": True}, format="json"
    )
    assert shared.status_code == 400
    # A personal group is listed apart from the organization's.
    personal = client.post(GROUPS_URL, {"name": "Équipe"}, format="json")
    assert personal.status_code == 201


def test_a_group_sharing_cannot_change(client, user, admins):
    admins.add(user.sub)
    personal = factories.ContactGroupFactory(owner=user)
    shared = factories.ContactGroupFactory(owner=None, organization=user.organization)

    for group, value in ((personal, True), (shared, False)):
        response = client.patch(
            f"{GROUPS_URL}{group.id}/", {"is_org_group": value}, format="json"
        )
        assert response.status_code == 400
        group.refresh_from_db()
        assert group.is_org_group is not value


def test_org_groups_are_read_only_when_entitlements_are_down(user, monkeypatch):
    def unavailable(self, *args, **kwargs):
        raise EntitlementsUnavailableError

    monkeypatch.setattr(LocalEntitlementsBackend, "get_user_entitlements", unavailable)
    group = factories.ContactGroupFactory(owner=None, organization=user.organization)
    assert group.is_writable(user) is False


def test_group_contacts_overlay_and_parent_both_members_dedup(client, user):
    """A logical contact appears once even if parent and overlay are members."""
    org = user.organization
    parent = models.Contact.objects.create(
        owner=None,
        organization=org,
        source="directory",
        external_id="uid=jean",
        full_name="Jean Dupont",
        emails=[{"type": "work", "value": "jean@example.org"}],
    )
    overlay = models.Contact.objects.create(
        owner=user,
        organization=org,
        overlay_of=parent,
        full_name="Jean (local)",
        overridden_fields=["full_name"],
    )
    group = factories.ContactGroupFactory(owner=user)
    models.ContactGroupMembership.objects.create(group=group, contact=parent)
    models.ContactGroupMembership.objects.create(group=group, contact=overlay)

    members = client.get(f"{GROUPS_URL}{group.id}/contacts/").json()
    assert [m["id"] for m in members] == [str(overlay.id)]


def test_org_group_hides_other_users_personal_members(client, user):
    """A provisioned org-wide group may hold rows the user cannot see."""
    org = user.organization
    group = factories.ContactGroupFactory(owner=None, organization=org, name="Org")
    colleague = factories.UserFactory(organization=org)
    theirs = factories.ContactFactory(owner=colleague, full_name="Private")
    mine = factories.ContactFactory(owner=user, full_name="Mine")
    shared = factories.ContactFactory(owner=None, organization=org, full_name="Org")
    for contact in (theirs, mine, shared):
        models.ContactGroupMembership.objects.create(group=group, contact=contact)

    listed = client.get(GROUPS_URL).json()
    assert listed[0]["members_count"] == 2
    members = client.get(f"{GROUPS_URL}{group.id}/contacts/").json()
    assert {m["id"] for m in members} == {str(mine.id), str(shared.id)}


def test_members_count_excludes_trashed(client, user):
    group = factories.ContactGroupFactory(owner=user)
    kept = factories.ContactFactory(owner=user)
    trashed = factories.ContactFactory(owner=user)
    models.ContactGroupMembership.objects.create(group=group, contact=kept)
    models.ContactGroupMembership.objects.create(group=group, contact=trashed)
    models.Contact.objects.filter(pk=trashed.pk).update(deleted_at=timezone.now())

    counted = client.get(GROUPS_URL).json()
    assert counted[0]["members_count"] == 1
    members = client.get(f"{GROUPS_URL}{group.id}/contacts/").json()
    assert [m["id"] for m in members] == [str(kept.id)]


def test_cannot_add_trashed_contact(client, user):
    group = factories.ContactGroupFactory(owner=user)
    contact = factories.ContactFactory(owner=user)
    models.Contact.objects.filter(pk=contact.pk).update(deleted_at=timezone.now())

    response = client.post(
        f"{GROUPS_URL}{group.id}/add-contact/",
        {"contact_id": str(contact.id)},
        format="json",
    )
    assert response.status_code == 404
    assert group.members.count() == 0


def test_delete_group_keeps_contacts(client, user):
    group = factories.ContactGroupFactory(owner=user)
    contact = factories.ContactFactory(owner=user)
    models.ContactGroupMembership.objects.create(group=group, contact=contact)

    assert client.delete(f"{GROUPS_URL}{group.id}/").status_code == 204
    contact.refresh_from_db()  # still alive
    assert models.Contact.objects.filter(pk=contact.pk).exists()


# --- duplicate names -------------------------------------------------


def test_cannot_create_two_groups_with_the_same_name(client, user):
    """Two identically named groups are indistinguishable in the sidebar."""
    client.post(GROUPS_URL, {"name": "Partenaires"}, format="json")

    response = client.post(GROUPS_URL, {"name": "Partenaires"}, format="json")

    assert response.status_code == 400
    assert [error["attr"] for error in response.json()["errors"]] == ["name"]
    assert models.ContactGroup.objects.filter(owner=user).count() == 1


def test_duplicate_group_name_check_ignores_case_and_spacing(client, user):
    client.post(GROUPS_URL, {"name": "Équipe Test"}, format="json")

    response = client.post(GROUPS_URL, {"name": "  équipe test  "}, format="json")

    assert response.status_code == 400
    assert models.ContactGroup.objects.filter(owner=user).count() == 1


def test_renaming_a_group_onto_another_name_is_refused(client, user):
    first = factories.ContactGroupFactory(owner=user, organization=user.organization)
    second = factories.ContactGroupFactory(owner=user, organization=user.organization)

    response = client.patch(
        f"{GROUPS_URL}{second.pk}/", {"name": first.name}, format="json"
    )

    assert response.status_code == 400
    second.refresh_from_db()
    assert second.name != first.name


def test_a_group_can_keep_its_own_name_when_edited(client, user):
    group = factories.ContactGroupFactory(owner=user, organization=user.organization)

    response = client.patch(
        f"{GROUPS_URL}{group.pk}/",
        {"name": group.name, "description": "Updated"},
        format="json",
    )

    assert response.status_code == 200
    group.refresh_from_db()
    assert group.description == "Updated"


def test_concurrent_duplicate_group_is_a_400(client, user, monkeypatch):
    """The name check and the insert can race; the constraint then answers."""
    factories.ContactGroupFactory(owner=user, name="Team")
    monkeypatch.setattr(
        "core.api.serializers.ContactGroupSerializer.validate_name",
        lambda self, value: value,
    )
    monkeypatch.setattr(
        models.ContactGroup, "validate_constraints", lambda self, exclude=None: None
    )

    response = client.post(GROUPS_URL, {"name": "Team"}, format="json")

    assert response.status_code == 400
    assert models.ContactGroup.objects.filter(owner=user).count() == 1


# --- memberships and overlays ------------------------------------------------


def _org_row(user, **kwargs):
    return models.Contact.objects.create(
        owner=None,
        organization=user.organization,
        source="directory",
        external_id=kwargs.pop("external_id", "uid=jean"),
        full_name=kwargs.pop("full_name", "Jean Dupont"),
        emails=[{"type": "work", "value": "jean@example.org"}],
    )


def _overlay(user, parent):
    return models.Contact.objects.create(
        owner=user, organization=user.organization, overlay_of=parent
    )


def test_adding_an_overlay_stores_the_parent(client, user):
    parent = _org_row(user)
    overlay = _overlay(user, parent)
    group = factories.ContactGroupFactory(owner=user)

    response = client.post(
        f"{GROUPS_URL}{group.id}/add-contact/",
        {"contact_id": str(overlay.id)},
        format="json",
    )

    assert response.status_code == 204
    assert list(group.members.all()) == [parent]
    members = client.get(f"{GROUPS_URL}{group.id}/contacts/").json()
    assert [m["id"] for m in members] == [str(overlay.id)]
    detail = client.get(f"/api/v1.0/contacts/{overlay.id}/").json()
    assert detail["group_ids"] == [str(group.id)]


def test_removing_an_overlay_removes_the_parent_membership(client, user):
    parent = _org_row(user)
    overlay = _overlay(user, parent)
    group = factories.ContactGroupFactory(owner=user)
    models.ContactGroupMembership.objects.create(group=group, contact=parent)
    models.ContactGroupMembership.objects.create(group=group, contact=overlay)

    response = client.post(
        f"{GROUPS_URL}{group.id}/remove-contact/",
        {"contact_id": str(overlay.id)},
        format="json",
    )

    assert response.status_code == 204
    assert not group.members.exists()


def test_members_count_counts_an_overlay_and_its_parent_once(client, user):
    parent = _org_row(user)
    overlay = _overlay(user, parent)
    other = factories.ContactFactory(owner=user)
    group = factories.ContactGroupFactory(owner=user)
    for contact in (parent, overlay, other):
        models.ContactGroupMembership.objects.create(group=group, contact=contact)

    counted = client.get(GROUPS_URL).json()
    assert counted[0]["members_count"] == 2
    assert len(client.get(f"{GROUPS_URL}{group.id}/contacts/").json()) == 2


def test_group_member_list_hides_other_users_groups_from_group_ids(client, user):
    parent = _org_row(user)
    colleague = factories.UserFactory(organization=user.organization)
    theirs = factories.ContactGroupFactory(owner=colleague)
    org_group = factories.ContactGroupFactory(
        owner=None, organization=user.organization
    )
    models.ContactGroupMembership.objects.create(group=theirs, contact=parent)
    models.ContactGroupMembership.objects.create(group=org_group, contact=parent)

    members = client.get(f"{GROUPS_URL}{org_group.id}/contacts/").json()
    assert members[0]["group_ids"] == [str(org_group.id)]


def test_malformed_member_id_is_400(client, user):
    group = factories.ContactGroupFactory(owner=user)
    response = client.post(
        f"{GROUPS_URL}{group.id}/add-contact/", {"contact_id": "nope"}, format="json"
    )
    assert response.status_code == 400


def test_another_user_may_reuse_the_name(user):
    factories.ContactGroupFactory(
        owner=user, organization=user.organization, name="Team"
    )
    other = factories.UserFactory()
    client = APIClient()
    client.force_login(other)

    response = client.post(GROUPS_URL, {"name": "Team"}, format="json")

    assert response.status_code == 201
