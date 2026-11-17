"""Stored characters that XML 1.0 does not allow, over CardDAV."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument

from xml.etree import ElementTree as ET

from django.test import Client

import pytest
from rest_framework.test import APIClient

from core import factories, models

from carddav.models import ContactChange
from carddav.tests.test_carddav import (
    PERSONAL,
    VCARD,
    dav,
    get,
    hrefs,
    make_channel,
    multiget,
    put,
    row,
)
from carddav.xml import CARD, D, to_bytes

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return factories.UserFactory(email="user@example.org")


@pytest.fixture
def auth(user):
    _channel, headers = make_channel(user)
    return headers


def store(instance, **values):
    """Write *values* to the row of *instance*, without the model checks."""
    type(instance).objects.filter(pk=instance.pk).update(**values)


def address_data(response):
    """The card text and the ETag of a multiget answer that holds one card."""
    assert response.status_code == 207
    root = ET.fromstring(response.content)
    card = root.find(f".//{CARD('address-data')}").text
    return card, root.find(f".//{D('getetag')}").text


def test_to_bytes_leaves_out_what_xml_does_not_allow():
    element = ET.Element(D("prop"), {"name": "a\x0bb"})
    element.text = "a\x00\x08\x0b\x0c\x0e\x1f\ud800\udfff\ufffe\uffffb\t\n é\U0001f600"

    parsed = ET.fromstring(to_bytes(element))
    assert parsed.text == "ab\t\n é\U0001f600"
    assert parsed.get("name") == "ab"


def test_a_note_with_a_control_character_is_served_as_valid_xml(auth, user):
    client = APIClient()
    client.force_login(user)
    created = client.post(
        "/api/v1.0/contacts/",
        {
            "full_name": "Ada Lovelace",
            "emails": [{"type": "work", "value": "ada@example.org"}],
            "notes": "line\x0bbreak",
        },
        format="json",
    )
    assert created.status_code == 201, created.content

    url = f"{PERSONAL}{created.json()['id']}.vcf"
    card, _etag = address_data(multiget(auth, PERSONAL, url))
    assert "\nNOTE:linebreak\n" in card


@pytest.mark.parametrize("char", ["\x0b", "\ufffe"])
def test_a_card_is_served_without_what_xml_does_not_allow(auth, user, char):
    contact = factories.ContactFactory(owner=user)
    label = models.ContactLabel.objects.create(owner=user, name="Friends")
    contact.labels.add(label)
    store(
        contact,
        full_name=f"Ada{char} Lovelace",
        vcard_extra={"X-PET": [f"X-PET:Mr{char} Whiskers"]},
    )
    store(label, name=f"Fri{char}ends")

    url = f"{PERSONAL}{contact.id}.vcf"
    card, _etag = address_data(multiget(auth, PERSONAL, url))
    assert "\nFN:Ada Lovelace\n" in card
    assert "\nCATEGORIES:Friends\n" in card
    assert "\nX-PET:Mr Whiskers\n" in card


def test_a_group_card_is_served_without_what_xml_does_not_allow(user):
    _channel, auth = make_channel(user, groups=True)
    group = factories.ContactGroupFactory(owner=user, organization=user.organization)
    store(group, name="Te\x0bam", vcard_extra={"X-PET": ["X-PET:Mr\x0b Whiskers"]})

    url = f"{PERSONAL}{group.id}.vcf"
    card, _etag = address_data(multiget(auth, PERSONAL, url))
    assert "\nFN:Team\n" in card
    assert "\nX-PET:Mr Whiskers\n" in card


def test_get_and_multiget_serve_the_same_card(auth, user):
    contact = factories.ContactFactory(owner=user)
    store(contact, notes="line\x0bbreak")
    url = f"{PERSONAL}{contact.id}.vcf"

    fetched = get(auth, url)
    card, etag = address_data(multiget(auth, PERSONAL, url))
    assert etag == fetched["ETag"]
    # An XML parser reads CRLF as LF.
    assert card == fetched.content.decode().replace("\r\n", "\n")
    assert "\nNOTE:linebreak\n" in card


def test_an_unchanged_put_keeps_the_stored_characters(auth, user):
    contact = factories.ContactFactory(owner=user)
    label = models.ContactLabel.objects.create(owner=user, name="Friends")
    contact.labels.add(label)
    store(
        contact,
        full_name="Ada\x0b Lovelace",
        notes="line\x0bbreak",
        vcard_extra={"X-PET": ["X-PET:Mr\x0b Whiskers"]},
    )
    store(label, name="Fri\x0bends")
    before = row(contact)
    seq = ContactChange.objects.get(contact_id=contact.pk).seq
    url = f"{PERSONAL}{contact.id}.vcf"
    served = get(auth, url)
    assert b"\x0b" not in served.content

    response = put(auth, url, served.content, HTTP_IF_MATCH=served["ETag"])
    assert response.status_code == 204
    assert response["ETag"] == served["ETag"]
    assert row(contact) == before
    assert ContactChange.objects.get(contact_id=contact.pk).seq == seq
    assert [label.name for label in contact.labels.all()] == ["Fri\x0bends"]


def test_display_names_are_served_as_valid_xml(auth, user):
    contact = factories.ContactFactory(owner=user)
    store(contact, full_name="Ada\x0b Lovelace")
    store(user, full_name="Grace\x0b Hopper")
    body = '<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/></d:prop></d:propfind>'

    def names(path, depth):
        response = dav(Client(), "PROPFIND", path, headers=auth, body=body, depth=depth)
        root = ET.fromstring(response.content)
        return [element.text for element in root.iter(D("displayname"))]

    assert names(PERSONAL, "1") == ["My contacts", "Ada Lovelace"]
    assert names(f"/carddav/principals/{user.email}/", "0") == ["Grace Hopper"]


def test_a_card_name_with_a_control_character_is_listed_percent_encoded(auth, user):
    url = f"{PERSONAL}a%0Bb.vcf"
    assert put(auth, url, VCARD).status_code == 201

    listing = dav(Client(), "PROPFIND", PERSONAL, headers=auth, depth="1")
    assert url in hrefs(listing)
    assert get(auth, url).status_code == 200
