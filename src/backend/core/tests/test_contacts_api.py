"""Tests for the contacts CRUD API."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument,too-many-arguments

from django.db import connection

import pytest
from rest_framework.test import APIClient

from core import factories, models
from core.api import viewsets as viewsets_module
from core.api.viewsets import _search_filter

pytestmark = pytest.mark.django_db

CONTACTS_URL = "/api/v1.0/contacts/"


@pytest.fixture
def user():
    return factories.UserFactory()


@pytest.fixture
def client(user):
    client = APIClient()
    client.force_login(user)
    return client


def test_anonymous_cannot_list_contacts():
    assert APIClient().get(CONTACTS_URL).status_code == 401


def test_create_and_retrieve_contact(client):
    response = client.post(
        CONTACTS_URL,
        {
            "civility": "mrs",
            "given_name": "Jeanne",
            "family_name": "Martin",
            "full_name": "Jeanne Martin",
            "emails": [{"type": "work", "value": "jeanne@example.org"}],
            "phones": [{"type": "cell", "value": "+33639985678"}],
            "company": "Brigny Agglo",
            "department": "Produits numériques",
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    data = response.json()
    assert data["display_name"] == "Jeanne Martin"
    assert data["civility"] == "mrs"
    assert data["is_org_contact"] is False

    detail = client.get(f"{CONTACTS_URL}{data['id']}/")
    assert detail.status_code == 200
    assert detail.json()["emails"] == [{"type": "work", "value": "jeanne@example.org"}]


def test_create_requires_a_name(client):
    response = client.post(
        CONTACTS_URL,
        {"full_name": " ", "emails": [{"value": "nobody@example.org"}]},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["errors"][0]["attr"] == "full_name"


@pytest.mark.parametrize(
    ("payload", "full_name"),
    [
        ({"given_name": "Solo"}, "Solo"),
        ({"family_name": "Martin"}, "Martin"),
        (
            {
                "given_name": "Jean",
                "middle_name": "Paul",
                "family_name": "Martin",
                "name_suffix": "Jr.",
            },
            "Jean Paul Martin Jr.",
        ),
        ({"full_name": "Madonna"}, "Madonna"),
        ({"full_name": "Dr J. Martin", "given_name": "Jean"}, "Dr J. Martin"),
    ],
)
def test_any_name_part_is_enough_and_fills_the_full_name(client, payload, full_name):
    response = client.post(
        CONTACTS_URL,
        {**payload, "emails": [{"value": "someone@example.org"}]},
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["full_name"] == full_name
    assert response.json()["display_name"] == full_name


def test_a_lone_full_name_is_not_split(client):
    response = client.post(
        CONTACTS_URL,
        {"full_name": "Jean de La Fontaine", "phones": [{"value": "+33199000405"}]},
        format="json",
    )
    body = response.json()
    assert (body["given_name"], body["family_name"]) == ("", "")


def test_an_update_cannot_blank_every_name(client, user):
    contact = factories.ContactFactory(
        owner=user, full_name="Jeanne Martin", given_name="Jeanne"
    )
    response = client.patch(
        f"{CONTACTS_URL}{contact.id}/",
        {"full_name": "", "given_name": ""},
        format="json",
    )
    assert response.status_code == 400
    contact.refresh_from_db()
    assert contact.full_name == "Jeanne Martin"


def test_contacts_are_filed_under_their_family_name(client, user):
    for full_name, given, family in [
        ("Zoé Abadie", "Zoé", "Abadie"),
        ("Alban Siou", "Alban", "Siou"),
        ("Médiathèque", "", ""),
        ("Bruno Siou", "Bruno", "Siou"),
    ]:
        factories.ContactFactory(
            owner=user, full_name=full_name, given_name=given, family_name=family
        )

    names = [row["display_name"] for row in client.get(CONTACTS_URL).json()["results"]]

    assert names == ["Zoé Abadie", "Médiathèque", "Alban Siou", "Bruno Siou"]


def test_create_requires_email_or_phone(client):
    response = client.post(
        CONTACTS_URL,
        {"given_name": "Jeanne", "family_name": "Martin"},
        format="json",
    )
    assert response.status_code == 400

    response = client.post(
        CONTACTS_URL,
        {
            "given_name": "Jeanne",
            "family_name": "Martin",
            "phones": [{"type": "work", "value": "+33199000405"}],
        },
        format="json",
    )
    assert response.status_code == 201, response.content


def test_favorite_endpoint_stars_and_unstars(client, user):
    contact = factories.ContactFactory(owner=user, full_name="Imported Only")

    assert client.post(f"{CONTACTS_URL}{contact.id}/favorite/").status_code == 204
    assert client.get(f"{CONTACTS_URL}{contact.id}/").json()["is_favorite"] is True

    assert client.delete(f"{CONTACTS_URL}{contact.id}/favorite/").status_code == 204
    assert client.get(f"{CONTACTS_URL}{contact.id}/").json()["is_favorite"] is False


def test_is_favorite_is_read_only(client, user):
    contact = factories.ContactFactory(owner=user)
    response = client.patch(
        f"{CONTACTS_URL}{contact.id}/", {"is_favorite": True}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["is_favorite"] is False
    assert not contact.favorited_by.exists()


def test_org_contact_star_is_per_user(client, user):
    """Starring a shared row needs no fork and stays private to the user."""
    org_contact = factories.ContactFactory(owner=None, organization=user.organization)
    colleague = factories.UserFactory(organization=user.organization)

    response = client.post(f"{CONTACTS_URL}{org_contact.id}/favorite/")

    assert response.status_code == 204
    assert not models.Contact.objects.filter(overlay_of=org_contact).exists()
    listed = client.get(CONTACTS_URL, {"favorites": "true"}).json()["results"]
    assert [row["id"] for row in listed] == [str(org_contact.id)]

    other = APIClient()
    other.force_login(colleague)
    assert other.get(CONTACTS_URL, {"favorites": "true"}).json()["count"] == 0
    assert other.get(f"{CONTACTS_URL}{org_contact.id}/").json()["is_favorite"] is False


def test_favorite_on_foreign_contact_is_404(client):
    foreign = factories.ContactFactory()
    assert client.post(f"{CONTACTS_URL}{foreign.id}/favorite/").status_code == 404
    assert not foreign.favorited_by.exists()


def test_list_scoping(client, user):
    mine = factories.ContactFactory(owner=user, full_name="Mine")
    org_contact = factories.ContactFactory(
        owner=None, organization=user.organization, full_name="Org Directory"
    )
    factories.ContactFactory(full_name="Other user's")  # other org/user

    response = client.get(CONTACTS_URL)
    assert response.status_code == 200
    names = {c["full_name"] for c in response.json()["results"]}
    assert names == {mine.full_name, org_contact.full_name}


def test_same_org_user_contacts_are_private(client, user):
    colleague = factories.UserFactory(organization=user.organization)
    factories.ContactFactory(owner=colleague, full_name="Colleague's secret")

    response = client.get(CONTACTS_URL)
    assert response.json()["count"] == 0


def test_search_filter(client, user):
    factories.ContactFactory(owner=user, full_name="Édouard Dupont")
    factories.ContactFactory(owner=user, full_name="Alice Zorg")

    response = client.get(CONTACTS_URL, {"q": "edouard"})
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["full_name"] == "Édouard Dupont"


def test_search_filter_accented_query(client, user):
    """An accented query matches both accented and unaccented names."""
    factories.ContactFactory(owner=user, full_name="Édouard Dupont")
    factories.ContactFactory(owner=user, full_name="Edouard Plain")
    factories.ContactFactory(owner=user, full_name="Alice Zorg")

    results = client.get(CONTACTS_URL, {"q": "Édouard"}).json()["results"]
    assert {c["full_name"] for c in results} == {"Édouard Dupont", "Edouard Plain"}


@pytest.mark.parametrize(
    "query",
    [
        "0639986309",  # national, no separators
        "06 39 98",  # national, as a start
        "06.39.98.63.09",
        "+33 6 39 98 63 09",  # international
        "+33639",
        "0033 6 39 98",  # international with the 00 call prefix
        "39 98 63",  # any run of digits
    ],
)
def test_search_finds_a_phone_number_however_it_is_typed(client, user, query):
    factories.ContactFactory(
        owner=user, full_name="Alban Siou", phones=[{"value": "+33 6 39 98 63 09"}]
    )
    factories.ContactFactory(
        owner=user, full_name="Other", phones=[{"value": "+33 1 99 00 11 22"}]
    )

    results = client.get(CONTACTS_URL, {"q": query}).json()["results"]

    assert [contact["full_name"] for contact in results] == ["Alban Siou"]


def test_search_finds_a_nationally_typed_number_by_its_international_form(client, user):
    factories.ContactFactory(
        owner=user, full_name="Armand Talot", phones=[{"value": "06.39.98.63.10"}]
    )

    results = client.get(CONTACTS_URL, {"q": "+33 6 39 98 63 10"}).json()["results"]

    assert [contact["full_name"] for contact in results] == ["Armand Talot"]


def test_search_reads_foreign_numbers_in_their_own_national_form(client, user):
    factories.ContactFactory(
        owner=user, full_name="Liam Carter", phones=[{"value": "+1 (201) 555-0123"}]
    )

    results = client.get(CONTACTS_URL, {"q": "201-555-0123"}).json()["results"]

    assert [contact["full_name"] for contact in results] == ["Liam Carter"]


def test_org_contacts_are_read_only(client, user):
    org_contact = factories.ContactFactory(
        owner=None, organization=user.organization, full_name="Org Directory"
    )
    response = client.patch(
        f"{CONTACTS_URL}{org_contact.id}/", {"full_name": "Hacked"}, format="json"
    )
    assert response.status_code == 403
    assert client.delete(f"{CONTACTS_URL}{org_contact.id}/").status_code == 403


def test_cannot_touch_other_org_contact(client):
    foreign = factories.ContactFactory(full_name="Foreign")
    assert client.get(f"{CONTACTS_URL}{foreign.id}/").status_code == 404
    assert client.delete(f"{CONTACTS_URL}{foreign.id}/").status_code == 404


def test_favorite_filter(client, user):
    factories.ContactFactory(owner=user, full_name="Fav").favorited_by.add(user)
    factories.ContactFactory(owner=user, full_name="Not fav")

    response = client.get(CONTACTS_URL, {"favorites": "true"})
    names = {c["full_name"] for c in response.json()["results"]}
    assert names == {"Fav"}


def test_group_filter(client, user):
    contact = factories.ContactFactory(owner=user, full_name="In group")
    factories.ContactFactory(owner=user, full_name="Not in group")
    group = factories.ContactGroupFactory(owner=user)
    models.ContactGroupMembership.objects.create(group=group, contact=contact)

    response = client.get(CONTACTS_URL, {"group": str(group.id)})
    names = {c["full_name"] for c in response.json()["results"]}
    assert names == {"In group"}


def test_export_vcf(client, user):
    factories.ContactFactory(
        owner=user,
        full_name="Jeanne Martin",
        emails=[{"type": "work", "value": "jeanne@example.org"}],
    )
    response = client.get(f"{CONTACTS_URL}export-vcf/")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/vcard")
    body = response.content.decode()
    assert "FN:Jeanne Martin" in body
    assert "jeanne@example.org" in body


def test_channel_with_contacts_read_scope_can_list(user):
    channel = factories.ChannelFactory(
        user=user, settings={"scopes": ["contacts:read"]}
    )
    factories.ContactFactory(owner=user, full_name="Readable")

    client = APIClient()
    response = client.get(
        CONTACTS_URL,
        HTTP_X_CHANNEL_ID=str(channel.id),
        HTTP_X_API_KEY=channel.raw_api_key,
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_channel_without_scope_cannot_list(user):
    channel = factories.ChannelFactory(user=user, settings={"scopes": ["groups:read"]})
    client = APIClient()
    response = client.get(
        CONTACTS_URL,
        HTTP_X_CHANNEL_ID=str(channel.id),
        HTTP_X_API_KEY=channel.raw_api_key,
    )
    assert response.status_code == 403


def test_channel_cannot_create_contact(user):
    channel = factories.ChannelFactory(
        user=user, settings={"scopes": ["contacts:read"]}
    )
    client = APIClient()
    response = client.post(
        CONTACTS_URL,
        {"full_name": "Nope"},
        format="json",
        HTTP_X_CHANNEL_ID=str(channel.id),
        HTTP_X_API_KEY=channel.raw_api_key,
    )
    assert response.status_code == 403


# --- sorting ---------------------------------------------------------


def make_named(user, *names):
    """Contacts whose display order is driven by their name."""
    for name in names:
        factories.ContactFactory(
            owner=user,
            organization=user.organization,
            full_name=name,
            given_name=name.split()[0],
            family_name=name.split()[-1],
        )


def listed_names(response):
    return [row["display_name"] for row in response.json()["results"]]


def test_contacts_are_listed_alphabetically_by_default(client, user):
    make_named(user, "Zoe Zulu", "Alice Alpha", "Marc Mike")

    response = client.get(CONTACTS_URL)

    assert response.status_code == 200
    assert listed_names(response) == ["Alice Alpha", "Marc Mike", "Zoe Zulu"]


def test_sort_desc_reverses_the_whole_list_server_side(client, user):
    """The client cannot reverse what it has not paged in, so the server does."""
    make_named(user, "Zoe Zulu", "Alice Alpha", "Marc Mike")

    response = client.get(CONTACTS_URL, {"sort": "desc"})

    assert listed_names(response) == ["Zoe Zulu", "Marc Mike", "Alice Alpha"]


def test_sort_desc_applies_across_pages(client, user):
    make_named(user, "Anna A", "Bruno B", "Carla C", "Dario D")

    first = client.get(CONTACTS_URL, {"sort": "desc", "page_size": 2})

    assert listed_names(first) == ["Dario D", "Carla C"]
    assert first.json()["count"] == 4


def test_sort_desc_works_with_a_search(client, user):
    make_named(user, "Team Alpha", "Team Beta")

    response = client.get(CONTACTS_URL, {"q": "Team", "sort": "desc"})

    assert listed_names(response) == ["Team Beta", "Team Alpha"]


def test_unknown_sort_value_falls_back_to_ascending(client, user):
    make_named(user, "Zoe Zulu", "Alice Alpha")

    response = client.get(CONTACTS_URL, {"sort": "sideways"})

    assert listed_names(response) == ["Alice Alpha", "Zoe Zulu"]


def test_very_long_names_do_not_break_saving(client):
    """given + family can exceed the 255 characters of full_name and sort_key."""
    response = client.post(
        CONTACTS_URL,
        {
            "given_name": "G" * 255,
            "family_name": "F" * 255,
            "emails": [{"value": "long@example.org"}],
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    contact = models.Contact.objects.get(pk=response.json()["id"])
    assert contact.full_name == ("G" * 255 + " " + "F" * 255)[:255]
    assert contact.sort_key == ("f" * 255 + " " + "g" * 255)[:255]


# --- visibility of groups and labels ------------------------------------------


def test_group_ids_hide_other_users_personal_groups(client, user):
    """An org contact may sit in a colleague's personal group."""
    org_contact = factories.ContactFactory(owner=None, organization=user.organization)
    colleague = factories.UserFactory(organization=user.organization)
    theirs = factories.ContactGroupFactory(owner=colleague)
    mine = factories.ContactGroupFactory(owner=user)
    org_group = factories.ContactGroupFactory(
        owner=None, organization=user.organization
    )
    for group in (theirs, mine, org_group):
        models.ContactGroupMembership.objects.create(group=group, contact=org_contact)

    expected = {str(mine.id), str(org_group.id)}
    detail = client.get(f"{CONTACTS_URL}{org_contact.id}/").json()
    assert set(detail["group_ids"]) == expected
    listed = client.get(CONTACTS_URL).json()["results"]
    assert set(listed[0]["group_ids"]) == expected


