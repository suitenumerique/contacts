"""Small shared helpers for the core app."""

import unicodedata


def strip_diacritics(text):
    """Return *text* with combining accents removed ("Édouard" -> "Edouard").

    ``Contact.search_text`` is stored folded this way, and queries are
    folded the same way before a ``contains`` lookup on it.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))
