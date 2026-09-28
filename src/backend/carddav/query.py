"""``addressbook-query`` filters (RFC 6352 section 10.5).

Supported: ``prop-filter`` on FN, EMAIL, TEL and N with ``test`` anyof or
allof, ``is-not-defined``, and ``text-match`` with every match type, the
``negate-condition`` attribute and the i;unicode-casemap, i;ascii-casemap
and i;octet collations. Anything else is refused with the precondition
RFC 6352 names for it, so a client is never handed a superset.
"""

from core.models import Contact
from core.services.vcf_service import formatted_name, structured_name_text

from carddav.xml import CARD

SUPPORTED_PROPERTIES = ("FN", "EMAIL", "TEL", "N")
MATCH_TYPES = ("equals", "contains", "starts-with", "ends-with")


class UnsupportedFilterError(Exception):
    """A filter this server cannot evaluate; carries the failed condition."""

    def __init__(self, condition):
        super().__init__(condition)
        self.condition = condition


def _fold(collation, text):
    if collation == "i;octet":
        return text
    if collation == "i;ascii-casemap":
        return "".join(char.lower() if char.isascii() else char for char in text)
    return text.casefold()


def property_values(contact, name):
    """The values of property *name* on *contact*'s card, as text.

    A group card carries its name as FN and N, and no EMAIL or TEL.
    """
    if not isinstance(contact, Contact):
        if name == "FN":
            return [contact.name]
        return [f"{contact.name};;;;"] if name == "N" else []
    merged = contact.merged_value
    if name == "FN":
        return [formatted_name(contact)]
    if name == "N":
        return [structured_name_text(contact)]
    field = "emails" if name == "EMAIL" else "phones"
    return [
        entry["value"]
        for entry in merged(field) or []
        if isinstance(entry, dict) and entry.get("value")
    ]


def _test(element):
    test = element.get("test", "anyof")
    if test not in ("anyof", "allof"):
        raise UnsupportedFilterError(CARD("supported-filter"))
    return all if test == "allof" else any


def _text_match(element):
    collation = element.get("collation", "i;unicode-casemap")
    if collation not in ("i;unicode-casemap", "i;ascii-casemap", "i;octet"):
        raise UnsupportedFilterError(CARD("supported-collation"))
    match_type = element.get("match-type", "contains")
    if match_type not in MATCH_TYPES:
        raise UnsupportedFilterError(CARD("supported-filter"))
    negate = element.get("negate-condition", "no") == "yes"
    needle = _fold(collation, element.text or "")

    def matches(value):
        value = _fold(collation, value)
        if match_type == "equals":
            found = value == needle
        elif match_type == "starts-with":
            found = value.startswith(needle)
        elif match_type == "ends-with":
            found = value.endswith(needle)
        else:
            found = needle in value
        return found != negate

    return matches


def _prop_filter(element):
    name = (element.get("name") or "").upper()
    if name not in SUPPORTED_PROPERTIES:
        raise UnsupportedFilterError(CARD("supported-filter"))
    combine = _test(element)
    undefined = False
    text_matches = []
    for child in element:
        if child.tag == CARD("is-not-defined"):
            undefined = True
        elif child.tag == CARD("text-match"):
            text_matches.append(_text_match(child))
        else:
            raise UnsupportedFilterError(CARD("supported-filter"))

    def matches(contact):
        values = property_values(contact, name)
        if undefined:
            return not values
        if not text_matches:
            return bool(values)
        # A text-match holds when any instance of the property matches it.
        return combine(any(match(value) for value in values) for match in text_matches)

    return matches


def compile_filter(element):
    """Turn a ``CARDDAV:filter`` element into a ``contact -> bool`` test.

    A missing or empty filter matches every card. Raises
    :class:`UnsupportedFilterError` for anything this module cannot
    evaluate exactly.
    """
    if element is None:
        return lambda contact: True
    combine = _test(element)
    prop_filters = []
    for child in element:
        if child.tag != CARD("prop-filter"):
            raise UnsupportedFilterError(CARD("supported-filter"))
        prop_filters.append(_prop_filter(child))
    if not prop_filters:
        return lambda contact: True
    return lambda contact: combine(match(contact) for match in prop_filters)