def test_label_ids_hide_other_users_labels(client, user):
    org_contact = factories.ContactFactory(owner=None, organization=user.organization)
    colleague = factories.UserFactory(organization=user.organization)
    org_contact.labels.add(
        models.ContactLabel.objects.create(owner=colleague, name="X")
    )

    detail = client.get(f"{CONTACTS_URL}{org_contact.id}/").json()
    assert detail["label_ids"] == []


def test_filter_on_invisible_group_is_404(client, user):
    org_contact = factories.ContactFactory(owner=None, organization=user.organization)
    colleague = factories.UserFactory(organization=user.organization)
    theirs = factories.ContactGroupFactory(owner=colleague)
    models.ContactGroupMembership.objects.create(group=theirs, contact=org_contact)

    assert client.get(CONTACTS_URL, {"group": str(theirs.id)}).status_code == 404
    assert client.get(CONTACTS_URL, {"group": "not-a-uuid"}).status_code == 404


def test_filter_on_invisible_label_is_404(client, user):
    colleague = factories.UserFactory(organization=user.organization)
    theirs = models.ContactLabel.objects.create(owner=colleague, name="X")
    factories.ContactFactory(owner=None, organization=user.organization).labels.add(
        theirs
    )

    assert client.get(CONTACTS_URL, {"label": str(theirs.id)}).status_code == 404
    assert client.get(CONTACTS_URL, {"label": "not-a-uuid"}).status_code == 404


