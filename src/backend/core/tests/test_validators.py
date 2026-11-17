"""Tests for the shared field validators."""

# pylint: disable=missing-function-docstring

import base64

from django.core.exceptions import ValidationError

import pytest

from core import factories
from core.validators import (
    MAX_TYPED_VALUES,
    PHOTO_MAX_BYTES,
    split_typed_values,
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
    ("field", "value"),
    [
        ("emails", "not-an-email"),
        ("phones", "1" * 65),
        ("urls", "javascript:alert(1)"),
        ("urls", "data:text/html,<script>alert(1)</script>"),
        ("urls", "ftp://example.org/x"),
        ("urls", "not a url"),
        ("addresses", "x" * 1025),
        ("addresses", " "),
    ],
    ids=lambda value: value[:20],
)
def test_split_refuses_a_value_that_breaks_a_rule(field, value):
    valid = {"type": "work", "value": VALID_VALUES[field]}
    broken = {"type": "work", "value": value}
    assert split_typed_values(field, [broken, valid]) == ([valid], [broken])


@pytest.mark.parametrize("type_value", ["<script>", "x" * 33, "home office"])
def test_split_refuses_a_type_that_breaks_the_pattern(type_value):
    broken = {"type": type_value, "value": "a@example.org"}
    assert split_typed_values("emails", [broken]) == ([], [broken])


@pytest.mark.parametrize("field", VALID_VALUES)
def test_split_keeps_valid_values(field):
    entries = [
        {"type": "x-custom-1", "value": VALID_VALUES[field]},
        {"type": "", "value": VALID_VALUES[field]},
        {"value": VALID_VALUES[field]},
    ]
    assert split_typed_values(field, entries) == (entries, [])


def test_split_keeps_the_first_values_up_to_the_limit():
    phones = [
        {"type": "", "value": f"+3319900{index:04}"}
        for index in range(MAX_TYPED_VALUES + 2)
    ]
    assert split_typed_values("phones", phones) == (
        phones[:MAX_TYPED_VALUES],
        phones[MAX_TYPED_VALUES:],
    )
