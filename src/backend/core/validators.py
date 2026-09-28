"""Reusable field validators for the core app."""

import base64
import binascii
import re

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

PHOTO_MAX_BYTES = 200 * 1024
PHOTO_URL_MAX_LENGTH = 2048

_PHOTO_DATA_URI = re.compile(
    r"^data:image/(?:png|jpeg|gif|webp);base64,(?P<data>[A-Za-z0-9+/]*={0,2})$"
)
_https_url = URLValidator(schemes=["https"])


def validate_photo(value):
    """Accept an empty value, an https URL or a small base64 image data URI.

    Data URIs are limited to PNG, JPEG, GIF and WebP, and to
    ``PHOTO_MAX_BYTES`` once decoded.
    """
    if not value:
        return
    if value.startswith("data:"):
        match = _PHOTO_DATA_URI.match(value)
        if match is None:
            raise ValidationError(
                "Photo must be a base64 PNG, JPEG, GIF or WebP data URI.",
                code="invalid_photo",
            )
        data = match.group("data")
        # Bounded before decoding so an oversized payload is never decoded.
        if len(data) > (PHOTO_MAX_BYTES + 2) // 3 * 4:
            raise ValidationError("Photo is too large.", code="photo_too_large")
        try:
            decoded = base64.b64decode(data, validate=True)
        except binascii.Error as exc:
            raise ValidationError(
                "Photo is not valid base64.", code="invalid_photo"
            ) from exc
        if len(decoded) > PHOTO_MAX_BYTES:
            raise ValidationError("Photo is too large.", code="photo_too_large")
        return
    if len(value) > PHOTO_URL_MAX_LENGTH:
        raise ValidationError("Photo URL is too long.", code="invalid_photo")
    try:
        _https_url(value)
    except ValidationError as exc:
        raise ValidationError(
            "Photo must be an https URL or an image data URI.", code="invalid_photo"
        ) from exc
