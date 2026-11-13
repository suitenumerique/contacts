"""Bidi control characters are removed from the names that the UI quotes."""

# pylint: disable=missing-function-docstring,redefined-outer-name

import pytest
from rest_framework.test import APIClient

from core import factories, models
from core.tasks import import_contacts
from core.utils import strip_bidi_controls

pytestmark = pytest.mark.django_db

CONTACTS_URL = "/api/v1.0/contacts/"

VCF = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\n"
    "FN:Zz\u202egnol\r\nN:\u2066gnol\u2069;Zz;;;\r\n"
    "EMAIL:zz@example.org\r\nCATEGORIES:Amis\u202e\r\n"
    "END:VCARD\r\n"
)


@pytest.fixture
def user():
    return factories.UserFactory()


def test_strip_removes_embeddings_overrides_and_isolates():
    controls = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
    assert strip_bidi_controls(f"a{controls}b") == "ab"


def test_strip_keeps_marks_joiners_and_rtl_letters():
    name = "\u200fדוד\u200e Jr. \u200c\u200d"
    assert strip_bidi_controls(name) == name


def test_api_write_stores_the_name_without_controls(user):
    client = APIClient()
    client.force_authenticate(user=user)
    contact = factories.ContactFactory(owner=user, full_name="Zz")

    response = client.patch(
        f"{CONTACTS_URL}{contact.id}/",
        {"full_name": "Zz\u202egnol", "company": "\u2067Acme\u2069"},
        format="json",
    )

    assert response.status_code == 200, response.content
    contact.refresh_from_db()
    assert contact.full_name == "Zzgnol"
    assert contact.company == "Acme"
    assert contact.search_text.startswith(" zzgnol acme")


def test_vcf_import_stores_names_and_labels_without_controls(user):
    models.ContactLabel.objects.get_or_create(owner=user, name="Amis")

    result = import_contacts(user, "vcf", VCF.encode())

    assert result["imported"] == 1
    contact = models.Contact.objects.get(owner=user)
    assert contact.full_name == "Zzgnol"
    assert contact.family_name == "gnol"
    assert [label.name for label in contact.labels.all()] == ["Amis"]


def test_group_and_label_names_lose_their_controls(user):
    group = factories.ContactGroupFactory(owner=user, name="Team\u202e", description="")
    label = models.ContactLabel.objects.create(owner=user, name="\u202dVIP")

    assert group.name == "Team"
    assert label.name == "VIP"
