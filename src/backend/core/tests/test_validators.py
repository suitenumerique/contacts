"""Tests for the shared field validators."""

# pylint: disable=missing-function-docstring

import base64

from django.core.exceptions import ValidationError

import pytest

from core import factories
from core.validators import PHOTO_MAX_BYTES, validate_photo


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
