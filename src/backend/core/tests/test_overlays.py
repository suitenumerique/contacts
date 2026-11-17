"""Tests for source provenance, read-only org/synced rows, and overlays."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument

from django.db.models import QuerySet

import pytest
from rest_framework.test import APIClient

from core import factories, models

pytestmark = pytest.mark.django_db

CONTACTS_URL = "/api/v1.0/contacts/"
GROUPS_URL = "/api/v1.0/groups/"


@pytest.fixture
def user():
    return factories.UserFactory()


@pytest.fixture
def client(user):
    client = APIClient()
    client.force_login(user)
    return client


def synced_contact(user, **kwargs):
    """An org-wide read-only contact materialized from a directory source."""
    defaults = {
        "owner": None,
        "organization": user.organization,
        "source": "directory",
        "external_id": "uid=jean",
        "full_name": "Jean Dupont",
        "emails": [{"type": "work", "value": "jean@example.org"}],
    }
    defaults.update(kwargs)
    return models.Contact.objects.create(**defaults)


# --- read-only enforcement -------------------------------------------------


def test_synced_contact_is_read_only(client, user):
    contact = synced_contact(user)
    listed = client.get(CONTACTS_URL).json()["results"]
    entry = next(c for c in listed if c["id"] == str(contact.id))
    assert entry["source"] == "directory"
    assert entry["is_writable"] is False

    assert (
        client.patch(
            f"{CONTACTS_URL}{contact.id}/", {"full_name": "X"}, format="json"
        ).status_code
        == 403
    )
    assert client.delete(f"{CONTACTS_URL}{contact.id}/").status_code == 403


def test_native_contact_is_writable(client, user):
    contact = factories.ContactFactory(owner=user, full_name="Mine")
    entry = client.get(f"{CONTACTS_URL}{contact.id}/").json()
    assert entry["is_writable"] is True
    assert entry["source"] == ""


def test_synced_group_is_read_only(client, user):
    group = models.ContactGroup.objects.create(
        owner=None,
        organization=user.organization,
        source="directory",
        external_id="cn=team",
        name="Annuaire",
    )
    entry = next(g for g in client.get(GROUPS_URL).json() if g["id"] == str(group.id))
    assert entry["is_writable"] is False
    assert (
        client.patch(
            f"{GROUPS_URL}{group.id}/", {"name": "X"}, format="json"
        ).status_code
        == 403
    )


# --- fork / overlay --------------------------------------------------------


def test_fork_creates_editable_overlay(client, user):
    contact = synced_contact(user)
    response = client.post(f"{CONTACTS_URL}{contact.id}/fork/")
    assert response.status_code == 200, response.content
    overlay = response.json()
    assert overlay["id"] != str(contact.id)
    assert overlay["overlay_of"] == str(contact.id)
    assert overlay["is_writable"] is True
    # Merged view still shows the source-provided fields.
    assert overlay["full_name"] == "Jean Dupont"
    assert overlay["emails"] == [{"type": "work", "value": "jean@example.org"}]


def test_fork_is_idempotent(client, user):
    contact = synced_contact(user)
    first = client.post(f"{CONTACTS_URL}{contact.id}/fork/").json()
    second = client.post(f"{CONTACTS_URL}{contact.id}/fork/").json()
    assert first["id"] == second["id"]
    assert models.Contact.objects.filter(overlay_of=contact, owner=user).count() == 1


def test_cannot_fork_a_writable_contact(client, user):
    contact = factories.ContactFactory(owner=user)
    assert client.post(f"{CONTACTS_URL}{contact.id}/fork/").status_code == 400


def test_list_hides_parent_in_favor_of_overlay(client, user):
    contact = synced_contact(user)
    overlay_id = client.post(f"{CONTACTS_URL}{contact.id}/fork/").json()["id"]
    ids = [c["id"] for c in client.get(CONTACTS_URL).json()["results"]]
    assert overlay_id in ids
    assert str(contact.id) not in ids


def test_overlay_overrides_one_field_and_tracks_source_for_others(client, user):
    contact = synced_contact(user)
    overlay_id = client.post(f"{CONTACTS_URL}{contact.id}/fork/").json()["id"]

    # Rename locally; leave email untouched.
    resp = client.patch(
        f"{CONTACTS_URL}{overlay_id}/",
        {"full_name": "Jean (boss)", "given_name": "Jean", "family_name": "Dupont"},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["full_name"] == "Jean (boss)"
    assert "full_name" in body["overridden_fields"]
    assert body["emails"] == [{"type": "work", "value": "jean@example.org"}]

    # A later source update flows into the non-overridden field...
    contact.emails = [{"type": "work", "value": "jean.dupont@example.org"}]
    contact.full_name = "Jean Dupont-Martin"
    contact.save()
    merged = client.get(f"{CONTACTS_URL}{overlay_id}/").json()
    assert merged["emails"] == [{"type": "work", "value": "jean.dupont@example.org"}]
    # ...but the overridden field stays local.
    assert merged["full_name"] == "Jean (boss)"


def test_setting_field_back_to_source_unoverrides_it(client, user):
    contact = synced_contact(user)
    overlay_id = client.post(f"{CONTACTS_URL}{contact.id}/fork/").json()["id"]
    client.patch(f"{CONTACTS_URL}{overlay_id}/", {"full_name": "Local"}, format="json")
    body = client.patch(
        f"{CONTACTS_URL}{overlay_id}/",
        {"full_name": "Jean Dupont"},
        format="json",
    ).json()
    assert "full_name" not in body["overridden_fields"]
    overlay = models.Contact.objects.get(pk=overlay_id)
    assert overlay.full_name == ""  # own value cleared, reads from parent
    assert body["full_name"] == "Jean Dupont"


def test_email_or_phone_rule_reads_the_merged_card(client, user):
    """An overlay cannot empty what its source has. It can keep a bare source."""
    contact = synced_contact(user)
    overlay_id = client.post(f"{CONTACTS_URL}{contact.id}/fork/").json()["id"]
    response = client.patch(
        f"{CONTACTS_URL}{overlay_id}/", {"emails": [], "phones": []}, format="json"
    )
    assert response.status_code == 400

    bare = synced_contact(user, external_id="uid=bare", emails=[])
    overlay_id = client.post(f"{CONTACTS_URL}{bare.id}/fork/").json()["id"]
    response = client.patch(
        f"{CONTACTS_URL}{overlay_id}/",
        {"company": "Mairie de Brigny", "emails": [], "phones": []},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["overridden_fields"] == ["company"]


def test_deleting_overlay_reverts_to_source(client, user):
    contact = synced_contact(user)
    overlay_id = client.post(f"{CONTACTS_URL}{contact.id}/fork/").json()["id"]
    assert client.delete(f"{CONTACTS_URL}{overlay_id}/").status_code == 204
    assert not models.Contact.objects.filter(pk=overlay_id).exists()
    # The source row is untouched and visible again.
    ids = [c["id"] for c in client.get(CONTACTS_URL).json()["results"]]
    assert str(contact.id) in ids


def test_overlay_star_is_kept_on_the_parent(client, user):
    """Starring an overlay stars the canonical row, which survives a revert."""
    contact = synced_contact(user)
    overlay_id = client.post(f"{CONTACTS_URL}{contact.id}/fork/").json()["id"]

    assert client.post(f"{CONTACTS_URL}{overlay_id}/favorite/").status_code == 204
    assert client.get(f"{CONTACTS_URL}{overlay_id}/").json()["is_favorite"] is True
    assert list(contact.favorited_by.all()) == [user]

    client.delete(f"{CONTACTS_URL}{overlay_id}/")
    assert client.get(f"{CONTACTS_URL}{contact.id}/").json()["is_favorite"] is True


def test_overlay_shows_the_parents_star(client, user):
    contact = synced_contact(user)
    contact.favorited_by.add(user)
    overlay_id = client.post(f"{CONTACTS_URL}{contact.id}/fork/").json()["id"]

    favorites = client.get(CONTACTS_URL, {"favorites": "true"}).json()["results"]
    assert [row["id"] for row in favorites] == [overlay_id]

    assert client.delete(f"{CONTACTS_URL}{overlay_id}/favorite/").status_code == 204
    assert not contact.favorited_by.exists()


def test_groups_cannot_be_forked(client, user):
    group = models.ContactGroup.objects.create(
        owner=None,
        organization=user.organization,
        source="directory",
        external_id="cn=team",
        name="Annuaire",
    )
    assert client.post(f"{GROUPS_URL}{group.id}/fork/").status_code == 404


def test_search_excludes_overlaid_parent(client, user):
    """Searching yields the overlay, never the read-only parent."""
    parent = synced_contact(user, full_name="Jean Dupont")
    overlay = models.Contact.objects.create(
        owner=user,
        organization=user.organization,
        overlay_of=parent,
        full_name="Jean (local)",
        overridden_fields=["full_name"],
    )

    results = client.get(CONTACTS_URL, {"q": "Jean"}).json()["results"]
    ids = {c["id"] for c in results}
    assert str(overlay.id) in ids
    assert str(parent.id) not in ids


def test_revert_list_field_unoverrides(client, user):
    """Re-submitting an email identical to the source clears the override."""
    contact = synced_contact(user)  # email jean@example.org, type work
    overlay_id = client.post(f"{CONTACTS_URL}{contact.id}/fork/").json()["id"]

    # Override the email, then set it back to the exact source value.
    client.patch(
        f"{CONTACTS_URL}{overlay_id}/",
        {"emails": [{"type": "work", "value": "other@example.org"}]},
        format="json",
    )
    body = client.patch(
        f"{CONTACTS_URL}{overlay_id}/",
        {"emails": [{"type": "work", "value": "jean@example.org"}]},
        format="json",
    ).json()
    assert "emails" not in body["overridden_fields"]
    assert body["emails"] == [{"type": "work", "value": "jean@example.org"}]


def test_forked_group_member_stays_in_group_view(client, user):
    """Forking a group member keeps it (as the overlay) in the group views."""
    org = user.organization
    parent = synced_contact(user)
    group = models.ContactGroup.objects.create(owner=user, organization=org, name="G")
    models.ContactGroupMembership.objects.create(group=group, contact=parent)

    overlay_id = client.post(f"{CONTACTS_URL}{parent.id}/fork/").json()["id"]

    # Group-filtered contact list shows the overlay, not the parent.
    ids = [
        c["id"]
        for c in client.get(f"{CONTACTS_URL}?group={group.id}").json()["results"]
    ]
    assert overlay_id in ids
    assert str(parent.id) not in ids

    # The group's own members endpoint agrees.
    member_ids = [
        c["id"] for c in client.get(f"{GROUPS_URL}{group.id}/contacts/").json()
    ]
    assert overlay_id in member_ids
    assert str(parent.id) not in member_ids


def test_vcf_export_merges_overlay_fields(client, user):
    """Exports must carry inherited fields, not just the overridden ones."""
    contact = synced_contact(user, full_name="Jean Dupont", company="Brigny Agglo")
    overlay_id = client.post(f"{CONTACTS_URL}{contact.id}/fork/").json()["id"]
    client.patch(
        f"{CONTACTS_URL}{overlay_id}/", {"full_name": "Jean (local)"}, format="json"
    )

    body = client.get(f"{CONTACTS_URL}export-vcf/").content.decode()
    assert "Jean (local)" in body  # the override
    assert "jean@example.org" in body  # inherited email
    assert "Brigny Agglo" in body  # inherited company


def test_csv_export_merges_overlay_fields(client, user):
    contact = synced_contact(user, full_name="Jean Dupont", company="Brigny Agglo")
    overlay_id = client.post(f"{CONTACTS_URL}{contact.id}/fork/").json()["id"]
    client.patch(
        f"{CONTACTS_URL}{overlay_id}/", {"full_name": "Jean (local)"}, format="json"
    )

    body = client.get(f"{CONTACTS_URL}export-csv/").content.decode()
    assert "jean@example.org" in body
    assert "Brigny Agglo" in body


def test_overlay_search_follows_parent_changes(client, user):
    """A parent edit must reach the overlay's search text and sort key."""
    contact = synced_contact(user, company="Brigny Agglo")
    overlay_id = client.post(f"{CONTACTS_URL}{contact.id}/fork/").json()["id"]

    contact.company = "Brigny Ville"
    contact.full_name = "Albert Dupont"
    contact.save()

    assert client.get(CONTACTS_URL, {"q": "ville"}).json()["count"] == 1
    assert client.get(CONTACTS_URL, {"q": "agglo"}).json()["count"] == 0
    overlay = models.Contact.objects.get(pk=overlay_id)
    assert overlay.sort_key == "albert dupont"


