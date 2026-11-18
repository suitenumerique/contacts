"""Tests for labels (libellés), the corbeille and CSV import/export."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument

import csv
import io
import logging
from datetime import timedelta

from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core import factories, models
from core.authentication.backends import OIDCAuthenticationBackend
from core.services.csv_service import parse_csv
from core.tasks import import_contacts, import_contacts_task

pytestmark = pytest.mark.django_db

CONTACTS_URL = "/api/v1.0/contacts/"
LABELS_URL = "/api/v1.0/labels/"


@pytest.fixture
def user():
    return factories.UserFactory()


@pytest.fixture
def client(user):
    client = APIClient()
    client.force_login(user)
    return client


# --- Labels ----------------------------------------------------------------


DEFAULT_LABEL_NAMES = {"Élu", "Agent", "Prestataire", "Partenaire", "Association"}


def test_new_user_gets_the_default_labels(user):
    OIDCAuthenticationBackend().post_get_or_create_user(user, {}, is_new_user=True)
    assert set(user.contact_labels.values_list("name", flat=True)) == (
        DEFAULT_LABEL_NAMES
    )


def test_returning_user_labels_are_left_alone(user):
    """A deleted default label does not come back at the next login."""
    OIDCAuthenticationBackend().post_get_or_create_user(user, {}, is_new_user=False)
    assert not user.contact_labels.exists()


def test_listing_labels_does_not_seed(client, user):
    assert client.get(LABELS_URL).json() == []


def test_ensure_defaults_keeps_existing_labels(user):
    models.ContactLabel.objects.create(owner=user, name="Agent", color="#000000")
    models.ContactLabel.ensure_defaults(user)
    assert user.contact_labels.count() == len(DEFAULT_LABEL_NAMES)
    assert user.contact_labels.get(name="Agent").color == "#000000"


def test_duplicate_label_name_is_a_400(client, user):
    client.post(LABELS_URL, {"name": "Mairie"}, format="json")
    response = client.post(LABELS_URL, {"name": "Mairie"}, format="json")
    assert response.status_code == 400
    assert user.contact_labels.count() == 1


def test_concurrent_duplicate_label_is_a_400(client, user, monkeypatch):
    """The name check and the insert can race; the constraint then answers."""
    models.ContactLabel.objects.create(owner=user, name="Mairie")
    monkeypatch.setattr(
        "core.api.serializers.ContactLabelSerializer.validate_name",
        lambda self, value: value,
    )
    monkeypatch.setattr(
        models.ContactLabel, "validate_constraints", lambda self, exclude=None: None
    )

    response = client.post(LABELS_URL, {"name": "Mairie"}, format="json")

    assert response.status_code == 400
    assert user.contact_labels.count() == 1


def test_label_crud_and_contact_filter(client, user):
    label = client.post(
        LABELS_URL, {"name": "Mairie", "color": "#112233"}, format="json"
    ).json()
    assert label["color"] == "#112233"

    contact = factories.ContactFactory(owner=user, full_name="Tagged One")
    factories.ContactFactory(owner=user, full_name="Untagged")
    response = client.patch(
        f"{CONTACTS_URL}{contact.id}/", {"label_ids": [label["id"]]}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["label_ids"] == [label["id"]]

    results = client.get(CONTACTS_URL, {"label": label["id"]}).json()["results"]
    assert [c["full_name"] for c in results] == ["Tagged One"]

    renamed = client.patch(
        f"{LABELS_URL}{label['id']}/", {"name": "Mairie centrale"}, format="json"
    )
    assert renamed.status_code == 200

    assert client.delete(f"{LABELS_URL}{label['id']}/").status_code == 204
    contact.refresh_from_db()
    assert contact.labels.count() == 0  # contact survives label deletion


def test_cannot_use_foreign_label(client, user):
    other = factories.UserFactory()
    foreign = models.ContactLabel.objects.create(owner=other, name="Foreign")
    contact = factories.ContactFactory(owner=user)
    response = client.patch(
        f"{CONTACTS_URL}{contact.id}/", {"label_ids": [str(foreign.id)]}, format="json"
    )
    assert response.status_code == 400


# --- Corbeille (trash) -------------------------------------------------------


def test_delete_is_soft_then_hard(client, user):
    contact = factories.ContactFactory(owner=user, full_name="Doomed")

    assert client.delete(f"{CONTACTS_URL}{contact.id}/").status_code == 204
    contact.refresh_from_db()
    assert contact.deleted_at is not None

    # gone from the list (and from search)...
    assert client.get(CONTACTS_URL).json()["count"] == 0
    assert client.get(CONTACTS_URL, {"q": "doomed"}).json()["count"] == 0

    # ...but present in the trash
    trashed = client.get(CONTACTS_URL, {"trash": "true"}).json()["results"]
    assert [c["full_name"] for c in trashed] == ["Doomed"]

    # second delete is permanent
    assert client.delete(f"{CONTACTS_URL}{contact.id}/").status_code == 204
    assert not models.Contact.objects.filter(pk=contact.pk).exists()


def test_restore_from_trash(client, user):
    contact = factories.ContactFactory(owner=user, full_name="Phoenix")
    client.delete(f"{CONTACTS_URL}{contact.id}/")

    response = client.post(f"{CONTACTS_URL}{contact.id}/restore/")
    assert response.status_code == 200
    contact.refresh_from_db()
    assert contact.deleted_at is None
    assert client.get(CONTACTS_URL).json()["count"] == 1


def test_contact_payload_tells_the_trashed_state(client, user):
    contact = factories.ContactFactory(owner=user, full_name="Doomed")
    url = f"{CONTACTS_URL}{contact.id}/"
    assert client.get(url).json()["deleted_at"] is None

    client.delete(url)
    assert client.get(url).json()["deleted_at"] is not None


def test_trashed_contact_is_not_editable(client, user):
    contact = factories.ContactFactory(
        owner=user, full_name="Doomed", deleted_at=timezone.now()
    )
    url = f"{CONTACTS_URL}{contact.id}/"

    response = client.patch(url, {"full_name": "Edited"}, format="json")
    assert response.status_code == 400
    contact.refresh_from_db()
    assert contact.full_name == "Doomed"

    client.post(f"{url}restore/")
    response = client.patch(url, {"full_name": "Edited"}, format="json")
    assert response.status_code == 200


def test_purge_command_respects_retention(user, settings):
    settings.TRASHBIN_CUTOFF_DAYS = 30
    old = factories.ContactFactory(owner=user, full_name="Old")
    recent = factories.ContactFactory(owner=user, full_name="Recent")
    models.Contact.objects.filter(pk=old.pk).update(
        deleted_at=timezone.now() - timedelta(days=31)
    )
    models.Contact.objects.filter(pk=recent.pk).update(
        deleted_at=timezone.now() - timedelta(days=1)
    )

    call_command("purge_trashed_contacts")

    assert not models.Contact.objects.filter(pk=old.pk).exists()
    assert models.Contact.objects.filter(pk=recent.pk).exists()


# --- CSV ----------------------------------------------------------------------

SAMPLE_CSV = """civilité,Prénom,Nom,email,téléphone,organisme,service,poste,statut
Mme,Jeanne,Martin,jeanne.martin@agglo.example,+33639985678,Brigny Agglo,Produits,Cheffe de projet,Agent
M.,Paul,Durand,paul.durand@mairie.example,,Mairie de Brigny,,Secrétaire,Élu;Agent
"""


def test_parse_csv_maps_french_headers():
    cards, errors = parse_csv(SAMPLE_CSV)
    assert errors == 0
    assert len(cards) == 2
    jeanne = cards[0]
    assert jeanne["civility"] == "mrs"
    assert jeanne["given_name"] == "Jeanne"
    assert jeanne["family_name"] == "Martin"
    assert jeanne["emails"] == [
        {"type": "work", "value": "jeanne.martin@agglo.example"}
    ]
    assert jeanne["company"] == "Brigny Agglo"
    assert jeanne["department"] == "Produits"
    assert jeanne["label_names"] == ["Agent"]
    assert cards[1]["label_names"] == ["Élu", "Agent"]


@pytest.mark.parametrize("civility", ["Mlle", "Melle", "Mademoiselle", "Miss", "Ms"])
def test_parse_csv_reads_mademoiselle_and_ms_as_madame(civility):
    (card,), errors = parse_csv(f"civilite,prenom,nom\n{civility},Ada,Lovelace\n")
    assert errors == 0
    assert card["civility"] == "mrs"


def test_csv_import_task_creates_contacts_with_labels(user):
    result = import_contacts(user, "csv", SAMPLE_CSV.encode())
    assert result["imported"] == 2

    paul = models.Contact.objects.get(owner=user, family_name="Durand")
    assert paul.civility == "mr"
    assert sorted(label.name for label in paul.labels.all()) == ["Agent", "Élu"]


def test_csv_import_endpoint(client, user):
    response = client.post(
        f"{CONTACTS_URL}import-csv/",
        {"file": io.BytesIO(SAMPLE_CSV.encode())},
        format="multipart",
    )
    assert response.status_code == 202, response.content
    assert models.Contact.objects.filter(owner=user).count() == 2


EXCEL_CSV = (
    "Prénom;Nom;E-mail;Téléphone\r\nHélène;Lefèvre;helene@example.org;0102030405\r\n"
)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252"])
def test_csv_import_reads_utf_8_and_windows_1252(user, encoding):
    result = import_contacts(user, "csv", EXCEL_CSV.encode(encoding))

    assert (result["imported"], result["errors"]) == (1, 0)
    contact = models.Contact.objects.get(owner=user)
    assert (contact.given_name, contact.family_name) == ("Hélène", "Lefèvre")
    assert contact.emails == [{"type": "work", "value": "helene@example.org"}]
    assert contact.phones == [{"type": "work", "value": "0102030405"}]


def test_parse_csv_reads_latin_1_when_windows_1252_fails():
    # Windows-1252 has no character for the byte 0x81.
    (card,), errors = parse_csv(b"nom\nLef\xe8vre\x81\n")

    assert errors == 0
    assert card["family_name"] == "Lefèvre\x81"


def test_csv_export_round_trip(client, user):
    label = models.ContactLabel.objects.create(owner=user, name="Agent")
    contact = factories.ContactFactory(
        owner=user,
        civility="mrs",
        given_name="Jeanne",
        family_name="Martin",
        full_name="Jeanne Martin",
        emails=[{"type": "work", "value": "jeanne@example.org"}],
        phones=[{"type": "work", "value": "+331020304"}],
        company="Brigny Agglo",
        department="Produits",
    )
    contact.labels.add(label)

    response = client.get(f"{CONTACTS_URL}export-csv/")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")

    cards, errors = parse_csv(response.content)
    assert errors == 0
    assert len(cards) == 1
    assert cards[0]["family_name"] == "Martin"
    assert cards[0]["civility"] == "mrs"
    assert cards[0]["label_names"] == ["Agent"]


def test_csv_export_round_trips_every_name_column(client, user):
    names = {
        "civility": "mrs",
        "name_prefix": "Dr",
        "given_name": "Hélène",
        "middle_name": "Marie",
        "family_name": "de La Tour",
        "name_suffix": "III",
        "full_name": "Hélène de La Tour",
        "nickname": "Léna",
    }
    factories.ContactFactory(owner=user, **names)
    factories.ContactFactory(owner=user, full_name="Médiathèque de Brigny")

    cards, _errors = parse_csv(client.get(f"{CONTACTS_URL}export-csv/").content)

    by_name = {card["full_name"]: card for card in cards}
    helene = by_name["Hélène de La Tour"]
    assert {field: helene[field] for field in names} == names
    assert by_name["Médiathèque de Brigny"]["family_name"] == ""


def test_csv_export_round_trips_every_typed_value_and_notes(client, user):
    typed = {
        "emails": [
            {"type": "work", "value": "ada@example.org"},
            {"type": "home", "value": "ada@perso.example"},
            {"type": "other", "value": "ada@autre.example"},
        ],
        "phones": [
            {"type": "cell", "value": "+33 6 39 98 56 78"},
            {"type": "home", "value": "+33 1 99 00 04 05"},
        ],
        "addresses": [
            {"type": "home", "value": "12 rue X\n75001 Paris\nFrance"},
            {"type": "work", "value": "8 rue de l'Exemple\n99102 Brigny"},
        ],
        "urls": [{"type": "blog", "value": "https://ada.example.org"}],
    }
    factories.ContactFactory(
        owner=user, full_name="Ada", notes="line one\nline two", **typed
    )
    factories.ContactFactory(owner=user, full_name="Bob", emails=[], phones=[])

    body = client.get(f"{CONTACTS_URL}export-csv/").content.decode()
    header = next(csv.reader(io.StringIO(body)))
    assert "email_3" in header and "email_4" not in header
    assert "address_2_type" in header

    cards, errors = parse_csv(body)
    assert errors == 0
    ada = next(card for card in cards if card["full_name"] == "Ada")
    assert {field: ada[field] for field in typed} == typed
    assert ada["notes"] == "line one\nline two"
    bob = next(card for card in cards if card["full_name"] == "Bob")
    assert (bob["emails"], bob["addresses"]) == ([], [])


def test_csv_plain_email_and_phone_columns_import():
    (card,), errors = parse_csv(
        "full_name,email,phone,phone2\nAda,ada@example.org,+33199000405,+33639985678\n"
    )
    assert errors == 0
    assert card["emails"] == [{"type": "work", "value": "ada@example.org"}]
    assert card["phones"] == [
        {"type": "work", "value": "+33199000405"},
        {"type": "cell", "value": "+33639985678"},
    ]


def test_csv_type_columns_accept_french_words():
    (card,), _errors = parse_csv(
        "nom_complet;courriel_1;courriel_1_type;telephone_1;telephone_1_type\n"
        "Ada;ada@perso.example;Domicile;+33639985678;Portable\n"
    )
    assert card["emails"] == [{"type": "home", "value": "ada@perso.example"}]
    assert card["phones"] == [{"type": "cell", "value": "+33639985678"}]


GOOGLE_CSV = (
    "First Name,Last Name,Name Prefix,Nickname,Organization Name,"
    "Organization Title,Notes,Labels,E-mail 1 - Label,E-mail 1 - Value,"
    "E-mail 2 - Label,E-mail 2 - Value,Phone 1 - Label,Phone 1 - Value,"
    "Address 1 - Label,Address 1 - Formatted,Address 1 - City,"
    "Website 1 - Label,Website 1 - Value\n"
    "Ada,Lovelace,Dr,Léna,Brigny Agglo,Cheffe,Met at a conference,"
    "* myContacts ::: Amis ::: * starred ::: Travail,* Work,ada@example.org,"
    "Home,ada@perso.example,"
    'Mobile,+33 6 39 98 56 78,Home,"12 rue X\n75001 Paris",Paris,'
    "Blog,https://ada.example.org\n"
)


def test_csv_google_contacts_export_imports():
    (card,), errors = parse_csv(GOOGLE_CSV)
    assert errors == 0
    assert (card["given_name"], card["family_name"], card["name_prefix"]) == (
        "Ada",
        "Lovelace",
        "Dr",
    )
    assert (card["company"], card["job_title"], card["nickname"]) == (
        "Brigny Agglo",
        "Cheffe",
        "Léna",
    )
    assert card["notes"] == "Met at a conference"
    # Google's system groups lose their "* " mark; the import maps them.
    assert card["label_names"] == ["myContacts", "Amis", "starred", "Travail"]
    assert card["emails"] == [
        {"type": "work", "value": "ada@example.org"},
        {"type": "home", "value": "ada@perso.example"},
    ]
    assert card["phones"] == [{"type": "cell", "value": "+33 6 39 98 56 78"}]
    # The formatted address wins over its split parts.
    assert card["addresses"] == [{"type": "home", "value": "12 rue X\n75001 Paris"}]
    assert card["urls"] == [{"type": "blog", "value": "https://ada.example.org"}]


def test_google_system_groups_are_not_imported_as_labels(user):
    """myContacts is dropped, starred makes a favorite."""
    import_contacts(user, "csv", GOOGLE_CSV.encode())

    contact = models.Contact.objects.get(owner=user)
    assert sorted(label.name for label in contact.labels.all()) == ["Amis", "Travail"]
    assert contact.favorited_by.filter(pk=user.pk).exists()


OUTLOOK_CSV = (
    "Title,First Name,Last Name,Suffix,Company,Department,Job Title,"
    "Business Street,Business City,Business Postal Code,Business Country/Region,"
    "Business Phone,Mobile Phone,E-mail Address,E-mail 2 Address,Web Page,"
    "Categories,Notes\n"
    "Dr,Ada,Lovelace,III,Brigny Agglo,Produits,Cheffe,8 rue de l'Exemple,Brigny,99102,"
    "France,+33 1 99 00 04 05,+33 6 39 98 56 78,ada@example.org,ada@perso.example,"
    "https://ada.example.org,Amis;Travail,Note\n"
)


def test_csv_outlook_export_imports():
    (card,), errors = parse_csv(OUTLOOK_CSV)
    assert errors == 0
    # Outlook's "Title" is the name prefix, next to "Job Title".
    assert (card["name_prefix"], card["job_title"]) == ("Dr", "Cheffe")
    assert card["name_suffix"] == "III"
    assert [email["value"] for email in card["emails"]] == [
        "ada@example.org",
        "ada@perso.example",
    ]
    assert card["phones"] == [
        {"type": "work", "value": "+33 1 99 00 04 05"},
        {"type": "cell", "value": "+33 6 39 98 56 78"},
    ]
    assert card["addresses"] == [
        {"type": "work", "value": "8 rue de l'Exemple\n99102 Brigny\nFrance"}
    ]
    assert card["urls"] == [{"type": "", "value": "https://ada.example.org"}]
    assert card["label_names"] == ["Amis", "Travail"]
    assert card["notes"] == "Note"


def test_csv_name_column_is_the_full_name_unsplit():
    cards, _errors = parse_csv("name,email\nJean de La Fontaine,jean@example.org\n")
    assert (
        cards[0]["full_name"],
        cards[0]["given_name"],
        cards[0]["family_name"],
    ) == ("Jean de La Fontaine", "", "")


# --- CSV injection and robustness ---------------------------------------------


def test_csv_export_neutralizes_formulas(client, user):
    factories.ContactFactory(
        owner=user,
        given_name='=HYPERLINK("http://evil")',
        family_name="@SUM(A1)",
        emails=[{"value": "a@example.org"}],
        phones=[{"value": "+33 6 39 98 56 78"}, {"value": "-1+1"}],
        company="\tTabbed",
    )

    body = client.get(f"{CONTACTS_URL}export-csv/").content.decode()
    header, row = csv.reader(io.StringIO(body))

    cells = dict(zip(header, row, strict=True))
    assert cells["first_name"] == '\'=HYPERLINK("http://evil")'
    assert cells["last_name"] == "'@SUM(A1)"
    assert cells["phone_1"] == "+33 6 39 98 56 78"  # a phone number is left alone
    assert cells["phone_2"] == "'-1+1"
    assert cells["organization"] == "'\tTabbed"


def test_csv_export_of_formulas_imports_back_unchanged(client, user):
    factories.ContactFactory(
        owner=user, given_name="=1+1", family_name="Martin", full_name="=1+1 Martin"
    )
    cards, _ = parse_csv(client.get(f"{CONTACTS_URL}export-csv/").content)
    assert cards[0]["given_name"] == "=1+1"


def test_csv_oversized_field_is_one_error_not_a_failed_import():
    huge = "x" * (csv.field_size_limit() + 10)
    data = (
        "prenom,nom,email\n"
        "Jeanne,Martin,jeanne@example.org\n"
        f'Bad,"{huge}",bad@example.org\n'
        "Paul,Durand,paul@example.org\n"
    )
    cards, errors = parse_csv(data)
    assert errors >= 1
    assert {"Jeanne", "Paul"} <= {card["given_name"] for card in cards}


@pytest.fixture
def csv_log(caplog, monkeypatch):
    """Capture the log records of the CSV parser.

    The "core" logger does not propagate by default. The fixture enables
    the propagation, because caplog reads the root logger.
    """
    monkeypatch.setattr(logging.getLogger("core"), "propagate", True)
    caplog.set_level("WARNING", logger="core.services.csv_service")
    return caplog


def test_csv_unreadable_rows_give_one_log_record(csv_log):
    cards, errors = parse_csv(b"\xff\r\xff\n" * 1000)

    assert not cards
    assert errors == 1000
    assert len(csv_log.records) == 1


def test_csv_failed_rows_give_one_traceback(csv_log, monkeypatch):
    def fail(*args):
        raise ValueError("bad row")

    monkeypatch.setattr("core.services.csv_service._to_fields", fail)
    cards, errors = parse_csv("prenom,nom\n" + "Ada,Lovelace\n" * 50)

    assert not cards
    assert errors == 50
    assert [bool(record.exc_info) for record in csv_log.records] == [True, False]


# --- import rules ---------------------------------------------------------------


def test_import_applies_the_api_rules(user):
    data = (
        "prenom,nom,email,telephone\n"
        ",,nobody@example.org,\n"  # no name
        "Paul,Durand,,\n"  # no email nor phone
        "Anne,Bad,not-an-email,\n"  # invalid email
        "Luc,Phone,,+33199000405\n"  # phone only: fine
        "Jeanne,,jeanne@example.org,\n"  # a first name alone is a name
    )
    result = import_contacts(user, "csv", data.encode())

    assert result["imported"] == 2
    assert result["errors"] == 3
    reasons = [detail["reason"] for detail in result["error_details"]]
    assert reasons == ["no_name", "no_email_or_phone", "invalid_email"]
    assert set(
        models.Contact.objects.filter(owner=user).values_list("full_name", flat=True)
    ) == {"Luc Phone", "Jeanne"}


def test_csv_import_rejects_a_card_with_a_phone_that_is_too_long(user):
    """The import does not remove a value. It rejects the card and says why."""
    long_phone = "1" * 65
    data = (
        "prenom,nom,email,telephone,phone2\n"
        f"Jean,Dupont,jean@example.org,{long_phone},+33199000405\n"
    )

    result = import_contacts(user, "csv", data.encode())

    assert (result["imported"], result["errors"]) == (0, 1)
    assert result["error_details"] == [
        {
            "entry": 1,
            "name": "Jean Dupont",
            "reason": "invalid_phone",
            "value": long_phone,
        }
    ]
    assert not models.Contact.objects.filter(owner=user).exists()


@pytest.mark.parametrize(
    ("cell", "reason"),
    [
        ("javascript:alert(1)", "invalid_url"),
        ("ftp://example.org/x", "invalid_url"),
        ("https://example.org/" + "x" * 1024, "value_too_long"),
    ],
    ids=lambda value: value[:20],
)
def test_csv_import_rejects_a_card_with_a_url_that_the_api_refuses(user, cell, reason):
    data = f"nom,email,website_1\nDupont,jean@example.org,{cell}\n"

    result = import_contacts(user, "csv", data.encode())

    assert (result["imported"], result["errors"]) == (0, 1)
    assert result["error_details"][0]["reason"] == reason
    assert not models.Contact.objects.filter(owner=user).exists()


def test_csv_import_rejects_a_card_with_too_many_values(user):
    header = ",".join(f"phone_{index}" for index in range(1, 52))
    phones = ",".join(f"+3319900{index:04}" for index in range(51))

    result = import_contacts(user, "csv", f"nom,{header}\nDupont,{phones}\n".encode())

    assert (result["imported"], result["errors"]) == (0, 1)
    assert result["error_details"][0]["reason"] == "too_many_values"
    assert not models.Contact.objects.filter(owner=user).exists()


def test_import_restores_a_trashed_duplicate(user):
    trashed = factories.ContactFactory(
        owner=user,
        given_name="Jeanne",
        family_name="Martin",
        emails=[{"value": "Jeanne@Example.org"}],
        deleted_at=timezone.now(),
    )
    data = "prenom,nom,email\nJeanne,Martin,jeanne@example.org\n"

    result = import_contacts(user, "csv", data.encode())

    assert result["imported"] == 1
    assert result["skipped"] == 0
    trashed.refresh_from_db()
    assert trashed.deleted_at is None
    assert models.Contact.objects.filter(owner=user).count() == 1


def test_import_recognizes_an_api_created_contact_without_email(client, user):
    """Dedup keys are the same for a card created in the app and one imported."""
    response = client.post(
        CONTACTS_URL,
        {
            "given_name": "Luc",
            "family_name": "Phone",
            "phones": [{"value": "+33 1 99 00 04 05"}],
        },
        format="json",
    )
    assert response.status_code == 201
    data = "prenom,nom,telephone\nLuc,Phone,+33199000405\n"

    result = import_contacts(user, "csv", data.encode())

    assert result["skipped"] == 1
    assert models.Contact.objects.filter(owner=user).count() == 1


def test_import_tells_apart_email_less_cards_named_by_full_name_only(user):
    """Two services sharing a switchboard number are two contacts."""
    data = (
        "name,telephone\n"
        "Médiathèque de Brigny,+33261914545\n"
        "Piscine de Brigny,+33261914545\n"
    )
    first = import_contacts(user, "csv", data.encode())
    again = import_contacts(user, "csv", data.encode())

    assert (first["imported"], again["skipped"]) == (2, 2)
    assert models.Contact.objects.filter(owner=user).count() == 2


def test_import_drops_an_unusable_photo_but_keeps_the_card(user, monkeypatch):
    def parse(_data):
        return (
            [
                {
                    "civility": "",
                    "given_name": "Jeanne",
                    "family_name": "Martin",
                    "full_name": "Jeanne Martin",
                    "emails": [{"type": "work", "value": "jeanne@example.org"}],
                    "phones": [],
                    "photo": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
                }
            ],
            [],
            0,
        )

    monkeypatch.setattr("core.services.vcf_service.parse_vcf_file", parse)
    result = import_contacts(user, "vcf", b"")

    assert result["imported"] == 1
    assert models.Contact.objects.get(owner=user).photo == ""


# --- import task -------------------------------------------------------------------


def test_import_task_reports_a_parser_crash_as_failure(client, user, monkeypatch):
    def explode(_data):
        raise ValueError("boom")

    monkeypatch.setattr("core.services.csv_service.parse_csv", explode)
    response = client.post(
        f"{CONTACTS_URL}import-csv/",
        {"file": io.BytesIO(SAMPLE_CSV.encode())},
        format="multipart",
    )
    assert response.status_code == 202
    status = client.get(f"/api/v1.0/tasks/{response.json()['task_id']}/").json()

    assert status["status"] == "FAILURE"
    assert status["result"]["imported"] == 0
    assert status["result"]["error_details"][0]["reason"] == "unreadable"


def test_import_task_status_reports_success(client, user):
    response = client.post(
        f"{CONTACTS_URL}import-csv/",
        {"file": io.BytesIO(SAMPLE_CSV.encode())},
        format="multipart",
    )
    status = client.get(f"/api/v1.0/tasks/{response.json()['task_id']}/").json()

    assert status["status"] == "SUCCESS"
    assert status["result"]["imported"] == 2


def test_import_task_does_not_carry_the_file(client, user, monkeypatch):
    """The upload goes through the cache, and is dropped once read."""
    sent = []
    original = import_contacts_task.send

    def send(*args, **kwargs):
        sent.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(import_contacts_task, "send", send)
    client.post(
        f"{CONTACTS_URL}import-csv/",
        {"file": io.BytesIO(SAMPLE_CSV.encode())},
        format="multipart",
    )

    assert len(sent) == 1
    file_key = sent[0][2]
    assert len(file_key) < 64
    assert cache.get(file_key) is None


def test_task_status_is_private_to_its_owner(client, user):
    response = client.post(
        f"{CONTACTS_URL}import-csv/",
        {"file": io.BytesIO(SAMPLE_CSV.encode())},
        format="multipart",
    )
    task_url = f"/api/v1.0/tasks/{response.json()['task_id']}/"
    other = APIClient()
    other.force_login(factories.UserFactory())

    assert other.get(task_url).status_code == 404
    assert APIClient().get(task_url).status_code in (401, 403)


def test_task_status_requires_the_entitlement(client, user, monkeypatch):
    response = client.post(
        f"{CONTACTS_URL}import-csv/",
        {"file": io.BytesIO(SAMPLE_CSV.encode())},
        format="multipart",
    )
    monkeypatch.setattr(
        "core.api.permissions.get_user_entitlements",
        lambda *_args, **_kwargs: {"can_access": False},
    )

    task_url = f"/api/v1.0/tasks/{response.json()['task_id']}/"
    assert client.get(task_url).status_code == 403
