"""CSV import/export for contacts.

Reference CSV model (header required, comma or semicolon separated,
UTF-8 with optional BOM)::

    civility,name_prefix,first_name,middle_name,last_name,name_suffix,
    full_name,nickname,organization,department,job_title,
    email_1,email_1_type,phone_1,phone_1_type,address_1,address_1_type,
    website_1,website_1_type,notes,labels

- ``civility``: ``M.`` / ``Mme`` (or ``mr`` / ``mrs``, ``Mlle`` read as
  ``Mme``); anything else is treated as unspecified.
- ``full_name``: the name as displayed; when empty, the name parts
  joined. A row needs a name in one of these columns.
- ``email_N``, ``phone_N``, ``address_N``, ``website_N``: as many as the
  export needs, each with an optional ``_type`` column (``work``,
  ``home``, ``cell``, ``other``..., French words accepted). Without one,
  emails, phones and addresses are ``work``.
- ``labels``: label names separated by ``;`` inside the cell (or ``,``
  when the file delimiter is ``;``).

Unknown columns are ignored. Common alternative headers are mapped:
French ones (``prenom``, ``nom``, ``courriel``, ``telephone``,
``organisme``, ``service``, ``poste``...), plain ``email`` / ``phone`` /
``phone2`` columns, and Google Contacts and Outlook exports. The import
decodes a file as UTF-8, then as Windows-1252, then as Latin-1.
"""

import csv
import io
import logging
import re
import unicodedata

from core.models import NAME_PARTS, compose_name
from core.utils import decode_text

logger = logging.getLogger(__name__)

# Characters that make spreadsheet apps read a cell as a formula (CSV
# injection). Phone numbers such as "+33 6 39 98 56 78" are left alone.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_PHONE_LIKE = re.compile(r"^\+?[\d\s().-]+$")

# CSV kind of a typed column -> Contact field.
TYPED_KINDS = {
    "email": "emails",
    "phone": "phones",
    "address": "addresses",
    "website": "urls",
}
_DEFAULT_TYPES = {"email": "work", "phone": "work", "address": "work", "website": ""}

_LEADING_COLUMNS = [
    "civility",
    "name_prefix",
    "first_name",
    "middle_name",
    "last_name",
    "name_suffix",
    "full_name",
    "nickname",
    "organization",
    "department",
    "job_title",
]
_TRAILING_COLUMNS = ["notes", "labels"]


def csv_columns(counts=None):
    """The export header, with ``counts[kind]`` entries per typed kind."""
    columns = list(_LEADING_COLUMNS)
    for kind in TYPED_KINDS:
        for index in range(1, (counts or {}).get(kind, 1) + 1):
            columns += [f"{kind}_{index}", f"{kind}_{index}_type"]
    return columns + _TRAILING_COLUMNS


# Accepted header aliases (normalized by _normalize_header) -> column.
HEADER_ALIASES = {
    "civility": "civility",
    "civilite": "civility",
    "name_prefix": "name_prefix",
    "prefix": "name_prefix",
    "prefixe": "name_prefix",
    "first_name": "first_name",
    "firstname": "first_name",
    "prenom": "first_name",
    "given_name": "first_name",
    "middle_name": "middle_name",
    "additional_name": "middle_name",
    "autres_prenoms": "middle_name",
    "last_name": "last_name",
    "lastname": "last_name",
    "nom": "last_name",
    "family_name": "last_name",
    "name_suffix": "name_suffix",
    "suffix": "name_suffix",
    "suffixe": "name_suffix",
    "full_name": "full_name",
    "display_name": "full_name",
    "name": "full_name",
    "nom_complet": "full_name",
    "nickname": "nickname",
    "surnom": "nickname",
    "organization": "organization",
    "organisation": "organization",
    "organisme": "organization",
    "company": "organization",
    "collectivite": "organization",
    "organization_name": "organization",
    "organization_1_-_name": "organization",
    "department": "department",
    "direction": "department",
    "service": "department",
    "organization_department": "department",
    "organization_1_-_department": "department",
    "job_title": "job_title",
    "poste": "job_title",
    "title": "job_title",
    "fonction": "job_title",
    "organization_title": "job_title",
    "organization_1_-_title": "job_title",
    "notes": "notes",
    "note": "notes",
    "commentaire": "notes",
    "commentaires": "notes",
    "remarques": "notes",
    "labels": "labels",
    "libelles": "labels",
    "tags": "labels",
    "statut": "labels",
    "status": "labels",
    "categories": "labels",
    "group_membership": "labels",
}