def test_overlay_search_follows_a_parent_phone_change(client, user):
    contact = synced_contact(user)
    client.post(f"{CONTACTS_URL}{contact.id}/fork/")

    contact.phones = [{"type": "work", "value": "+33 1 99 00 11 22"}]
    contact.save(update_fields=["phones", "search_text", "sort_key"])

    assert client.get(CONTACTS_URL, {"q": "0199001122"}).json()["count"] == 1


def test_overlay_keeps_its_own_name_in_search_when_parent_changes(client, user):
    contact = synced_contact(user)
    overlay_id = client.post(f"{CONTACTS_URL}{contact.id}/fork/").json()["id"]
    client.patch(f"{CONTACTS_URL}{overlay_id}/", {"full_name": "Zed"}, format="json")

    contact.full_name = "Albert Dupont"
    contact.save()

    assert models.Contact.objects.get(pk=overlay_id).sort_key == "zed"


def test_concurrent_fork_returns_the_winning_overlay(client, user, monkeypatch):
    """A fork racing another one sees no overlay, then fails to insert."""
    contact = synced_contact(user)
    winner = models.Contact.objects.create(
        owner=user, organization=user.organization, overlay_of=contact
    )
    real_get, real_first = QuerySet.get, QuerySet.first
    missed = []

    def looks_up_the_overlay(queryset, kwargs):
        if "overlay_of" in kwargs:
            return True
        return any(
            getattr(getattr(child, "lhs", None), "target", None) is not None
            and child.lhs.target.name == "overlay_of"
            for child in queryset.query.where.children
        )

    def get(self, *args, **kwargs):
        if not missed and looks_up_the_overlay(self, kwargs):
            missed.append(True)
            raise self.model.DoesNotExist()
        return real_get(self, *args, **kwargs)

    def first(self):
        if not missed and looks_up_the_overlay(self, {}):
            missed.append(True)
            return None
        return real_first(self)

    monkeypatch.setattr(QuerySet, "get", get)
    monkeypatch.setattr(QuerySet, "first", first)
    response = client.post(f"{CONTACTS_URL}{contact.id}/fork/")

    assert missed
    assert response.status_code == 200, response.content
    assert response.json()["id"] == str(winner.id)
    assert models.Contact.objects.filter(overlay_of=contact).count() == 1
