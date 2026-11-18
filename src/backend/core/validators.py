"""Reusable field validators for the core app."""

import base64
import binascii
import re

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email

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


# The value rules of the multi-valued contact fields (emails, phones,
# addresses, urls). An entry is {"type": ..., "value": ...}. Every writer
# of a contact applies these rules.
MAX_TYPED_VALUES = 50
TYPE_MAX_LENGTH = 32
TYPE_PATTERN = rf"^[A-Za-z0-9-]{{0,{TYPE_MAX_LENGTH}}}$"
VALUE_MAX_LENGTH = 1024
PHONE_MAX_LENGTH = 64

_URL_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_http_url = URLValidator(schemes=["http", "https"])


def validate_email_value(value):
    """Refuse a value that is not an email address."""
    try:
        validate_email(value)
    except ValidationError as exc:
        raise ValidationError(f"Enter a valid email address: {value}") from exc


def validate_phone_value(value):
    """Refuse a value that is too long for a phone number."""
    if len(value) > PHONE_MAX_LENGTH:
        raise ValidationError(f"Phone numbers are {PHONE_MAX_LENGTH} characters max.")


def validate_url_value(value):
    """Accept an http(s) URL, or a URL without a scheme ("example.com").

    The frontend shows the value as a link, and adds https:// to a value
    without a scheme. Thus a value without a scheme is checked as an https
    URL, and every other scheme (javascript:, data:, ...) is refused.
    """
    candidate = value if _URL_SCHEME.match(value) else f"https://{value}"
    try:
        _http_url(candidate)
    except ValidationError as exc:
        raise ValidationError(f"Enter a valid URL: {value}") from exc


_FIELD_RULES = {
    "emails": ("invalid_email", validate_email_value),
    "phones": ("invalid_phone", validate_phone_value),
    "urls": ("invalid_url", validate_url_value),
}


def _broken_rule(field, value):
    """The name of the value rule of *field* that *value* breaks, or ""."""
    if len(value) > VALUE_MAX_LENGTH:
        return "value_too_long"
    if field not in _FIELD_RULES:
        return ""
    rule, validate = _FIELD_RULES[field]
    try:
        validate(value)
    except ValidationError:
        return rule
    return ""


def broken_value_rule(field, entries):
    """The first value rule that the entries of a multi-valued contact field break.

    Returns ``(rule, value)``: the name of the rule and the value that
    breaks it, or ``("", "")`` when the entries break no rule. The rules
    are ``too_many_values`` (it has no value), ``value_too_long``,
    ``invalid_email``, ``invalid_phone`` and ``invalid_url``. The type of
    an entry and a blank value are not checked.
    """
    if len(entries) > MAX_TYPED_VALUES:
        return "too_many_values", ""
    for entry in entries:
        if rule := _broken_rule(field, entry["value"]):
            return rule, entry["value"]
    return "", ""