# Typed columns whose header fixes the type: kind, type, and the slot
# number when the header names one.
_FIXED_TYPED_HEADERS = {
    # Plain columns.
    "phone2": ("phone", "cell", 2),
    "telephone2": ("phone", "cell", 2),
    "tel2": ("phone", "cell", 2),
    "mobile": ("phone", "cell", None),
    "portable": ("phone", "cell", None),
    "telephone_mobile": ("phone", "cell", None),
    # Outlook.
    "business_phone": ("phone", "work", None),
    "business_phone_2": ("phone", "work", None),
    "company_main_phone": ("phone", "work", None),
    "home_phone": ("phone", "home", None),
    "home_phone_2": ("phone", "home", None),
    "mobile_phone": ("phone", "cell", None),
    "other_phone": ("phone", "other", None),
    "primary_phone": ("phone", "other", None),
    "business_fax": ("phone", "fax", None),
    "home_fax": ("phone", "fax", None),
    "other_fax": ("phone", "fax", None),
    "web_page": ("website", "", None),
    "personal_web_page": ("website", "home", None),
}

_KIND_ALIASES = {
    "email": "email",
    "e-mail": "email",
    "mail": "email",
    "courriel": "email",
    "adresse_mail": "email",
    "adresse_email": "email",
    "adresse_e-mail": "email",
    "adresse_electronique": "email",
    "phone": "phone",
    "telephone": "phone",
    "tel": "phone",
    "address": "address",
    "adresse": "address",
    "adresse_postale": "address",
    "website": "website",
    "site_web": "website",
    "url": "website",
}

# Parts of a structured address, as Google and Outlook split them, in the
# order they are written back as text.
_ADDRESS_PARTS = {
    "po_box": "po_box",
    "extended_address": "extended",
    "street": "street",
    "street_2": "street_2",
    "street_3": "street_3",
    "postal_code": "postal_code",
    "city": "city",
    "region": "region",
    "state": "region",
    "country": "country",
    "country/region": "country",
}

# ours: email_1, email_1_type, phone2; Google: e-mail_1_-_value,
# e-mail_1_-_label, address_1_-_city; Outlook: e-mail_2_address.
_TYPED_HEADER_RE = re.compile(
    r"^(?P<kind>[a-z_-]+?)_?(?P<index>\d+)?(?:_-)?"
    r"(?:_(?P<part>value|type|label|formatted|address|"
    + "|".join(re.escape(part) for part in _ADDRESS_PARTS)
    + r"))?$"
)
# Outlook: business_street, home_postal_code, other_country/region...
_OUTLOOK_ADDRESS_RE = re.compile(
    r"^(?P<prefix>business|home|other)_(?P<part>"
    + "|".join(re.escape(part) for part in _ADDRESS_PARTS)
    + r")$"
)
_OUTLOOK_ADDRESS_TYPES = {"business": "work", "home": "home", "other": "other"}

_TYPE_ALIASES = {
    "work": "work",
    "travail": "work",
    "pro": "work",
    "professionnel": "work",
    "professionnelle": "work",
    "business": "work",
    "bureau": "work",
    "home": "home",
    "domicile": "home",
    "perso": "home",
    "personnel": "home",
    "personnelle": "home",
    "personal": "home",
    "cell": "cell",
    "mobile": "cell",
    "portable": "cell",
    "other": "other",
    "autre": "other",
    "fax": "fax",
    "home_fax": "fax",
    "work_fax": "fax",
}
_TYPE_RE = re.compile(r"^[a-z0-9-]{1,32}$")

CIVILITY_ALIASES = {
    "m.": "mr",
    "m": "mr",
    "mr": "mr",
    "monsieur": "mr",
    "mme": "mrs",
    "mme.": "mrs",
    "mrs": "mrs",
    "madame": "mrs",
    "ms": "mrs",
    "ms.": "mrs",
    # Mademoiselle is no longer used by administrations (circulaire du
    # 21 février 2012): read as Madame.
    "mlle": "mrs",
    "mlle.": "mrs",
    "melle": "mrs",
    "mademoiselle": "mrs",
    "miss": "mrs",
}


def _normalize_header(value):
    """Lowercase a header cell and strip accents/spaces."""
    value = (value or "").strip().lower().replace(" ", "_")
    value = unicodedata.normalize("NFD", value)
    return "".join(c for c in value if not unicodedata.combining(c))


