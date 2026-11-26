"""Tests for the kind and the grammatical gender of a vCard."""

# pylint: disable=missing-function-docstring

import pytest

from core import factories, models
from core.services.vcf_service import parse_vcf, serialize_contacts

pytestmark = pytest.mark.django_db


def round_trip(fields):
    """Store *fields* as a contact, serialize it, parse it back."""
    user = factories.UserFactory()
    contact = models.Contact.objects.create(
        owner=user, organization=user.organization, **fields
    )
    output = serialize_contacts([contact])
    (card,), errors = parse_vcf(output)
    assert errors == 0
    return card, output


def _card(*lines):
    body = "".join(f"{line}\r\n" for line in lines)
    return f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Brigny\r\n{body}END:VCARD\r\n"


@pytest.mark.parametrize(
    "line, kind",
    [
        ("KIND:org", "org"),
        ("KIND:Individual", "individual"),
        ("X-ABShowAs:COMPANY", "org"),
    ],
)
def test_kind_is_read_from_kind_and_apple_show_as(line, kind):
    (card,), _errors = parse_vcf(_card(line))
    assert card["kind"] == kind
    assert card["vcard_extra"] == {}


def test_an_unmodeled_kind_stays_a_raw_line():
    (card,), _errors = parse_vcf(_card("KIND:location"))
    assert card["kind"] == ""
    assert card["vcard_extra"] == {"KIND": ["KIND:location"]}


def test_an_org_card_writes_kind_and_apple_show_as():
    card, output = round_trip({"full_name": "Médiathèque de Brigny", "kind": "org"})
    assert "\r\nKIND:org\r\nX-ABShowAs:COMPANY\r\n" in output
    assert card["kind"] == "org"


def test_a_card_with_no_kind_writes_no_kind_line():
    card, output = round_trip({"full_name": "Ada"})
    assert "KIND" not in output
    assert "X-ABShowAs" not in output
    assert card["kind"] == ""


def test_gramgender_wins_over_the_honorific_of_n():
    (card,), _errors = parse_vcf(_card("N:Lovelace;Ada;;Mme;", "GRAMGENDER:neuter"))
    assert card["grammatical_gender"] == "neuter"
    assert card["vcard_extra"] == {}


def test_an_unknown_gramgender_stays_a_raw_line():
    (card,), _errors = parse_vcf(_card("N:Lovelace;Ada;;Mme;", "GRAMGENDER:x-other"))
    assert card["grammatical_gender"] == "feminine"
    assert card["vcard_extra"] == {"GRAMGENDER": ["GRAMGENDER:x-other"]}


@pytest.mark.parametrize(
    "gender, n_line",
    [
        ("feminine", "N:Lovelace;Ada;;Mme;"),
        ("masculine", "N:Lovelace;Ada;;M.;"),
        ("common", "N:Lovelace;Ada;;;"),
    ],
)
def test_grammatical_gender_writes_gramgender_and_an_honorific(gender, n_line):
    card, output = round_trip(
        {
            "full_name": "Ada",
            "given_name": "Ada",
            "family_name": "Lovelace",
            "grammatical_gender": gender,
        }
    )
    assert f"\r\n{n_line}\r\n" in output
    assert f"\r\nGRAMGENDER:{gender}\r\n" in output
    assert card["grammatical_gender"] == gender


def test_stored_kind_and_gender_lines_are_not_written_twice():
    contact = factories.ContactFactory(
        full_name="Brigny",
        kind="org",
        grammatical_gender="feminine",
        vcard_extra={
            "KIND": ["KIND:org"],
            "X-ABSHOWAS": ["X-ABShowAs:COMPANY"],
            "GRAMGENDER": ["GRAMGENDER:feminine"],
        },
    )
    output = serialize_contacts([contact])
    assert output.count("KIND:") == 1
    assert output.count("X-ABShowAs:") == 1
    assert output.count("GRAMGENDER:") == 1
