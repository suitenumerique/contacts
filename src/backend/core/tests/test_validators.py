"""Tests for the shared field validators."""

# pylint: disable=missing-function-docstring

import base64

from django.core.exceptions import ValidationError

import pytest

from core import factories
from core.validators import (
    MAX_TYPED_VALUES,
    PHOTO_MAX_BYTES,
    broken_value_rule,
    validate_photo,
)


def data_uri(size, mime="image/png"):
    return f"data:{mime};base64,{base64.b64encode(b'x' * size).decode()}"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://example.com/avatar.png",
        data_uri(10),
        data_uri(10, "image/jpeg"),
        data_uri(10, "image/gif"),
        data_uri(10, "image/webp"),
        data_uri(PHOTO_MAX_BYTES),
    ],
    ids=lambda value: value[:40],
)
def test_accepted_photos(value):
    validate_photo(value)


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com/avatar.png",
        "javascript:alert(1)",
        "//example.com/avatar.png",
        data_uri(10, "image/svg+xml"),
        data_uri(10, "text/html"),
        "data:image/png,rawbytes",
        "data:image/png;base64,not base64!",
        "data:image/png;base64,abc",
        data_uri(PHOTO_MAX_BYTES + 1),
        "https://" + "a" * 2050 + ".com",
    ],
    ids=lambda value: value[:40],
)
def test_refused_photos(value):
    with pytest.raises(ValidationError):
        validate_photo(value)


@pytest.mark.django_db
def test_model_validates_the_photo():
    """Every write path (imports, CardDAV, admin) goes through full_clean."""
    with pytest.raises(ValidationError) as excinfo:
        factories.ContactFactory(photo="javascript:alert(1)")
    assert "photo" in excinfo.value.message_dict


VALID_VALUES = {
    "emails": "jeanne@example.org",
    "phones": "+33 1 99 00 04 05",
    "addresses": "1 rue de la Mairie\n01000 Brigny",
    "urls": "example.org/jeanne",
}


@pytest.mark.parametrize(
    ("field", "value", "rule"),
    [
        ("emails", "not-an-email", "invalid_email"),
        ("phones", "1" * 65, "invalid_phone"),
        ("urls", "javascript:alert(1)", "invalid_url"),
        ("urls", "data:text/html,<script>alert(1)</script>", "invalid_url"),
        ("urls", "ftp://example.org/x", "invalid_url"),
        ("urls", "not a url", "invalid_url"),
        ("addresses", "x" * 1025, "value_too_long"),
        ("phones", "1" * 1025, "value_too_long"),
    ],
    ids=lambda value: value[:20],
)
def test_broken_value_rule_names_the_rule_and_the_value(field, value, rule):
    entries = [
        {"type": "work", "value": VALID_VALUES[field]},
        {"type": "work", "value": value},
    ]
    assert broken_value_rule(field, entries) == (rule, value)


@pytest.mark.parametrize("field", VALID_VALUES)
def test_valid_values_break_no_value_rule(field):
    entries = [{"type": "work", "value": VALID_VALUES[field]}] * MAX_TYPED_VALUES
    assert broken_value_rule(field, entries) == ("", "")


def test_too_many_values_break_a_value_rule():
    phones = [{"type": "", "value": "+33199000405"}] * (MAX_TYPED_VALUES + 1)
    assert broken_value_rule("phones", phones) == ("too_many_values", "")