def _typed_column(header, position):
    """``(kind, slot, part, fixed_type)`` for a typed column, or None.

    *slot* orders and groups the columns of one entry: numbered columns
    share ``(0, number)``, others each get their own, after them.
    """
    if header in _FIXED_TYPED_HEADERS:
        kind, fixed_type, index = _FIXED_TYPED_HEADERS[header]
        slot = (0, index) if index else (1, position)
        return kind, slot, "value", fixed_type
    if match := _OUTLOOK_ADDRESS_RE.match(header):
        prefix = match.group("prefix")
        part = _ADDRESS_PARTS[match.group("part")]
        return "address", (2, prefix), part, _OUTLOOK_ADDRESS_TYPES[prefix]
    match = _TYPED_HEADER_RE.match(header)
    if not match or match.group("kind") not in _KIND_ALIASES:
        return None
    kind = _KIND_ALIASES[match.group("kind")]
    part = match.group("part") or "value"
    if part in ("label", "type"):
        part = "type"
    elif part in _ADDRESS_PARTS:
        if kind != "address":
            return None
        part = _ADDRESS_PARTS[part]
    else:
        part = "value"
    index = int(match.group("index") or 1)
    return kind, (0, index), part, None


def _resolve_header(cells):
    """Map each header cell to what it holds (see _read_row), or None."""
    normalized = [_normalize_header(cell) for cell in cells]
    columns = []
    for position, header in enumerate(normalized):
        if header in HEADER_ALIASES:
            columns.append(("field", HEADER_ALIASES[header]))
        elif typed := _typed_column(header, position):
            columns.append(("typed", *typed))
        else:
            columns.append(None)
    # Outlook's "Title" is the name prefix (Mr., Dr.) next to "Job Title".
    if "job_title" in normalized:
        columns = [
            ("field", "name_prefix") if header == "title" else column
            for header, column in zip(normalized, columns, strict=True)
        ]
    return columns


