"""Rewrite ``search_text`` in its word-start format, for contacts and groups.

The new format (see ``core.utils.build_search_text``) is a pure function of
the old one for contacts, so no card is re-read: each word gets a leading
space, and words joined by punctuation are also listed apart. Groups get
theirs from name, email and description. The helper is copied here so a
later change to it can't alter what this migration did.
"""

import re
import unicodedata

from django.db import migrations

_WORD_SEPARATORS = re.compile(r"[\W_]+")
BATCH_SIZE = 1000


def _build_search_text(*parts):
    text = unicodedata.normalize("NFKD", " ".join(p for p in parts if p).lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    words = text.split()
    pieces = [
        piece
        for word in words
        for piece in _WORD_SEPARATORS.split(word)
        if piece and piece != word
    ]
    unique = dict.fromkeys([*words, *pieces])
    return f" {' '.join(unique)}" if unique else ""


def _rewrite(model, compute):
    batch = []
    for row in model.objects.only("pk", *compute.fields).iterator(
        chunk_size=BATCH_SIZE
    ):
        row.search_text = compute(row)
        batch.append(row)
        if len(batch) >= BATCH_SIZE:
            model.objects.bulk_update(batch, ["search_text"])
            batch = []
    if batch:
        model.objects.bulk_update(batch, ["search_text"])


def _contact_text(row):
    return _build_search_text(row.search_text)


_contact_text.fields = ("search_text",)


def _group_text(row):
    return _build_search_text(row.name, row.email, row.description)


_group_text.fields = ("name", "email", "description", "search_text")


def forwards(apps, schema_editor):  # pylint: disable=unused-argument
    """Backfill both tables."""
    _rewrite(apps.get_model("core", "Contact"), _contact_text)
    _rewrite(apps.get_model("core", "ContactGroup"), _group_text)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_search_and_interactions"),
    ]

    operations = [
        # The old contact format is still matched by the web search
        # (``contains`` per word), so going back needs no rewrite.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
