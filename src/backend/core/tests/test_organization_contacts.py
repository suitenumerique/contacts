"""The organization's shared address book: scope, administrators, sharing."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument

from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core import factories, models
from core.entitlements.backends.local import LocalEntitlementsBackend
from core.enums import Scope
from core.tasks import import_contacts

pytestmark = pytest.mark.django_db

CONTACTS_URL = "/api/v1.0/contacts/"


@pytest.fixture
def admins(monkeypatch):
    """The subs of the users ``can_admin`` grants; everyone else is not."""
    subs = set()

    def entitlements(self, user_sub, user_email, user_info=None, force_refresh=False):  # pylint: disable=unused-argument
        return {"can_access": True, "can_admin": user_sub in subs}

    monkeypatch.setattr(LocalEntitlementsBackend, "get_user_entitlements", entitlements)
    return subs


@pytest.fixture
def admin(admins):
    user = factories.UserFactory()
    admins.add(user.sub)
    return user


@pytest.fixture
def colleague(admin):
    return factories.UserFactory(organization=admin.organization)


def client_for(user):
    client = APIClient()
    client.force_login(user)
    return client


def org_contact(organization, **kwargs):
    return factories.ContactFactory(owner=None, organization=organization, **kwargs)


# --- scope ------------------------------------------------------------------


def test_scope_follows_the_owner():
    personal = factories.ContactFactory()
    shared = org_contact(personal.organization)
    assert (personal.scope, shared.scope) == (Scope.PERSONAL, Scope.ORGANIZATION)
    group = factories.ContactGroupFactory(
        owner=None, organization=personal.organization
    )
    assert group.scope == Scope.ORGANIZATION


@pytest.mark.parametrize("model", [models.Contact, models.ContactGroup])
def test_the_database_refuses_a_scope_that_contradicts_the_owner(model):
    factory = (
        factories.ContactFactory
        if model is models.Contact
        else factories.ContactGroupFactory
    )
    row = factory()
    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.filter(pk=row.pk).update(scope=Scope.ORGANIZATION)


def test_visible_to_is_own_rows_and_the_organization(admin, colleague):
    mine = factories.ContactFactory(owner=colleague)
    theirs = factories.ContactFactory(
        owner=factories.UserFactory(organization=admin.organization)
    )
    shared = org_contact(admin.organization)
    elsewhere = org_contact(factories.OrganizationFactory())

    visible = set(models.Contact.objects.visible_to(colleague))
    assert visible == {mine, shared}
    assert theirs not in visible and elsewhere not in visible


# --- administrators write the shared address book ----------------------------


def test_an_administrator_creates_and_edits_organization_contacts(admin, colleague):
    response = client_for(admin).post(
        CONTACTS_URL,
        {
            "full_name": "Accueil de la mairie",
            "emails": [{"type": "work", "value": "accueil@example.org"}],
            "is_org_contact": True,
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    assert (response.json()["is_org_contact"], response.json()["is_writable"]) == (
        True,
        True,
    )
    contact = models.Contact.objects.get(pk=response.json()["id"])
    assert (contact.owner, contact.scope) == (None, Scope.ORGANIZATION)
    assert (contact.created_by, contact.updated_by) == (admin, admin)

    url = f"{CONTACTS_URL}{contact.id}/"
    seen = client_for(colleague).get(url).json()
    assert (seen["full_name"], seen["is_writable"]) == ("Accueil de la mairie", False)
    assert (
        client_for(colleague)
        .patch(url, {"job_title": "Accueil"}, format="json")
        .status_code
        == 403
    )

    assert (
        client_for(admin)
        .patch(url, {"job_title": "Accueil"}, format="json")
        .status_code
        == 200
    )
    contact.refresh_from_db()
    assert contact.job_title == "Accueil"


def test_only_an_administrator_creates_an_organization_contact(colleague):
    response = client_for(colleague).post(
        CONTACTS_URL,
        {"full_name": "X", "phones": [{"value": "0199000405"}], "is_org_contact": True},
        format="json",
    )
    assert response.status_code == 403
    assert not models.Contact.objects.exists()


def test_a_synced_organization_contact_stays_read_only(admin):
    synced = org_contact(admin.organization, source="directory", external_id="1")
    url = f"{CONTACTS_URL}{synced.id}/"
    assert (
        client_for(admin).patch(url, {"job_title": "x"}, format="json").status_code
        == 403
    )


def test_organization_contacts_carry_no_labels(admin):
    label = models.ContactLabel.objects.create(owner=admin, name="Agent")
    contact = org_contact(admin.organization)
    response = client_for(admin).patch(
        f"{CONTACTS_URL}{contact.id}/", {"label_ids": [str(label.id)]}, format="json"
    )
    assert response.status_code == 400


def test_a_contact_sharing_cannot_change(admin):
    personal = factories.ContactFactory(owner=admin)
    shared = org_contact(admin.organization)
    for contact, value in ((personal, True), (shared, False)):
        response = client_for(admin).patch(
            f"{CONTACTS_URL}{contact.id}/", {"is_org_contact": value}, format="json"
        )
        assert response.status_code == 400


# --- the organization's trash -------------------------------------------------


def test_the_organization_trash_is_the_administrators(admin, colleague):
    contact = org_contact(admin.organization, full_name="Gone")
    url = f"{CONTACTS_URL}{contact.id}/"

    assert client_for(admin).delete(url).status_code == 204
    contact.refresh_from_db()
    assert contact.deleted_at is not None
    assert contact.updated_by == admin

    trash = client_for(admin).get(CONTACTS_URL, {"trash": "true"}).json()["results"]
    assert [row["full_name"] for row in trash] == ["Gone"]
    assert (
        client_for(colleague).get(CONTACTS_URL, {"trash": "true"}).json()["count"] == 0
    )
    assert client_for(colleague).get(url).status_code == 404

    assert client_for(admin).post(f"{url}restore/").status_code == 200
    contact.refresh_from_db()
    assert contact.deleted_at is None


# --- deleting a shared contact detaches the users' copies ---------------------


def _overlay(user, parent, **kwargs):
    return models.Contact.objects.create(
        owner=user, organization=user.organization, overlay_of=parent, **kwargs
    )


def test_deleting_an_organization_contact_detaches_the_local_copies(admin, colleague):
    parent = org_contact(
        admin.organization,
        full_name="Accueil",
        emails=[{"type": "work", "value": "accueil@example.org"}],
        vcard_extra={"UID": ["UID:accueil"]},
    )
    overlay = _overlay(
        colleague, parent, full_name="Mon accueil", overridden_fields=["full_name"]
    )
    personal_group = factories.ContactGroupFactory(owner=colleague, name="Mine")
    shared_group = factories.ContactGroupFactory(
        owner=None, organization=admin.organization, name="Shared"
    )
    for group in (personal_group, shared_group):
        models.ContactGroupMembership.objects.create(group=group, contact=parent)
    parent.favorited_by.add(colleague)

    parent.delete()

    overlay.refresh_from_db()
    assert overlay.overlay_of is None and overlay.overridden_fields == []
    assert overlay.full_name == "Mon accueil"  # its own edit
    assert overlay.emails == [{"type": "work", "value": "accueil@example.org"}]
    assert overlay.vcard_extra == {"UID": ["UID:accueil"]}
    assert list(overlay.groups.all()) == [personal_group]
    assert overlay.favorited_by.filter(pk=colleague.pk).exists()


def test_purging_the_trash_detaches_the_local_copies_too(admin, colleague, settings):
    settings.TRASHBIN_CUTOFF_DAYS = 30
    parent = org_contact(admin.organization, full_name="Accueil")
    overlay = _overlay(colleague, parent)
    models.Contact.objects.filter(pk=parent.pk).update(
        deleted_at=timezone.now() - timedelta(days=31)
    )

    call_command("purge_trashed_contacts")

    overlay.refresh_from_db()
    assert (overlay.overlay_of, overlay.full_name) == (None, "Accueil")


# --- sharing a personal contact ----------------------------------------------


def test_sharing_a_contact_copies_it_and_keeps_what_is_private(admin):
    contact = factories.ContactFactory(
        owner=admin,
        full_name="Ada",
        emails=[{"type": "work", "value": "ada@example.org"}],
        notes="private",
        vcard_extra={"UID": ["UID:ada"]},
    )
    label = models.ContactLabel.objects.create(owner=admin, name="Agent")
    contact.labels.add(label)
    group = factories.ContactGroupFactory(owner=admin, name="Team")
    models.ContactGroupMembership.objects.create(group=group, contact=contact)
    contact.favorited_by.add(admin)

    response = client_for(admin).post(f"{CONTACTS_URL}{contact.id}/share/")

    assert response.status_code == 201, response.content
    shared = models.Contact.objects.get(pk=response.json()["id"])
    assert (shared.scope, shared.full_name, shared.notes) == (
        Scope.ORGANIZATION,
        "Ada",
        "",
    )
    assert shared.vcard_extra == {"UID": ["UID:ada"]}
    assert not shared.labels.exists()
    assert list(shared.groups.all()) == [group]
    assert shared.favorited_by.filter(pk=admin.pk).exists()
    # The personal card became a local copy holding the private part.
    contact.refresh_from_db()
    assert contact.overlay_of == shared
    assert contact.overridden_fields == ["notes"]
    assert (contact.notes, contact.full_name) == ("private", "")
    assert list(contact.labels.all()) == [label]


def test_sharing_a_contact_with_nothing_private_moves_it(admin):
    contact = factories.ContactFactory(owner=admin, full_name="Bob")
    response = client_for(admin).post(f"{CONTACTS_URL}{contact.id}/share/")
    assert response.status_code == 201
    assert not models.Contact.objects.filter(pk=contact.pk).exists()
    assert models.Contact.objects.get(scope=Scope.ORGANIZATION).full_name == "Bob"


def test_only_an_administrator_shares_and_only_their_own_contacts(admin, colleague):
    mine = factories.ContactFactory(owner=colleague)
    assert (
        client_for(colleague).post(f"{CONTACTS_URL}{mine.id}/share/").status_code == 403
    )
    shared = org_contact(admin.organization)
    assert (
        client_for(admin).post(f"{CONTACTS_URL}{shared.id}/share/").status_code == 400
    )
    assert (
        not models.Contact.objects.filter(scope=Scope.ORGANIZATION)
        .exclude(pk=shared.pk)
        .exists()
    )


# --- importing into the organization -----------------------------------------

VCF = (
    "BEGIN:VCARD\nVERSION:3.0\nUID:ada\nFN:Ada\nEMAIL:ada@example.org\n"
    "CATEGORIES:Agent,starred\nEND:VCARD\n"
    "BEGIN:VCARD\nVERSION:3.0\nFN:Team\nX-ADDRESSBOOKSERVER-KIND:group\n"
    "X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:ada\nEND:VCARD\n"
)


def test_an_administrator_imports_into_the_organization(admin):
    result = import_contacts(admin, "vcf", VCF.encode(), Scope.ORGANIZATION)

    assert (result["imported"], result["groups"]) == (1, 1)
    contact = models.Contact.objects.get()
    assert (contact.scope, contact.owner, contact.created_by) == (
        Scope.ORGANIZATION,
        None,
        admin,
    )
    # No labels, and no star: those are per user.
    assert not contact.labels.exists() and not contact.favorited_by.exists()
    group = models.ContactGroup.objects.get()
    assert (group.scope, list(group.members.all())) == (Scope.ORGANIZATION, [contact])

    again = import_contacts(admin, "vcf", VCF.encode(), Scope.ORGANIZATION)
    assert again["skipped"] == 1
    assert models.Contact.objects.count() == 1


def test_importing_into_the_organization_takes_an_administrator(colleague):
    response = client_for(colleague).post(
        f"{CONTACTS_URL}import-vcf/",
        {
            "file": ContentFile(VCF.encode(), name="contacts.vcf"),
            "scope": "organization",
        },
        format="multipart",
    )
    assert response.status_code == 403


def test_the_import_endpoint_imports_into_the_organization(admin):
    response = client_for(admin).post(
        f"{CONTACTS_URL}import-vcf/",
        {
            "file": ContentFile(VCF.encode(), name="contacts.vcf"),
            "scope": "organization",
        },
        format="multipart",
    )
    assert response.status_code == 202, response.content
    # Test settings use the eager broker: the import already ran.
    assert models.Contact.objects.get().scope == Scope.ORGANIZATION