# --- validation ------------------------------------------------------------------


def card(**fields):
    return {
        "given_name": "Jeanne",
        "family_name": "Martin",
        "emails": [{"type": "work", "value": "jeanne@example.org"}],
        **fields,
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"emails": [{"type": "work", "value": "not-an-email"}]},
        {"emails": [{"type": "<script>", "value": "a@example.org"}]},
        {"emails": [{"type": "x" * 33, "value": "a@example.org"}]},
        {"emails": [{"value": f"a{i}@example.org"} for i in range(51)]},
        {"phones": [{"value": "1" * 65}]},
        {"urls": [{"value": "javascript:alert(1)"}]},
        {"urls": [{"value": "data:text/html,<script>alert(1)</script>"}]},
        {"urls": [{"value": "not a url"}]},
        {"addresses": [{"value": "x" * 1025}]},
        {"photo": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="},
        {"photo": "http://example.com/me.png"},
        {"photo": "javascript:alert(1)"},
    ],
)
def test_invalid_card_fields_are_refused(client, fields):
    response = client.post(CONTACTS_URL, card(**fields), format="json")
    assert response.status_code == 400, response.content
    assert not models.Contact.objects.exists()


@pytest.mark.parametrize(
    "fields",
    [
        {"urls": [{"type": "work", "value": "example.com"}]},
        {"urls": [{"value": "https://example.com/path?x=1"}]},
        {"emails": [{"type": "x-custom-1", "value": "a@example.org"}]},
        {"photo": "https://example.com/me.png"},
        {"photo": "data:image/png;base64,iVBORw0KGgo="},
    ],
)
def test_valid_card_fields_are_accepted(client, fields):
    response = client.post(CONTACTS_URL, card(**fields), format="json")
    assert response.status_code == 201, response.content


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        (
            {"emails": [{"value": "not-an-email"}]},
            ("invalid", "Enter a valid email address: not-an-email", "emails"),
        ),
        (
            {"emails": [{"type": "<script>", "value": "a@example.org"}]},
            (
                "invalid",
                "This value does not match the required pattern.",
                "emails.0.type",
            ),
        ),
        (
            {"emails": [{"value": f"a{i}@example.org"} for i in range(51)]},
            (
                "max_length",
                "Ensure this field has no more than 50 elements.",
                "emails.non_field_errors",
            ),
        ),
        (
            {"phones": [{"value": "1" * 65}]},
            ("invalid", "Phone numbers are 64 characters max.", "phones"),
        ),
        (
            {"urls": [{"value": " ftp://example.org/x "}]},
            ("invalid", "Enter a valid URL: ftp://example.org/x", "urls"),
        ),
        (
            {"addresses": [{"value": "x" * 1025}]},
            (
                "max_length",
                "Ensure this field has no more than 1024 characters.",
                "addresses.0.value",
            ),
        ),
    ],
)
def test_refused_values_report_their_rule(client, fields, error):
    response = client.post(CONTACTS_URL, card(**fields), format="json")
    assert response.status_code == 400, response.content
    code, detail, attr = error
    assert response.json()["errors"] == [{"code": code, "detail": detail, "attr": attr}]


