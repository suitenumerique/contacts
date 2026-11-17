"""Small shared helpers for the core app."""

import re
import unicodedata

# What separates the pieces of an email address, a hyphenated name or an
# initial: "felix.marchand@brigny.example", "jean-pierre", "o'neil".
_WORD_SEPARATORS = re.compile(r"[\W_]+")


def strip_diacritics(text):
    """Return *text* with combining accents removed ("Édouard" -> "Edouard").

    ``Contact.search_text`` is stored folded this way, and queries are
    folded the same way before a ``contains`` lookup on it.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def fold(text):
    """*text* lowercased and accent-folded, as search compares it."""
    return strip_diacritics((text or "").lower())


def build_search_text(*parts):
    """The ``search_text`` of a row whose searchable values are *parts*.

    Lowercased and accent-folded, so a plain ``contains`` (``LIKE``) covered
    by the trigram index finds it. Every word is preceded by a single space,
    the first one included, so "a word starts with x" is ``contains(" x")``:
    a pattern the trigram index serves from one character on, where
    "x anywhere" needs three. Words joined by punctuation are also listed
    apart ("felix.marchand@brigny.example" adds "felix marchand brigny
    example"), so each piece starts a word.

    Idempotent: feeding a ``search_text`` back in returns it unchanged.
    """
    words = fold(" ".join(p for p in parts if p)).split()
    # Each word split on punctuation. "(annuaire)" splits into "",
    # "annuaire", "": empty pieces are dropped. A word without punctuation
    # splits into itself, already listed in ``words``.
    pieces = [
        piece
        for word in words
        for piece in _WORD_SEPARATORS.split(word)
        if piece and piece != word
    ]
    unique = dict.fromkeys([*words, *pieces])
    return f" {' '.join(unique)}" if unique else ""


def decode_text(data):
    """Return the bytes of a text file as a string.

    The function reads UTF-8, with or without a byte order mark. It reads
    bytes that are not UTF-8 as Windows-1252, then as Latin-1. It returns a
    string unchanged.
    """
    if isinstance(data, str):
        return data
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")
