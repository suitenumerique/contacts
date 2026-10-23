"""The stored search_text format (core.utils.build_search_text)."""

import pytest

from core import factories
from core.utils import build_search_text

pytestmark = pytest.mark.django_db


def test_every_word_starts_with_a_space():
    """Every word starts with a space."""
    assert build_search_text("Félix MARCHAND", "Mairie") == " felix marchand mairie"


def test_words_joined_by_punctuation_are_also_listed_apart():
    """Words joined by punctuation are also listed apart."""
    assert build_search_text("felix.marchand@brigny.example", "Jean-Pierre") == (
        " felix.marchand@brigny.example jean-pierre"
        " felix marchand brigny example jean pierre"
    )


def test_it_is_idempotent():
    """It is idempotent."""
    text = build_search_text("Claire D. (annuaire)", "claire.dubois@brigny.example")

    assert build_search_text(text) == text


def test_nothing_gives_an_empty_text():
    """Nothing gives an empty text."""
    assert build_search_text("", None) == ""


def test_contacts_and_groups_store_it():
    """Contacts and groups store it."""
    contact = factories.ContactFactory(
        full_name="Zoé", emails=[{"type": "", "value": "z.l@x.org"}]
    )
    group = factories.ContactGroupFactory(name="Service technique", email="tech@x.org")

    assert contact.search_text == " zoe z.l@x.org z l x org"
    assert group.search_text == " service technique tech@x.org tech x org"
