"""Contacts and groups as JSContact cards (RFC 9553).

The external API speaks JSContact, the IETF's JSON format for contact data,
so other apps read Contacts with the standard's vocabulary. Only what a
picker needs is written: a card carries no more than its row shows in
Contacts. The conversion lives here, apart from the API, so any later
export can reuse it.

Conformance notes:

- ``@type`` is written on the card only: RFC 9553 makes it optional on
  nested objects, whose type follows from where they sit.
- Multi-valued properties are id-keyed maps; ids are positions (``e1``,
  ``e2``...), stable for a given card content, not across edits.
- What JSContact has no property for is written as vendor properties,
  prefixed with the ``JSCONTACT_VENDOR_PREFIX`` domain (RFC 9553, 1.8).
"""

from django.conf import settings

from core.enums import Scope

VERSION = "1.0"

INDIVIDUAL = "individual"
GROUP = "group"
KINDS = (INDIVIDUAL, GROUP)

UID_PREFIX = "urn:uuid:"

# Contacts' email types to JSContact contexts. "home" is JSContact's
# "private"; anything else is kept as a free-text label.
_CONTEXTS = {"work": "work", "home": "private", "private": "private"}
# Contacts' name parts to JSContact name component kinds (RFC 9553, 2.2.1).
_NAME_COMPONENTS = (
    ("name_prefix", "title"),
    ("given_name", "given"),
    ("middle_name", "given2"),
    ("family_name", "surname"),
    ("name_suffix", "credential"),
)
# Civility to the grammatical gender to address someone with (speakToAs).
_GENDERS = {"mr": "masculine", "mrs": "feminine"}


def vendor(name):
    """The vendor property *name*, prefixed: ``numerique.gouv.fr:name``."""
    return f"{settings.JSCONTACT_VENDOR_PREFIX}:{name}"


def uid(pk):
    """The card uid of a row: its id as a ``urn:uuid:`` URI."""
    return f"{UID_PREFIX}{pk}"


def parse_uid(value):
    """The row id in a card uid (``urn:uuid:…`` or a bare UUID), as text."""
    value = (value or "").strip()
    if value.lower().startswith(UID_PREFIX):
        return value[len(UID_PREFIX) :]
    return value


def _card(pk, kind):
    return {"@type": "Card", "version": VERSION, "uid": uid(pk), "kind": kind}


def _emails(items):
    """Typed address list to an EmailAddress map, the first one preferred."""
    emails = {}
    for item in items or []:
        if not isinstance(item, dict) or not item.get("value"):
            continue
        entry = {"address": item["value"]}
        email_type = (item.get("type") or "").strip()
        if context := _CONTEXTS.get(email_type.lower()):
            entry["contexts"] = {context: True}
        elif email_type and email_type.lower() != "other":
            entry["label"] = email_type
        if not emails:
            entry["pref"] = 1
        emails[f"e{len(emails) + 1}"] = entry
    return emails


def contact_card(contact):
    """*contact* as a JSContact card (merged, for an overlay)."""
    value = contact.merged_value
    card = _card(contact.pk, INDIVIDUAL)

    name = {"full": contact.display_name}
    components = [
        {"kind": kind, "value": value(field)}
        for field, kind in _NAME_COMPONENTS
        if value(field)
    ]
    if components:
        name["components"] = components
    card["name"] = name

    if nickname := value("nickname"):
        card["nicknames"] = {"k1": {"name": nickname}}
    if gender := _GENDERS.get(value("civility")):
        card["speakToAs"] = {"grammaticalGender": gender}
    if emails := _emails(value("emails")):
        card["emails"] = emails

    company, department = value("company"), value("department")
    if company or department:
        organization = {"name": company} if company else {}
        if department:
            organization["units"] = [{"name": department}]
        card["organizations"] = {"o1": organization}
    if job_title := value("job_title"):
        card["titles"] = {"t1": {"kind": "title", "name": job_title}}
    # Photos stored inline (data: URIs) are left out: they would make every
    # typeahead row heavy.
    photo = value("photo") or ""
    if photo.startswith("https://"):
        card["media"] = {"m1": {"kind": "photo", "uri": photo}}

    card[vendor("isOrg")] = contact.scope == Scope.ORGANIZATION
    card[vendor("source")] = contact.source
    return card


def group_card(group):
    """*group* as a JSContact card of kind "group".

    Its members are not listed (``members`` would hold every one of them):
    their count is, and the group members endpoint lists them.
    """
    card = _card(group.pk, GROUP)
    card["name"] = {"full": group.name}
    if group.email:
        card["emails"] = {"e1": {"address": group.email, "pref": 1}}
    card[vendor("isOrg")] = group.scope == Scope.ORGANIZATION
    card[vendor("source")] = group.source
    if hasattr(group, "members_count"):
        card[vendor("membersCount")] = group.members_count
    return card
