"""Phone numbers as search sees them.

A number is stored as typed ("+33 6 39 98 63 09", "06.39.98.63.09"), so
search indexes digit strings instead: the digits as written, and for a
number libphonenumber can read, its international digits (country code
first) and its national digits (with the trunk prefix, "0" in France).
Any way of typing the number then finds it with a plain substring match.
"""

import re

from django.conf import settings

import phonenumbers

_NON_DIGITS = re.compile(r"\D")
# Only digits and the separators people type in phone numbers.
_PHONE_QUERY = re.compile(r"\+?[\d\s().\-/]*\d[\d\s().\-/]*")


def phone_search_forms(value):
    """The digit strings under which the number *value* is indexed."""
    digits = _NON_DIGITS.sub("", value or "")
    if not digits:
        return []
    forms = {digits}
    try:
        number = phonenumbers.parse(value, settings.CONTACTS_PHONE_DEFAULT_REGION)
    except phonenumbers.NumberParseException:
        return sorted(forms)
    if not phonenumbers.is_possible_number(number):
        # Extensions, short codes, notes: parsing them leniently would
        # index made-up forms.
        return sorted(forms)
    for number_format in (
        phonenumbers.PhoneNumberFormat.E164,
        phonenumbers.PhoneNumberFormat.NATIONAL,
    ):
        formatted = phonenumbers.format_number(number, number_format)
        forms.add(_NON_DIGITS.sub("", formatted))
    return sorted(forms)


def phone_query_digits(query):
    """The digits to look for when *query* is a phone number, else None.

    "+33 6 39" looks for the international digits "33639", "06 39 98" for
    "063998". A leading "00" (the international call prefix in most of
    Europe) is dropped so "0033 6" finds "+33 6" numbers too, and so is
    the "(0)" trunk prefix of "+33 (0)6 39".
    """
    query = query.strip().replace("(0)", "")
    if not _PHONE_QUERY.fullmatch(query):
        return None
    digits = _NON_DIGITS.sub("", query)
    if not query.startswith("+") and digits.startswith("00"):
        digits = digits[2:]
    return digits or None