def parse_csv(data):
    """Parse CSV bytes/str into Contact field dicts.

    Returns ``(cards, errors)``; each card holds the same Contact fields and
    ``label_names`` as a ``vcf_service.parse_vcf`` card, without ``photo``
    or ``vcard_extra``.
    """
    data = decode_text(data)

    try:
        dialect = csv.Sniffer().sniff(data[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    rows, errors = _read_rows(data, dialect)
    if not rows:
        return [], errors

    columns = _resolve_header(rows[0])
    if not any(columns):
        # No recognizable header: refuse rather than mis-import.
        return [], errors + len(rows)

    label_separator = "," if dialect.delimiter == ";" else ";"
    cards = []
    failures = 0
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        try:
            cards.append(_to_fields(*_read_row(columns, row), label_separator))
        except Exception:  # pylint: disable=broad-exception-caught
            # One traceback is sufficient. A file can contain many bad rows.
            if not failures:
                logger.exception("Failed to parse a CSV row")
            failures += 1
    if failures > 1:
        logger.error("Failed to parse %d CSV rows", failures)
    return cards, errors + failures


def _read_rows(data, dialect):
    """Read every row, skipping the ones the csv module rejects.

    Returns ``(rows, errors)``. A malformed row (e.g. a field over the csv
    module's size limit) counts as one error instead of aborting the file.
    """
    reader = csv.reader(io.StringIO(data), dialect)
    rows = []
    errors = 0
    first_error = None
    while True:
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error as error:
            first_error = first_error or error
            errors += 1
            continue
        rows.append(row)
    if errors:
        # Write one record for the file. A small binary file can contain
        # thousands of unreadable rows.
        logger.warning(
            "Skipped %d unreadable CSV rows, the first one: %s", errors, first_error
        )
    return rows, errors


def _read_row(columns, row):
    """Split one row into ``(values, entries)``.

    *values* maps plain columns to their cell; *entries* maps each typed
    slot to ``{"kind", "type", "value", <address parts>}``.
    """
    values, entries = {}, {}
    for column, raw in zip(columns, row, strict=False):
        cell = _unescape_cell(raw.strip())
        if column is None or not cell:
            continue
        if column[0] == "field":
            values.setdefault(column[1], cell)
            continue
        kind, slot, part, fixed_type = column[1:]
        entry = entries.setdefault((kind, slot), {"kind": kind})
        if fixed_type is not None:
            entry.setdefault("type", fixed_type)
        if part == "type":
            entry["type"] = _normalize_type(cell, kind)
        else:
            entry.setdefault(part, cell)
    return values, entries


def _normalize_type(cell, kind):
    """A type cell as a stored type: known words mapped, else kept if safe."""
    value = cell.strip().lstrip("*").strip().lower().replace(" ", "_")
    value = _TYPE_ALIASES.get(value, value)
    return value if _TYPE_RE.match(value) else _DEFAULT_TYPES[kind]


def _address_text(entry):
    """An address entry as text: its formatted value, else its parts."""
    if entry.get("value"):
        return entry["value"]
    city = " ".join(
        part for part in (entry.get("postal_code"), entry.get("city")) if part
    )
    lines = (
        entry.get("po_box"),
        entry.get("extended"),
        entry.get("street"),
        entry.get("street_2"),
        entry.get("street_3"),
        city,
        entry.get("region"),
        entry.get("country"),
    )
    return "\n".join(line for line in lines if line)


def _typed_lists(entries):
    """The emails/phones/addresses/urls lists of one row, in slot order."""
    lists = {field: [] for field in TYPED_KINDS.values()}
    for (kind, _slot), entry in sorted(entries.items(), key=lambda item: item[0][1]):
        value = _address_text(entry) if kind == "address" else entry.get("value")
        if value:
            lists[TYPED_KINDS[kind]].append(
                {"type": entry.get("type", _DEFAULT_TYPES[kind]), "value": value}
            )
    return lists


def _label_names(cell, separator):
    """Label names of a cell, Google's " ::: " lists too.

    Google marks its system groups with "* " ("* myContacts ::: * starred");
    the mark is dropped, the import decides what they stand for (see
    ``core.tasks.GOOGLE_SYSTEM_LABELS``).
    """
    parts = cell.split(":::") if ":::" in cell else cell.split(separator)
    names = (part.strip().removeprefix("*").strip() for part in parts)
    return [name for name in names if name]


def _to_fields(values, entries, label_separator):
    """Convert one row into Contact constructor kwargs."""
    names = {
        "given_name": values.get("first_name", ""),
        "middle_name": values.get("middle_name", ""),
        "family_name": values.get("last_name", ""),
        "name_suffix": values.get("name_suffix", ""),
    }
    return {
        "civility": CIVILITY_ALIASES.get(
            (values.get("civility") or "").strip().lower(), ""
        ),
        "name_prefix": values.get("name_prefix", ""),
        **names,
        "full_name": values.get("full_name")
        or compose_name(*(names[part] for part in NAME_PARTS)),
        "nickname": values.get("nickname", ""),
        **_typed_lists(entries),
        "company": values.get("organization", ""),
        "department": values.get("department", ""),
        "job_title": values.get("job_title", ""),
        "notes": values.get("notes", ""),
        "label_names": _label_names(values.get("labels", ""), label_separator),
    }


def _safe_cell(value):
    """Neutralize a cell a spreadsheet would evaluate as a formula."""
    value = str(value or "")
    if value.startswith(_FORMULA_PREFIXES) and not _PHONE_LIKE.match(value):
        return f"'{value}"
    return value


def _unescape_cell(value):
    """Undo ``_safe_cell`` so an exported file imports back unchanged."""
    if value.startswith("'") and value[1:].startswith(_FORMULA_PREFIXES):
        return value[1:]
    return value


def _typed_entries(contact):
    """Each typed kind's ``(value, type)`` pairs for *contact* (merged)."""
    return {
        kind: [
            (entry.get("value"), entry.get("type") or "")
            for entry in contact.merged_value(field) or []
            if isinstance(entry, dict) and entry.get("value")
        ]
        for kind, field in TYPED_KINDS.items()
    }


def serialize_contacts_csv(contacts):
    """Serialize Contact rows to the reference CSV format.

    Every email, phone, address and website is written: the header has as
    many numbered columns as the contact with the most of each needs.
    """
    rows = [(contact, _typed_entries(contact)) for contact in contacts]
    counts = {
        kind: max([1, *(len(typed[kind]) for _contact, typed in rows)])
        for kind in TYPED_KINDS
    }
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(csv_columns(counts))
    for contact, typed in rows:
        # Read through merged_value so an overlay exports what the user
        # sees, not just the handful of fields it overrides.
        merged = contact.merged_value
        row = [
            {"mr": "M.", "mrs": "Mme"}.get(merged("civility"), ""),
            merged("name_prefix"),
            merged("given_name"),
            merged("middle_name"),
            merged("family_name"),
            merged("name_suffix"),
            merged("full_name"),
            merged("nickname"),
            merged("company"),
            merged("department"),
            merged("job_title"),
        ]
        for kind in TYPED_KINDS:
            pairs = typed[kind] + [("", "")] * (counts[kind] - len(typed[kind]))
            for value, type_value in pairs:
                row += [value, type_value]
        row += [
            merged("notes"),
            ";".join(label.name for label in contact.labels.all()),
        ]
        writer.writerow([_safe_cell(cell) for cell in row])
    return output.getvalue()
