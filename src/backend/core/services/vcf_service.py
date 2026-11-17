"""vCard (.vcf) parsing and serialization.

Reads vCard 3.0 and 4.0 (vobject splits and unfolds the lines), writes
3.0. Values are decoded and encoded here rather than by vobject, whose text
behavior truncates any value at its first unescaped comma.

Every property maps to Contact columns, and whatever the columns cannot
hold is kept in ``Contact.vcard_extra`` as raw content lines keyed by
property name, so parse -> serialize -> parse is stable:

- properties without a column (UID, KEY, X-*, ...) verbatim;
- a modeled property whose line carries more than its column (a group,
  extra TYPE values, ADR components, a list of values in one N component,
  more ORG units) as a "shadow" line. Serialization writes the shadow back
  instead of rebuilding the line, as long as it still decodes to the
  column's current value; once the column is edited it is ignored;
- later instances of a single-column property (a second ORG, NOTE, ...)
  verbatim, written back after the modeled lines.

Mappings: FN <-> full_name; N <-> family_name, given_name, middle_name,
civility (``M.``/``Mme``) plus name_prefix, name_suffix; NICKNAME <->
nickname; ORG <-> company and department; PHOTO <-> photo; CATEGORIES
<-> the owner's labels (parsed as ``label_names``). A card with only FN
keeps it whole: splitting it would misfile single names, multi-word family
names and family-name-first orders. A row without an owner has no labels:
its stored CATEGORIES lines are written back verbatim.
"""

import base64
import binascii
import io
import logging
import re

from django.core.exceptions import ValidationError

import vobject
from vobject.base import VObjectError

from core.models import NAME_PARTS, Civility, compose_name
from core.utils import decode_text
from core.validators import validate_photo

logger = logging.getLogger(__name__)

# Dropped on parse: VERSION and PRODID describe the file, REV is rewritten.
_SKIPPED = frozenset({"VERSION", "PRODID", "REV"})
_SINGLE = frozenset({"FN", "N", "NICKNAME", "ORG", "TITLE", "NOTE", "PHOTO"})
# Only the first instance of a _SINGLE property fills its column. Later
# ones are kept verbatim under this key (not a property name) and written
# after the modeled lines. A second N is dropped: both vCard 3 and 4 allow
# one at most.
_MORE = "+"
_REPEATABLE = _SINGLE - {"N"}
_TYPED = {"EMAIL": "emails", "TEL": "phones", "ADR": "addresses", "URL": "urls"}
_DEFAULT_TYPE = {"EMAIL": "other", "TEL": "other", "ADR": "other", "URL": ""}
_TEXT = {"TITLE": "job_title", "NOTE": "notes", "NICKNAME": "nickname"}
# Modeled properties whose original line may be kept as a shadow.
_SHADOWED = frozenset({"FN", "N", "ORG", *_TEXT, *_TYPED})

_IGNORED_TYPES = frozenset({"internet", "pref", "voice"})
_TYPE_RE = re.compile(r"[A-Za-z0-9-]{1,32}")
_NAME_RE = re.compile(r"[A-Za-z0-9-]+")
_UNESCAPE_RE = re.compile(r"\\(.)", re.DOTALL)
_PHOTO_URI_RE = re.compile(
    r"^data:image/(png|jpeg|gif|webp);base64,([A-Za-z0-9+/]*={0,2})$"
)
_PHOTO_MAGIC = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF8", "gif"),
)

_PREFIX_TO_CIVILITY = {
    "m": Civility.MR,
    "m.": Civility.MR,
    "mr": Civility.MR,
    "mr.": Civility.MR,
    "monsieur": Civility.MR,
    "mme": Civility.MRS,
    "mme.": Civility.MRS,
    "mrs": Civility.MRS,
    "mrs.": Civility.MRS,
    "madame": Civility.MRS,
    "ms": Civility.MRS,
    "ms.": Civility.MRS,
    # Mademoiselle is no longer used by administrations (circulaire du
    # 21 février 2012): read as Madame.
    "mlle": Civility.MRS,
    "mlle.": Civility.MRS,
    "melle": Civility.MRS,
    "mademoiselle": Civility.MRS,
    "miss": Civility.MRS,
}
_CIVILITY_PREFIX = {Civility.MR.value: "M.", Civility.MRS.value: "Mme"}

