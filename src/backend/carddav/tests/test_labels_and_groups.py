"""Labels as CATEGORIES and groups as group cards, over CardDAV."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument

from datetime import timedelta

from django.core.management import call_command
from django.test import Client
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core import factories, models

from carddav.models import CardName, ContactChange
from carddav.tests.test_carddav import (
    DIRECTORY,
    PERSONAL,
    dav,
    get,
    hrefs,
    make_channel,
    put,
    row,
    sync,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return factories.UserFactory(email="user@example.org")


@pytest.fixture
def auth(user):
    _channel, headers = make_channel(user)
    return headers


@pytest.fixture
def groups_auth(user):
    _channel, headers = make_channel(user, groups=True)
    return headers


@pytest.fixture(params=[False, True], ids=["without_groups", "with_groups"])
def with_groups(request):
    return request.param


@pytest.fixture
def any_auth(user, with_groups):
    """The headers of a device, for each value of its groups option."""
    _channel, headers = make_channel(user, groups=with_groups)
    return headers


def card(uid, name, *extra):
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"UID:{uid}",
        f"FN:{name}",
        f"N:{name};;;;",
        *extra,
        "END:VCARD",
    ]
    return "\r\n".join(lines) + "\r\n"


def contact_card(uid, name, *extra):
    return card(uid, name, "EMAIL:someone@example.org", *extra)


def group_card(uid, name, *member_uids):
    members = [f"X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:{uid}" for uid in member_uids]
    return card(uid, name, "X-ADDRESSBOOKSERVER-KIND:group", *members)


def seq_of(pk):
    return ContactChange.objects.get(contact_id=pk).seq


def etag(auth, url):
    return get(auth, url)["ETag"]


def label_names(contact):
    return [label.name for label in contact.labels.all()]


# --- labels as CATEGORIES ---------------------------------------------


def test_labels_are_served_as_categories(auth, user):
    contact = factories.ContactFactory(owner=user, full_name="Ada")
    for name in ("Élu", "Agent, principal"):
        contact.labels.add(models.ContactLabel.objects.create(owner=user, name=name))

    body = get(auth, f"{PERSONAL}{contact.id}.vcf").content.decode()
    assert "\r\nCATEGORIES:Agent\\, principal,Élu\r\n" in body


def test_put_categories_become_labels(auth, user):
    existing = models.ContactLabel.objects.create(owner=user, name="Amis")
    url = f"{PERSONAL}ada.vcf"

    response = put(auth, url, contact_card("ada", "Ada", "CATEGORIES:amis,Travail"))
    assert response.status_code == 201
    contact = models.Contact.objects.get(owner=user)
    labels = sorted(contact.labels.all(), key=lambda label: label.name)
    # "amis" found the existing label rather than making a second one.
    assert [label.name for label in labels] == ["Amis", "Travail"]
    assert labels[0] == existing


def test_put_changed_categories_update_labels(any_auth, user):
    url = f"{PERSONAL}ada.vcf"
    put(any_auth, url, contact_card("ada", "Ada", "CATEGORIES:Amis,Travail"))
    served = get(any_auth, url).content.decode()

    body = served.replace("CATEGORIES:Amis,Travail", "CATEGORIES:Travail,Famille")
    assert put(any_auth, url, body).status_code == 204
    contact = models.Contact.objects.get(owner=user)
    assert sorted(label.name for label in contact.labels.all()) == [
        "Famille",
        "Travail",
    ]
    # The label itself stays: other contacts may wear it.
    assert models.ContactLabel.objects.filter(owner=user, name="Amis").exists()


@pytest.mark.parametrize("names", [["Amis"], []])
def test_unchanged_put_keeps_labels_and_version(any_auth, user, names):
    url = f"{PERSONAL}ada.vcf"
    categories = [f"CATEGORIES:{name}" for name in names]
    put(any_auth, url, contact_card("ada", "Ada", *categories))
    contact = models.Contact.objects.get(owner=user)
    stored = row(contact)
    before = seq_of(contact.pk)
    served = get(any_auth, url)

    response = put(any_auth, url, served.content)
    assert response.status_code == 204
    assert response["ETag"] == served["ETag"]
    assert row(contact) == stored
    assert seq_of(contact.pk) == before
    assert label_names(contact) == names


def test_put_without_categories_keeps_labels_only_with_groups(
    any_auth, with_groups, user
):
    url = f"{PERSONAL}ada.vcf"
    put(any_auth, url, contact_card("ada", "Ada", "CATEGORIES:Amis"))
    served = get(any_auth, url).content.decode()

    body = served.replace("CATEGORIES:Amis\r\n", "TEL:+33123456789\r\n")
    response = put(any_auth, url, body)
    assert response.status_code == 204
    contact = models.Contact.objects.get(owner=user)
    assert [phone["value"] for phone in contact.phones] == ["+33123456789"]
    assert label_names(contact) == (["Amis"] if with_groups else [])
    # A kept label makes the stored card differ from the body. The answer
    # then has no ETag, so the client reads the card back.
    assert response.has_header("ETag") is not with_groups


def test_put_empty_categories_removes_labels(any_auth, user):
    url = f"{PERSONAL}ada.vcf"
    put(any_auth, url, contact_card("ada", "Ada", "CATEGORIES:Amis,Travail"))
    served = get(any_auth, url).content.decode()

    body = served.replace("CATEGORIES:Amis,Travail", "CATEGORIES:")
    assert put(any_auth, url, body).status_code == 204
    assert label_names(models.Contact.objects.get(owner=user)) == []


def test_put_first_categories_add_labels(any_auth, user):
    url = f"{PERSONAL}ada.vcf"
    put(any_auth, url, contact_card("ada", "Ada"))
    served = get(any_auth, url).content.decode()

    body = served.replace("END:VCARD", "CATEGORIES:Amis\r\nEND:VCARD")
    assert put(any_auth, url, body).status_code == 204
    assert label_names(models.Contact.objects.get(owner=user)) == ["Amis"]


def restored_label_names(auth, user, *categories):
    """The label names of a card labeled "Amis", trashed, then PUT again."""
    url = f"{PERSONAL}ada.vcf"
    put(auth, url, contact_card("ada", "Ada", "CATEGORIES:Amis"))
    assert dav(Client(), "DELETE", url, headers=auth).status_code == 204

    assert put(auth, url, contact_card("ada", "Ada", *categories)).status_code == 201
    contact = models.Contact.objects.get(owner=user)
    assert contact.deleted_at is None
    return label_names(contact)


@pytest.mark.parametrize(
    "categories,expected",
    [("CATEGORIES:Travail", ["Travail"]), ("CATEGORIES:", [])],
)
def test_restore_follows_the_categories_of_the_body(
    any_auth, user, categories, expected
):
    assert restored_label_names(any_auth, user, categories) == expected


def test_restore_without_categories_keeps_labels_only_with_groups(
    any_auth, with_groups, user
):
    assert restored_label_names(any_auth, user) == (["Amis"] if with_groups else [])


@pytest.mark.parametrize("change", ["add", "remove", "rename", "delete"])
def test_a_label_change_is_synced_with_a_new_etag(auth, user, change):
    contact = factories.ContactFactory(owner=user, full_name="Ada")
    label = models.ContactLabel.objects.create(owner=user, name="Agent")
    if change != "add":
        contact.labels.add(label)
    url = f"{PERSONAL}{contact.id}.vcf"
    before = etag(auth, url)
    _first, token = sync(auth, PERSONAL)

    if change == "add":
        contact.labels.add(label)
    elif change == "remove":
        label.contacts.remove(contact)
    elif change == "rename":
        label.name = "Agente"
        label.save()
    else:
        label.delete()

    response, _ = sync(auth, PERSONAL, token)
    assert hrefs(response) == [url]
    assert etag(auth, url) != before
    categories = {"add": "Agent", "remove": None, "rename": "Agente", "delete": None}
    body = get(auth, url).content.decode()
    if categories[change]:
        assert f"CATEGORIES:{categories[change]}\r\n" in body
    else:
        assert "CATEGORIES" not in body


def test_a_cleared_label_set_is_synced(auth, user):
    contact = factories.ContactFactory(owner=user, full_name="Ada")
    contact.labels.add(models.ContactLabel.objects.create(owner=user, name="Agent"))
    _first, token = sync(auth, PERSONAL)

    contact.labels.clear()

    response, _ = sync(auth, PERSONAL, token)
    assert hrefs(response) == [f"{PERSONAL}{contact.id}.vcf"]


def test_org_row_keeps_its_stored_categories(auth, user):
    """A row without an owner has no labels: its own CATEGORIES stand."""
    org = factories.ContactFactory(
        owner=None,
        organization=user.organization,
        full_name="Accueil",
        vcard_extra={"CATEGORIES": ["CATEGORIES:Accueil"]},
    )
    body = get(auth, f"{DIRECTORY}{org.id}.vcf").content.decode()
    assert "\r\nCATEGORIES:Accueil\r\n" in body


def test_overlay_shows_its_labels_not_the_parent_categories(auth, user):
    parent = factories.ContactFactory(
        owner=None,
        organization=user.organization,
        source="directory",
        external_id="ext-1",
        full_name="Accueil",
        vcard_extra={"CATEGORIES": ["CATEGORIES:Source"]},
    )
    overlay = models.Contact.objects.create(
        owner=user,
        organization=user.organization,
        overlay_of=parent,
        overridden_fields=[],
    )
    overlay.labels.add(models.ContactLabel.objects.create(owner=user, name="Mine"))

    body = get(auth, f"{PERSONAL}{overlay.id}.vcf").content.decode()
    assert "\r\nCATEGORIES:Mine\r\n" in body
    assert "Source" not in body


# --- group cards: off by default --------------------------------------


def test_groups_are_not_served_without_the_device_setting(auth, user):
    group = factories.ContactGroupFactory(owner=user, organization=user.organization)
    response = dav(Client(), "PROPFIND", PERSONAL, headers=auth, depth="1")
    assert f"{PERSONAL}{group.id}.vcf" not in hrefs(response)
    assert get(auth, f"{PERSONAL}{group.id}.vcf").status_code == 404


def test_a_group_card_is_refused_without_the_device_setting(auth, user):
    response = put(auth, f"{PERSONAL}team.vcf", group_card("team", "Team"))
    assert response.status_code == 403
    assert not models.ContactGroup.objects.filter(owner=user).exists()


# --- group cards: reading ---------------------------------------------


def test_a_group_is_served_as_a_group_card(groups_auth, user):
    # A member created by a phone is named by the phone's UID.
    put(groups_auth, f"{PERSONAL}ada.vcf", contact_card("ada-uid", "Ada"))
    ada = models.Contact.objects.get(owner=user)
    bob = factories.ContactFactory(owner=user, full_name="Bob")
    group = factories.ContactGroupFactory(
        owner=user, organization=user.organization, name="Équipe"
    )
    for member in (ada, bob):
        models.ContactGroupMembership.objects.create(group=group, contact=member)

    listing = dav(Client(), "PROPFIND", PERSONAL, headers=groups_auth, depth="1")
    assert f"{PERSONAL}{group.id}.vcf" in hrefs(listing)

    body = get(groups_auth, f"{PERSONAL}{group.id}.vcf").content.decode()
    assert "\r\nFN:Équipe\r\n" in body
    assert "\r\nX-ADDRESSBOOKSERVER-KIND:group\r\n" in body
    assert "\r\nX-ADDRESSBOOKSERVER-MEMBER:urn:uuid:ada-uid\r\n" in body
    assert f"\r\nX-ADDRESSBOOKSERVER-MEMBER:urn:uuid:{bob.id}\r\n" in body
    assert f"\r\nUID:{group.id}\r\n" in body


def test_a_group_card_lists_only_members_of_its_book(groups_auth, user):
    """An org contact is a card of the directory book, not of personal."""
    org = factories.ContactFactory(owner=None, organization=user.organization)
    group = factories.ContactGroupFactory(owner=user, organization=user.organization)
    models.ContactGroupMembership.objects.create(group=group, contact=org)

    body = get(groups_auth, f"{PERSONAL}{group.id}.vcf").content.decode()
    assert "MEMBER" not in body


def test_an_overlaid_member_is_named_by_its_overlay(groups_auth, user):
    parent = factories.ContactFactory(
        owner=None,
        organization=user.organization,
        source="directory",
        external_id="ext-1",
    )
    group = factories.ContactGroupFactory(owner=user, organization=user.organization)
    models.ContactGroupMembership.objects.create(group=group, contact=parent)
    url = f"{PERSONAL}{group.id}.vcf"
    _first, token = sync(groups_auth, PERSONAL)

    overlay = models.Contact.objects.create(
        owner=user,
        organization=user.organization,
        overlay_of=parent,
        overridden_fields=[],
    )

    # The overlay joins the personal book, and the group card with it.
    response, _ = sync(groups_auth, PERSONAL, token)
    assert url in hrefs(response)
    body = get(groups_auth, url).content.decode()
    assert f"X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:{overlay.id}" in body


def test_org_groups_are_read_only_group_cards_of_the_directory(groups_auth, user):
    org = factories.ContactFactory(owner=None, organization=user.organization)
    group = factories.ContactGroupFactory(owner=None, organization=user.organization)
    models.ContactGroupMembership.objects.create(group=group, contact=org)
    url = f"{DIRECTORY}{group.id}.vcf"

    body = get(groups_auth, url).content.decode()
    assert f"X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:{org.id}" in body
    assert put(groups_auth, url, body).status_code == 403
    assert dav(Client(), "DELETE", url, headers=groups_auth).status_code == 403


def test_query_matches_group_cards_by_name(groups_auth, user):
    factories.ContactGroupFactory(
        owner=user, organization=user.organization, name="Équipe projet"
    )
    body = (
        '<card:addressbook-query xmlns:d="DAV:" '
        'xmlns:card="urn:ietf:params:xml:ns:carddav">'
        "<d:prop><d:getetag/></d:prop><card:filter>"
        '<card:prop-filter name="FN"><card:text-match>projet</card:text-match>'
        "</card:prop-filter></card:filter></card:addressbook-query>"
    )
    response = dav(Client(), "REPORT", PERSONAL, headers=groups_auth, body=body)
    assert response.status_code == 207
    assert len(hrefs(response)) == 1


# --- group cards: writing ---------------------------------------------


def test_put_a_group_card_creates_the_group(groups_auth, user):
    put(groups_auth, f"{PERSONAL}ada.vcf", contact_card("ada-uid", "Ada"))
    url = f"{PERSONAL}TEAM-UID.vcf"

    response = put(groups_auth, url, group_card("team-uid", "Team", "ada-uid"))
    assert response.status_code == 201

    group = models.ContactGroup.objects.get(owner=user)
    assert group.name == "Team"
    assert [member.full_name for member in group.members.all()] == ["Ada"]
    assert CardName.objects.get(owner=user, name="TEAM-UID.vcf").contact_id == group.pk
    body = get(groups_auth, url).content.decode()
    assert "\r\nUID:team-uid\r\n" in body
    assert "\r\nX-ADDRESSBOOKSERVER-MEMBER:urn:uuid:ada-uid\r\n" in body


def test_a_group_card_sent_back_unchanged_is_a_no_op(groups_auth, user):
    put(groups_auth, f"{PERSONAL}ada.vcf", contact_card("ada-uid", "Ada"))
    url = f"{PERSONAL}team.vcf"
    put(groups_auth, url, group_card("team-uid", "Team", "ada-uid"))
    group = models.ContactGroup.objects.get(owner=user)
    before = seq_of(group.pk)

    served = get(groups_auth, url).content
    response = put(groups_auth, url, served)
    assert response.status_code == 204
    assert response["ETag"] == etag(groups_auth, url)
    assert seq_of(group.pk) == before


def test_put_a_changed_group_card_updates_name_and_members(groups_auth, user):
    for uid, name in (("ada-uid", "Ada"), ("bob-uid", "Bob"), ("eve-uid", "Eve")):
        put(groups_auth, f"{PERSONAL}{uid}.vcf", contact_card(uid, name))
    url = f"{PERSONAL}team.vcf"
    put(groups_auth, url, group_card("team-uid", "Team", "ada-uid", "bob-uid"))
    _first, token = sync(groups_auth, PERSONAL)

    response = put(
        groups_auth, url, group_card("team-uid", "Crew", "bob-uid", "eve-uid")
    )
    assert response.status_code == 204

    group = models.ContactGroup.objects.get(owner=user)
    assert group.name == "Crew"
    assert sorted(member.full_name for member in group.members.all()) == ["Bob", "Eve"]
    delta, _ = sync(groups_auth, PERSONAL, token)
    assert url in hrefs(delta)


def test_a_member_added_from_a_phone_is_synced(groups_auth, user):
    for uid, name in (("ada-uid", "Ada"), ("bob-uid", "Bob")):
        put(groups_auth, f"{PERSONAL}{uid}.vcf", contact_card(uid, name))
    url = f"{PERSONAL}team.vcf"
    put(groups_auth, url, group_card("team-uid", "Team", "ada-uid"))
    before = etag(groups_auth, url)
    _first, token = sync(groups_auth, PERSONAL)

    body = group_card("team-uid", "Team", "ada-uid", "bob-uid")
    assert put(groups_auth, url, body).status_code == 204

    delta, _ = sync(groups_auth, PERSONAL, token)
    assert url in hrefs(delta)
    assert etag(groups_auth, url) != before


def test_a_group_card_does_not_drop_members_it_was_not_served(groups_auth, user):
    org = factories.ContactFactory(owner=None, organization=user.organization)
    group = factories.ContactGroupFactory(
        owner=user, organization=user.organization, name="Team"
    )
    models.ContactGroupMembership.objects.create(group=group, contact=org)
    url = f"{PERSONAL}{group.id}.vcf"

    assert put(groups_auth, url, get(groups_auth, url).content).status_code == 204
    assert list(group.members.all()) == [org]


def test_a_member_sent_before_its_card_joins_when_the_card_arrives(groups_auth, user):
    """iOS may send a group card before the card of a new member."""
    url = f"{PERSONAL}team.vcf"
    put(groups_auth, url, group_card("team-uid", "Team", "new-uid"))
    group = models.ContactGroup.objects.get(owner=user)
    assert not group.members.exists()
    # The unknown member is served back as it was sent.
    served = get(groups_auth, url).content.decode()
    assert "\r\nX-ADDRESSBOOKSERVER-MEMBER:urn:uuid:new-uid\r\n" in served

    put(groups_auth, f"{PERSONAL}new.vcf", contact_card("new-uid", "New"))

    assert [member.full_name for member in group.members.all()] == ["New"]
    group.refresh_from_db()
    assert "X-ADDRESSBOOKSERVER-MEMBER" not in group.vcard_extra
    served = get(groups_auth, url).content.decode()
    assert served.count("X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:new-uid") == 1


def test_a_group_name_taken_by_another_group_is_numbered(groups_auth, user):
    factories.ContactGroupFactory(
        owner=user, organization=user.organization, name="Team"
    )
    response = put(groups_auth, f"{PERSONAL}team.vcf", group_card("t", "team"))
    assert response.status_code == 201
    names = sorted(
        models.ContactGroup.objects.filter(owner=user).values_list("name", flat=True)
    )
    assert names == ["Team", "team (2)"]


def test_a_nameless_group_card_is_refused(groups_auth, user):
    body = card("t", "", "X-ADDRESSBOOKSERVER-KIND:group").replace("N:;;;;\r\n", "")
    assert put(groups_auth, f"{PERSONAL}t.vcf", body).status_code == 403


def test_a_card_does_not_change_kind(groups_auth, user):
    url = f"{PERSONAL}ada.vcf"
    put(groups_auth, url, contact_card("ada-uid", "Ada"))
    assert put(groups_auth, url, group_card("ada-uid", "Ada")).status_code == 403


def test_vcard4_kind_and_member_are_read(groups_auth, user):
    put(groups_auth, f"{PERSONAL}ada.vcf", contact_card("ada-uid", "Ada"))
    body = card("t", "Team", "KIND:group", "MEMBER:urn:uuid:ada-uid")
    assert put(groups_auth, f"{PERSONAL}t.vcf", body).status_code == 201
    group = models.ContactGroup.objects.get(owner=user)
    assert [member.full_name for member in group.members.all()] == ["Ada"]


def test_delete_a_group_card_deletes_the_group(groups_auth, user):
    url = f"{PERSONAL}team.vcf"
    put(groups_auth, url, group_card("team-uid", "Team"))
    _first, token = sync(groups_auth, PERSONAL)

    assert dav(Client(), "DELETE", url, headers=groups_auth).status_code == 204

    assert not models.ContactGroup.objects.filter(owner=user).exists()
    delta, _ = sync(groups_auth, PERSONAL, token)
    assert hrefs(delta) == [url]


# --- group cards: journal ---------------------------------------------


@pytest.mark.parametrize("change", ["add", "remove", "rename", "trash_member"])
def test_a_group_change_is_synced_with_a_new_etag(groups_auth, user, change):
    ada = factories.ContactFactory(owner=user, full_name="Ada")
    bob = factories.ContactFactory(owner=user, full_name="Bob")
    group = factories.ContactGroupFactory(owner=user, organization=user.organization)
    models.ContactGroupMembership.objects.create(group=group, contact=ada)
    url = f"{PERSONAL}{group.id}.vcf"
    before = etag(groups_auth, url)
    _first, token = sync(groups_auth, PERSONAL)

    if change == "add":
        models.ContactGroupMembership.objects.create(group=group, contact=bob)
    elif change == "remove":
        models.ContactGroupMembership.objects.filter(group=group, contact=ada).delete()
    elif change == "rename":
        group.name = "Renamed"
        group.save()
    else:
        ada.deleted_at = timezone.now()
        ada.save()

    delta, _ = sync(groups_auth, PERSONAL, token)
    assert url in hrefs(delta)
    assert etag(groups_auth, url) != before


def test_a_device_without_groups_is_not_told_about_them(auth, user):
    _first, token = sync(auth, PERSONAL)
    group = factories.ContactGroupFactory(owner=user, organization=user.organization)
    group.delete()

    delta, _ = sync(auth, PERSONAL, token)
    assert hrefs(delta) == []


def test_group_tombstones_are_purged(user, settings):
    settings.TRASHBIN_CUTOFF_DAYS = 30
    group = factories.ContactGroupFactory(owner=user, name="Gone")
    kept = factories.ContactGroupFactory(owner=user, name="Kept")
    group.delete()
    ContactChange.objects.update(updated_at=timezone.now() - timedelta(days=31))

    call_command("purge_carddav_changes")

    assert list(ContactChange.objects.values_list("contact_id", flat=True)) == [kept.pk]


# --- device setting ---------------------------------------------------


def test_a_device_can_be_created_with_groups(user):
    client = APIClient()
    client.force_login(user)
    response = client.post(
        "/api/v1.0/channels/",
        {
            "name": "iPhone",
            "type": "carddav",
            "scopes": ["carddav:write"],
            "groups": True,
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["groups"] is True

    response = client.post(
        "/api/v1.0/channels/",
        {"name": "Svc", "scopes": ["contacts:read"], "groups": True},
        format="json",
    )
    assert response.status_code == 400
