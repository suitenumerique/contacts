"""Contacts and groups as JSContact cards (core.services.jscontact)."""

from django.test import override_settings

import pytest

from core import factories, models
from core.enums import Scope
from core.services import jscontact

pytestmark = pytest.mark.django_db


def _contact(**fields):
    fields.setdefault("emails", [{"type": "work", "value": "ada@example.org"}])
    return factories.ContactFactory(**fields)


def test_a_card_has_the_required_properties():
    """@type, version and uid are always there; kind says what it is."""
    contact = _contact(full_name="Ada Lovelace")

    card = jscontact.contact_card(contact)

    assert card["@type"] == "Card"
    assert card["version"] == "1.0"
    assert card["uid"] == f"urn:uuid:{contact.pk}"
    assert card["kind"] == "individual"


def test_the_name_has_its_full_form_and_its_parts():
    """name.full is the displayed name; the stored parts are components."""
    contact = _contact(
        full_name="Dr Ada King Lovelace",
        name_prefix="Dr",
        given_name="Ada",
        middle_name="King",
        family_name="Lovelace",
        name_suffix="PhD",
        nickname="Countess",
        civility="mrs",
    )

    card = jscontact.contact_card(contact)

    assert card["name"] == {
        "full": "Dr Ada King Lovelace",
        "components": [
            {"kind": "title", "value": "Dr"},
            {"kind": "given", "value": "Ada"},
            {"kind": "given2", "value": "King"},
            {"kind": "surname", "value": "Lovelace"},
            {"kind": "credential", "value": "PhD"},
        ],
    }
    assert card["nicknames"] == {"k1": {"name": "Countess"}}
    assert card["speakToAs"] == {"grammaticalGender": "feminine"}


def test_emails_are_keyed_with_contexts_labels_and_a_preferred_one():
    """work and home become contexts, other types labels; the first is pref 1."""
    contact = _contact(
        emails=[
            {"type": "work", "value": "ada@work.example"},
            {"type": "home", "value": "ada@home.example"},
            {"type": "other", "value": "ada@other.example"},
            {"type": "school", "value": "ada@school.example"},
            {"type": "", "value": ""},
        ]
    )

    assert jscontact.contact_card(contact)["emails"] == {
        "e1": {"address": "ada@work.example", "contexts": {"work": True}, "pref": 1},
        "e2": {"address": "ada@home.example", "contexts": {"private": True}},
        "e3": {"address": "ada@other.example"},
        "e4": {"address": "ada@school.example", "label": "school"},
    }


def test_company_department_title_and_photo():
    """Organization with its unit, job title, and an https photo."""
    contact = _contact(
        company="Mairie de Brigny",
        department="Service technique",
        job_title="Directrice",
        photo="https://cdn.example/ada.png",
    )

    card = jscontact.contact_card(contact)

    assert card["organizations"] == {
        "o1": {"name": "Mairie de Brigny", "units": [{"name": "Service technique"}]}
    }
    assert card["titles"] == {"t1": {"kind": "title", "name": "Directrice"}}
    assert card["media"] == {
        "m1": {"kind": "photo", "uri": "https://cdn.example/ada.png"}
    }


def test_empty_properties_and_inline_photos_are_left_out():
    """A property with nothing to say is absent, and data: photos too."""
    contact = _contact(full_name="Ada", photo="data:image/png;base64,iVBORw0KGgo=")

    card = jscontact.contact_card(contact)

    for absent in (
        "nicknames",
        "speakToAs",
        "organizations",
        "titles",
        "media",
    ):
        assert absent not in card
    assert "components" not in card["name"]


def test_vendor_properties_say_whose_book_and_where_from():
    """isOrg and source, with the configured prefix."""
    user = factories.UserFactory()
    shared = factories.ContactFactory(
        owner=None,
        organization=user.organization,
        scope=Scope.ORGANIZATION,
        source="ldap",
        external_id="1",
        emails=[{"type": "work", "value": "ada@example.org"}],
    )

    card = jscontact.contact_card(shared)

    assert card["numerique.gouv.fr:isOrg"] is True
    assert card["numerique.gouv.fr:source"] == "ldap"


@override_settings(JSCONTACT_VENDOR_PREFIX="suite.example")
def test_the_vendor_prefix_comes_from_the_settings():
    """JSCONTACT_VENDOR_PREFIX names every vendor property."""
    card = jscontact.contact_card(_contact())

    assert card["suite.example:isOrg"] is False
    assert not [key for key in card if key.startswith("numerique.gouv.fr:")]


def test_an_overlay_shows_merged_under_its_own_uid():
    """An overlay's card reads the fields it doesn't override from its parent."""
    user = factories.UserFactory()
    synced = factories.ContactFactory(
        owner=None,
        organization=user.organization,
        scope=Scope.ORGANIZATION,
        full_name="Claire Dubois",
        job_title="Agent",
        source="ldap",
        external_id="1",
        emails=[{"type": "work", "value": "claire@brigny.example"}],
    )
    overlay = models.Contact(
        owner=user,
        organization=user.organization,
        overlay_of=synced,
        full_name="Claire D.",
        overridden_fields=["full_name"],
    )
    overlay.save()

    card = jscontact.contact_card(overlay)

    assert card["uid"] == f"urn:uuid:{overlay.pk}"
    assert card["name"]["full"] == "Claire D."
    assert card["emails"]["e1"]["address"] == "claire@brigny.example"
    assert card["titles"]["t1"]["name"] == "Agent"
    assert card["numerique.gouv.fr:isOrg"] is False


def test_a_group_card():
    """A group: kind group, its address if any, its members counted."""
    group = factories.ContactGroupFactory(name="Team", email="team@example.org")
    group.members_count = 4

    assert jscontact.group_card(group) == {
        "@type": "Card",
        "version": "1.0",
        "uid": f"urn:uuid:{group.pk}",
        "kind": "group",
        "name": {"full": "Team"},
        "emails": {"e1": {"address": "team@example.org", "pref": 1}},
        "numerique.gouv.fr:isOrg": False,
        "numerique.gouv.fr:source": "",
        "numerique.gouv.fr:membersCount": 4,
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "urn:uuid:79aa8714-94af-4ea5-8fc3-55673153af3e",
            "79aa8714-94af-4ea5-8fc3-55673153af3e",
        ),
        (
            "URN:UUID:79aa8714-94af-4ea5-8fc3-55673153af3e",
            "79aa8714-94af-4ea5-8fc3-55673153af3e",
        ),
        (
            "79aa8714-94af-4ea5-8fc3-55673153af3e",
            "79aa8714-94af-4ea5-8fc3-55673153af3e",
        ),
        ("", ""),
    ],
)
def test_a_uid_parses_back_to_the_row_id(value, expected):
    """A card uid gives back the row id (a bare UUID is accepted too)."""
    assert jscontact.parse_uid(value) == expected
