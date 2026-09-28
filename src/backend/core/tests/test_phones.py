"""Tests for the phone number forms used by search."""

# pylint: disable=missing-function-docstring

import pytest

from core.phones import phone_query_digits, phone_search_forms


def test_a_readable_number_is_indexed_in_every_form():
    assert phone_search_forms("06 39 98 63 09") == ["0639986309", "33639986309"]


def test_the_default_region_applies_to_numbers_without_a_country_code(settings):
    settings.CONTACTS_PHONE_DEFAULT_REGION = "BE"
    assert "32470123456" in phone_search_forms("0470 12 34 56")


def test_an_unreadable_number_keeps_its_digits():
    assert phone_search_forms("poste 1234") == ["1234"]
    assert not phone_search_forms("")
    assert not phone_search_forms("no digits")


@pytest.mark.parametrize(
    ("query", "digits"),
    [
        ("06 39 98", "063998"),
        ("+33 (0)6 39", "33639"),
        ("0033 6 39", "33639"),
        ("  3998  ", "3998"),
        ("+", None),
        ("Siou 06", None),
        ("jean-paul", None),
    ],
)
def test_query_digits(query, digits):
    assert phone_query_digits(query) == digits