FOLD_AT = 75  # octets, RFC 2426 section 2.6


class InvalidVCardError(ValueError):
    """Raised when a body is not exactly one readable vCard."""


# --- text encoding ----------------------------------------------------


def _unescape(raw):
    return _UNESCAPE_RE.sub(
        lambda match: "\n" if match.group(1) in "nN" else match.group(1), raw
    )


def _escape(text):
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    return (
        text.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _split(raw, separator):
    """Split *raw* on unescaped *separator*, leaving escapes in place."""
    parts, current, index = [], [], 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and index + 1 < len(raw):
            current.append(raw[index : index + 2])
            index += 2
            continue
        if char == separator:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    parts.append("".join(current))
    return parts


def _parts(raw, count):
    parts = _split(raw, ";")
    return (parts + [""] * count)[:count]


def _raw(line):
    """The unfolded text of a parsed content line, still encoded."""
    text = f"{line.group}.{line.name}" if line.group else line.name
    for key, values in line.params.items():
        rendered = [
            f'"{v}"' if any(c in v for c in ":;,") else v
            for v in values
            if '"' not in v
        ]
        text += f";{key}={','.join(rendered)}"
    for param in line.singletonparams:
        text += f";{param}"
    # A quoted-printable value arrives decoded and may span lines.
    value = re.sub(r"\r\n|\r|\n", r"\\n", line.value)
    return f"{text}:{value}"


def _fold(line):
    """Fold one content line at 75 octets without splitting a character."""
    if line.startswith("PHOTO"):
        # Apple Contacts misreads folded base64, as vobject also assumes.
        return line + "\r\n"
    chunks, current, size = [], [], 0
    for char in line:
        width = len(char.encode("utf-8"))
        limit = FOLD_AT if not chunks else FOLD_AT - 1
        if size + width > limit:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += width
    chunks.append("".join(current))
    return "\r\n ".join(chunks) + "\r\n"


def _line_from_text(name, text):
    """Parse a stored raw line back into a content line, or None."""
    if "\r" in text or "\n" in text:
        return None
    try:
        line = vobject.base.textLineToContentLine(text)
    except VObjectError:
        return None
    return line if line.name == name else None


# --- property values --------------------------------------------------


def _types(line):
    types = list(line.singletonparams)
    for value in line.params.get("TYPE", []):
        types += value.split(",")
    return types


def _type_param(line, default=""):
    """The first meaningful TYPE, lowercased ("work", "home", "cell"...)."""
    for raw_type in _types(line):
        type_value = raw_type.strip().lower()
        if type_value not in _IGNORED_TYPES and _TYPE_RE.fullmatch(type_value):
            return type_value
    return default


def _type_suffix(type_value):
    """``;TYPE=X`` for a safe type, "" otherwise (no parameter injection)."""
    if type_value and _TYPE_RE.fullmatch(type_value):
        return f";TYPE={type_value.upper()}"
    return ""


def _format_address(parts):
    box, extended, street, locality, region, code, country = (
        part.strip() for part in parts
    )
    city = " ".join(part for part in (code, locality) if part)
    return "\n".join(
        part for part in (box, extended, street, city, region, country) if part
    )


def _decode_photo(line):
    """A PHOTO line as a value ``validate_photo`` accepts, or ""."""
    value = line.value.strip()
    encodings = {v.lower() for v in line.params.get("ENCODING", [])}
    encodings |= {p.lower() for p in line.singletonparams}
    if encodings & {"b", "base64"}:
        try:
            data = base64.b64decode("".join(value.split()), validate=True)
        except binascii.Error, ValueError:
            return ""
        subtype = next(
            (sub for magic, sub in _PHOTO_MAGIC if data.startswith(magic)), ""
        )
        if not subtype and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            subtype = "webp"
        if not subtype:
            declared = (line.params.get("TYPE") or [""])[0].lower()
            subtype = declared.removeprefix("image/")
        value = f"data:image/{subtype};base64,{base64.b64encode(data).decode()}"
    try:
        validate_photo(value)
    except ValidationError:
        return ""
    return value


def _values(component):
    """The unescaped, non-empty values of a comma-separated list."""
    values = (_unescape(part).strip() for part in _split(component, ","))
    return [value for value in values if value]


def _decode_n(value):
    """Split N into its columns; the first civility-like prefix is civility."""
    family, given, middle, prefix, suffix = _parts(value, 5)
    civility, prefixes = Civility.UNSPECIFIED, []
    for item in _values(prefix):
        if not civility and item.lower() in _PREFIX_TO_CIVILITY:
            civility = _PREFIX_TO_CIVILITY[item.lower()]
        else:
            prefixes.append(item)
    return {
        "family_name": " ".join(_values(family)),
        "given_name": " ".join(_values(given)),
        "middle_name": " ".join(_values(middle)),
        "civility": str(civility),
        "name_prefix": " ".join(prefixes),
        "name_suffix": " ".join(_values(suffix)),
    }


def _decode(name, line):  # pylint: disable=too-many-return-statements
    """The column value(s) one modeled line stands for."""
    value = line.value
    if name in ("FN", "TITLE", "NOTE"):
        return _unescape(value).strip()
    if name == "NICKNAME":
        return ", ".join(_values(value))
    if name == "N":
        return _decode_n(value)
    if name == "ORG":
        units = [_unescape(part).strip() for part in _split(value, ";")]
        return {
            "company": units[0],
            "department": ", ".join(unit for unit in units[1:] if unit),
        }
    if name == "PHOTO":
        return _decode_photo(line)
    if name == "ADR":
        text = _format_address([_unescape(p) for p in _parts(value, 7)])
    else:
        text = _unescape(value).strip()
        if name == "TEL" and text.lower().startswith("tel:"):
            text = text[4:]
    return {"type": _type_param(line, _DEFAULT_TYPE[name]), "value": text}


def _encode(name, item):
    """The content line text written for a modeled value."""
    if name in ("FN", "NICKNAME", "TITLE", "NOTE"):
        return f"{name}:{_escape(item)}"
    if name == "N":
        prefixes = (_CIVILITY_PREFIX.get(item["civility"], ""), item["name_prefix"])
        components = (
            item["family_name"],
            item["given_name"],
            item["middle_name"],
            ",".join(_escape(prefix) for prefix in prefixes if prefix),
            item["name_suffix"],
        )
        return "N:" + ";".join(
            part if index == 3 else _escape(part)
            for index, part in enumerate(components)
        )
    if name == "ORG":
        department = f";{_escape(item['department'])}" if item["department"] else ""
        return f"ORG:{_escape(item['company'])}{department}"
    if name == "ADR":
        return f"ADR{_type_suffix(item['type'])}:;;{_escape(item['value'])};;;;"
    return f"{name}{_type_suffix(item['type'])}:{_escape(item['value'])}"


def _encode_photo(photo):
    try:
        validate_photo(photo)
    except ValidationError:
        return None
    if match := _PHOTO_URI_RE.match(photo or ""):
        return f"PHOTO;ENCODING=b;TYPE={match.group(1).upper()}:{match.group(2)}"
    if photo:
        return f"PHOTO;VALUE=uri:{photo}"
    return None


def _default_full_name(name_parts, emails):
    """What FN falls back to when the card has no full name of its own.

    vCard requires FN, so a card without any name gets its first email.
    """
    if composed := compose_name(*name_parts):
        return composed
    for email in emails or []:
        if email.get("value"):
            return email["value"]
    return ""


# --- parsing ----------------------------------------------------------


def _read_components(data):
    """Yield ``(name, lines)`` for each top-level component in *data*.

    Lines keep their encoded values (only quoted-printable is decoded, by
    ``ContentLine``). Unparseable lines are skipped; an unbalanced
    BEGIN/END raises :class:`InvalidVCardError`.
    """
    stack, lines = [], []
    stream = io.StringIO(decode_text(data))
    for text, number in vobject.base.getLogicalLines(stream):
        if not text.strip():
            continue
        try:
            line = vobject.base.textLineToContentLine(text, number)
        except VObjectError, LookupError, ValueError:
            continue
        name, value = line.name.upper(), str(line.value).strip().upper()
        if name == "BEGIN":
            stack.append(value)
            if len(stack) == 1:
                lines = []
        elif name == "END":
            if not stack or stack.pop() != value:
                raise InvalidVCardError(f"unexpected END:{value} at line {number}")
            if not stack:
                yield value, lines
        elif len(stack) == 1:
            lines.append(line)
    if stack:
        raise InvalidVCardError(f"BEGIN:{stack[0]} is never closed")


def contains_vcard(data):
    """Return True if *data* contains a vCard component.

    Also return True if a BEGIN has no END. The import reports this file
    as an error.
    """
    try:
        return any(name == "VCARD" for name, _lines in _read_components(data))
    except InvalidVCardError:
        return True


def parse_vcf(data):
    """Parse VCF bytes/str into a list of contact field dicts.

    Returns ``(cards, errors)`` where each card is a dict of Contact
    constructor kwargs and ``errors`` counts unparseable components.
    Group cards are left out: they are not contacts.
    """
    cards, _groups, errors = parse_vcf_file(data)
    return cards, errors


def parse_vcf_file(data):
    """Parse VCF bytes/str into ``(cards, groups, errors)``.

    *cards* as ``parse_vcf``; *groups* are the group cards' fields (see
    ``_parse_group``).
    """
    cards, groups = [], []
    errors = 0
    try:
        for name, lines in _read_components(data):
            if name != "VCARD":
                continue
            try:
                if _is_group(lines):
                    groups.append(_parse_group(lines))
                else:
                    cards.append(_parse_card(lines))
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Failed to parse a vCard component")
                errors += 1
    except InvalidVCardError:
        # A truncated card ends the file; the cards before it stand.
        logger.info("Unreadable vCard data", exc_info=True)
        errors += 1
    return cards, groups, errors


def parse_one_card(data):
    """Parse a body that must hold exactly one vCard (a CardDAV PUT).

    Returns ``(is_group, fields)``: group fields (see ``_parse_group``) for
    a group card, Contact kwargs otherwise. Raises
    :class:`InvalidVCardError` for anything else.
    """
    components = list(_read_components(data))
    if len(components) != 1 or components[0][0] != "VCARD":
        raise InvalidVCardError("the body must hold exactly one VCARD")
    lines = components[0][1]
    is_group = _is_group(lines)
    try:
        return is_group, (_parse_group(lines) if is_group else _parse_card(lines))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise InvalidVCardError(str(exc)) from exc


# --- group cards ------------------------------------------------------
#
# Apple's form, which vCard 3 clients (iOS, macOS, DAVx5 set to "groups
# are separate vCards") read and write: a card marked
# X-ADDRESSBOOKSERVER-KIND:group listing its members' UIDs as
# X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:<UID>. The vCard 4 KIND and MEMBER
# are read too.

_KIND_NAMES = frozenset({"KIND", "X-ADDRESSBOOKSERVER-KIND"})
_MEMBER_NAMES = frozenset({"MEMBER", "X-ADDRESSBOOKSERVER-MEMBER"})
# Where a group keeps the member lines that name no known card yet.
PENDING_MEMBERS = "X-ADDRESSBOOKSERVER-MEMBER"


def _is_group(lines):
    return any(
        line.name.upper() in _KIND_NAMES and str(line.value).strip().lower() == "group"
        for line in lines
    )


def member_ref(uid):
    """The MEMBER value naming the card whose UID is *uid*."""
    return uid if uid.lower().startswith("urn:") else f"urn:uuid:{uid}"


def ref_uids(ref):
    """The UIDs a MEMBER value may name: itself, and without ``urn:uuid:``."""
    uids = {ref}
    if ref.lower().startswith("urn:uuid:"):
        uids.add(ref[len("urn:uuid:") :])
    return uids


def card_uid(card):
    """The UID written on the card of a contact (merged) or a group."""
    merged = getattr(card, "merged_value", None)
    extra = (merged("vcard_extra") if merged else card.vcard_extra) or {}
    return stored_uid(extra) or str(card.pk)


def stored_uid(extra):
    """The UID kept in a ``vcard_extra``, or "" when there is none."""
    for text in extra.get("UID", []):
        line = _line_from_text("UID", text)
        if line is not None and (uid := _unescape(line.value).strip()):
            return uid
    return ""


def _parse_group(lines):
    """Group fields of a group card: name, member refs, unmodeled lines."""
    fields = {"name": "", "member_refs": [], "vcard_extra": {}}
    family = ""
    for line in lines:
        name = line.name.upper()
        if name in _SKIPPED or name in _KIND_NAMES:
            continue
        if name in _MEMBER_NAMES:
            ref = str(line.value).strip()
            if ref and ref not in fields["member_refs"]:
                fields["member_refs"].append(ref)
        elif name == "FN":
            fields["name"] = fields["name"] or _unescape(line.value).strip()
        elif name == "N":
            family = family or " ".join(_values(_parts(line.value, 1)[0]))
        elif _NAME_RE.fullmatch(name):
            fields["vcard_extra"].setdefault(name, []).append(_raw(line))
    fields["name"] = fields["name"] or family
    return fields


def serialize_group(group, member_uids):
    """Serialize a ContactGroup to a vCard 3.0 group card.

    *member_uids* are the UIDs of the member cards in the same book. The
    group's pending member lines (members not known yet) follow them.
    """
    extra = group.vcard_extra or {}
    name = _escape(group.name)
    lines = ["VERSION:3.0", f"FN:{name}", f"N:{name};;;;"]
    lines.append("X-ADDRESSBOOKSERVER-KIND:group")
    refs = [member_ref(uid) for uid in sorted(member_uids)]
    for text in extra.get(PENDING_MEMBERS, []):
        line = _line_from_text(PENDING_MEMBERS, text)
        if line is not None and (ref := str(line.value).strip()) not in refs:
            refs.append(ref)
    lines += [f"X-ADDRESSBOOKSERVER-MEMBER:{ref}" for ref in refs]
    if "UID" not in extra:
        lines.append(f"UID:{group.pk}")
    lines += _unmodeled_lines(extra, {PENDING_MEMBERS})
    body = "".join(_fold(line) for line in lines)
    return f"BEGIN:VCARD\r\n{body}END:VCARD\r\n"


def _set_aside(name, line, fields, seen):
    """Handle a line that fills no column itself; False for one that does.

    Skipped, CATEGORIES (label names), a later instance of a one-column
    property, or a property without a column (kept verbatim).
    """
    extra = fields["vcard_extra"]
    if name in _SKIPPED:
        return True
    if name == "CATEGORIES":
        for label_name in _values(line.value):
            if label_name not in fields["label_names"]:
                fields["label_names"].append(label_name)
        return True
    if name in _SINGLE and name in seen:
        if name in _REPEATABLE:
            extra.setdefault(_MORE, []).append(_raw(line))
        return True
    seen.add(name)
    if name not in _SHADOWED and name != "PHOTO":
        if _NAME_RE.fullmatch(name):
            extra.setdefault(name, []).append(_raw(line))
        return True
    return False


def _parse_card(lines):
    """Convert the content lines of one VCARD into Contact kwargs."""
    fields = {
        "full_name": "",
        "civility": "",
        "name_prefix": "",
        "given_name": "",
        "middle_name": "",
        "family_name": "",
        "name_suffix": "",
        "nickname": "",
        "emails": [],
        "phones": [],
        "addresses": [],
        "urls": [],
        "company": "",
        "department": "",
        "job_title": "",
        "notes": "",
        "photo": "",
        "label_names": [],
        "vcard_extra": {},
    }
    extra = fields["vcard_extra"]
    seen = set()
    full_name = ""

    for line in lines:
        name = line.name.upper()
        if _set_aside(name, line, fields, seen):
            continue

        value = _decode(name, line)
        if name in _TYPED:
            used = bool(value["value"])
            if used:
                fields[_TYPED[name]].append(value)
        elif name in ("N", "ORG"):
            used = name == "N" or any(value.values())
            fields.update(value)
        elif name == "FN":
            used = bool(value)
            full_name = value
        else:
            used = bool(value)
            fields[_TEXT.get(name, "photo")] = value
        # Only a line serialization will write again can be a shadow.
        if used and name in _SHADOWED and _raw(line) != _encode(name, value):
            extra.setdefault(name, []).append(_raw(line))

    # An empty FN reads as what serialization would write in its place,
    # so a second round trip gives the same full_name.
    fields["full_name"] = full_name or _default_full_name(
        [fields[part] for part in NAME_PARTS], fields["emails"]
    )
    return fields


# --- serialization ----------------------------------------------------


def formatted_name(contact):
    """The FN written for *contact* (merged for overlays)."""
    merged = contact.merged_value
    return merged("full_name") or _default_full_name(
        [merged(part) or "" for part in NAME_PARTS], merged("emails")
    )


def structured_name(contact):
    """The N columns of *contact* (merged for overlays)."""
    merged = contact.merged_value
    return {
        field: merged(field) or "" for field in ("civility", "name_prefix", *NAME_PARTS)
    }


def structured_name_text(contact):
    """The N value written for *contact*, unescaped, components ";"-joined."""
    encoded = _encode("N", structured_name(contact)).removeprefix("N:")
    return ";".join(_unescape(part) for part in _split(encoded, ";"))


def serialize_contact(contact):
    """Serialize one Contact row to a vCard 3.0 string.

    Every field is read through ``Contact.merged_value`` so a personal
    overlay exports the card the user actually sees: its own overridden
    fields plus everything else inherited live from the read-only parent.
    """
    merged = contact.merged_value
    extra = merged("vcard_extra") or {}
    shadows = {
        name: [
            line
            for line in (_line_from_text(name, text) for text in extra.get(name, []))
            if line is not None
        ]
        for name in _SHADOWED
    }
    lines = ["VERSION:3.0"]

    def emit(name, item):
        for line in shadows[name]:
            if _decode(name, line) == item:
                shadows[name].remove(line)
                lines.append(_raw(line))
                return
        lines.append(_encode(name, item))

    emit("FN", formatted_name(contact))
    emit("N", structured_name(contact))
    if nickname := merged("nickname"):
        emit("NICKNAME", nickname)
    for name, field in _TYPED.items():
        for entry in merged(field) or []:
            if isinstance(entry, dict) and entry.get("value"):
                item = {"type": entry.get("type") or "", "value": entry["value"]}
                emit(name, item)
    company = merged("company") or ""
    department = merged("department") or ""
    if company or department:
        emit("ORG", {"company": company, "department": department})
    if job_title := merged("job_title"):
        emit("TITLE", job_title)
    if notes := merged("notes"):
        emit("NOTE", notes)
    if label_names := contact_label_names(contact):
        lines.append("CATEGORIES:" + ",".join(_escape(name) for name in label_names))
    if photo := _encode_photo(merged("photo")):
        lines.append(photo)
    lines += _repeated_lines(extra)

    if "UID" not in extra:
        lines.append(f"UID:{contact.pk}")
    # An owned row's CATEGORIES are its labels, written above.
    skipped = {"CATEGORIES"} if contact.owner_id else set()
    lines += _unmodeled_lines(extra, skipped)

    body = "".join(_fold(line) for line in lines)
    return f"BEGIN:VCARD\r\n{body}END:VCARD\r\n"


def contact_label_names(contact):
    """The names of the labels its owner put on *contact*, sorted.

    Reads ``contact.labels.all()``, so a prefetch of the labels is used.
    """
    if contact.owner_id is None:
        return []
    return sorted(
        label.name
        for label in contact.labels.all()
        if label.owner_id == contact.owner_id
    )


def _unmodeled_lines(extra, skipped=frozenset()):
    """The stored lines of properties without a column, safe to write."""
    for name, texts in extra.items():
        if (
            name in _SHADOWED
            or name in _SKIPPED
            or name in skipped
            or not _NAME_RE.fullmatch(name)
        ):
            continue
        for text in texts:
            if "\r" in text or "\n" in text:
                continue
            if re.match(rf"(?:[A-Za-z0-9-]+\.)?{name}[;:]", text, re.IGNORECASE):
                yield text


def _repeated_lines(extra):
    """The stored later instances of _SINGLE properties, safe to write."""
    for text in extra.get(_MORE, []):
        if "\r" in text or "\n" in text:
            continue
        match = re.match(r"(?:[A-Za-z0-9-]+\.)?([A-Za-z0-9-]+)[;:]", text)
        if match and match.group(1).upper() in _REPEATABLE:
            yield text


def serialize_contacts(contacts):
    """Serialize an iterable of Contact rows to a vCard 3.0 string."""
    return "".join(serialize_contact(contact) for contact in contacts)