def test_email_lookups_use_the_upper_index(user):
    """on_behalf_of and CardDAV resolve users with email__iexact."""
    queryset = models.User.objects.filter(email__iexact=user.email)

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        plan = queryset.explain()

    assert "contacts_user_email_upper" in plan


def test_export_query_count_does_not_grow_with_overlays(
    client, user, django_assert_max_num_queries
):
    def add_overlay(index):
        parent = factories.ContactFactory(
            owner=None,
            organization=user.organization,
            source="directory",
            external_id=f"uid={index}",
        )
        models.Contact.objects.create(
            owner=user, organization=user.organization, overlay_of=parent
        )

    add_overlay(0)
    with django_assert_max_num_queries(8) as baseline:
        client.get(f"{CONTACTS_URL}export-csv/")
    for index in range(1, 6):
        add_overlay(index)
    with django_assert_max_num_queries(len(baseline.captured_queries)):
        client.get(f"{CONTACTS_URL}export-csv/")
    with django_assert_max_num_queries(len(baseline.captured_queries)):
        client.get(f"{CONTACTS_URL}export-vcf/")


def test_channel_user_is_resolved_once_per_request(user, monkeypatch):
    channel = factories.ChannelFactory(user=user)
    factories.ContactFactory.create_batch(3, owner=user)
    calls = []
    real = viewsets_module.resolve_acting_user

    def counting(request):
        calls.append(request)
        return real(request)

    monkeypatch.setattr(viewsets_module, "resolve_acting_user", counting)
    response = APIClient().get(
        CONTACTS_URL,
        HTTP_X_CHANNEL_ID=str(channel.id),
        HTTP_X_API_KEY=channel.raw_api_key,
    )

    assert response.status_code == 200
    assert len(calls) == 1


def test_search_uses_the_trigram_index(user):
    """The folded column is matched with LIKE, which the GIN index serves."""
    factories.ContactFactory(owner=user, full_name="Édouard Dupont")
    queryset = models.Contact.objects.filter(_search_filter("search_text", "Édouard"))

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        plan = queryset.explain()

    assert "contacts_contact_search_trgm" in plan
    assert queryset.count() == 1
