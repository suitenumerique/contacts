"""Tests for the create_demo management command."""

# pylint: disable=missing-function-docstring

from django.core.management import call_command

import pytest

from core import models

pytestmark = pytest.mark.django_db


def test_create_demo_is_idempotent():
    call_command("create_demo")
    counts = (models.Contact.objects.count(), models.ContactGroup.objects.count())

    call_command("create_demo")

    assert (models.Contact.objects.count(), models.ContactGroup.objects.count()) == (
        counts
    )


def test_create_demo_search_fields_match_the_cards():
    """Rows written with QuerySet.update() would miss fields in search."""
    call_command("create_demo")

    for contact in models.Contact.objects.select_related("overlay_of"):
        assert (contact.search_text, contact.sort_key) == (
            contact.compute_search_fields()
        ), contact.display_name


def test_create_demo_users_have_the_default_labels():
    call_command("create_demo")

    default_names = {name for name, _color in models.DEFAULT_LABELS}
    for user in models.User.objects.all():
        names = set(user.contact_labels.values_list("name", flat=True))
        assert default_names <= names, user.email
