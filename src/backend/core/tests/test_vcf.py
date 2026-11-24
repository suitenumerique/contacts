"""Tests for vCard parsing, import task and round-tripping."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument,too-many-arguments

import io

from django.core.files.base import ContentFile

import pytest
from rest_framework.test import APIClient

from core import factories, models, tasks
from core.services.vcf_service import parse_vcf, parse_vcf_file, serialize_contacts
from core.tasks import import_contacts

pytestmark = pytest.mark.django_db

SAMPLE_VCF = """BEGIN:VCARD
VERSION:3.0
FN:Jeanne Martin
N:Martin;Jeanne;;;
EMAIL;TYPE=WORK:jeanne@example.org
EMAIL;TYPE=HOME:jeanne@perso.example
TEL;TYPE=CELL:+33639985678
ORG:Brigny Agglo
TITLE:Cheffe de projet
NOTE:VIP
URL:https://example.org/jeanne
X-CUSTOM:keepme
END:VCARD
BEGIN:VCARD
VERSION:4.0
FN:Paul Durand
EMAIL:paul@example.org
END:VCARD
"""


def test_parse_vcf_fields():
    cards, errors = parse_vcf(SAMPLE_VCF)
    assert errors == 0
    assert len(cards) == 2

    jeanne = cards[0]
    assert jeanne["full_name"] == "Jeanne Martin"
    assert jeanne["given_name"] == "Jeanne"
    assert jeanne["family_name"] == "Martin"
    assert {"type": "work", "value": "jeanne@example.org"} in jeanne["emails"]
    assert jeanne["phones"][0]["type"] == "cell"
    assert jeanne["company"] == "Brigny Agglo"
    assert jeanne["job_title"] == "Cheffe de projet"
    assert jeanne["notes"] == "VIP"
    assert jeanne["vcard_extra"] == {"X-CUSTOM": ["X-CUSTOM:keepme"]}


def counters(result):
    """The contact counts of a result (no error descriptions, no groups)."""
    return {
        key: value
        for key, value in result.items()
        if key not in ("error_details", "groups")
    }


def test_import_task_creates_contacts_idempotently():
    user = factories.UserFactory()
    data = SAMPLE_VCF.encode()

    result = import_contacts(user, "vcf", data)
    assert counters(result) == {"imported": 2, "skipped": 0, "errors": 0, "total": 2}
    assert not result["error_details"]
    assert models.Contact.objects.filter(owner=user).count() == 2

    # importing the same file again skips everything
    result = import_contacts(user, "vcf", data)
    assert counters(result) == {"imported": 0, "skipped": 2, "errors": 0, "total": 2}
    assert models.Contact.objects.filter(owner=user).count() == 2


NO_EMAIL_VCF = """BEGIN:VCARD
VERSION:3.0
FN:Only Fullname No Email
TEL:0199000405
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Nameless But Reachable
TEL:+33 6 39 98 33 44
END:VCARD
"""


def test_import_skips_email_less_cards_already_present():
    """Cards with no email must not be re-created on every import."""
    user = factories.UserFactory()
    data = NO_EMAIL_VCF.encode()

    result = import_contacts(user, "vcf", data)
    assert counters(result) == {"imported": 2, "skipped": 0, "errors": 0, "total": 2}

    result = import_contacts(user, "vcf", data)
    assert counters(result) == {"imported": 0, "skipped": 2, "errors": 0, "total": 2}
    assert models.Contact.objects.filter(owner=user).count() == 2


def test_import_matches_an_email_less_card_across_phone_formatting():
    """The phone is compared on digits, not on how the card spells it."""
    user = factories.UserFactory()
    import_contacts(user, "vcf", NO_EMAIL_VCF.encode())

    respaced = NO_EMAIL_VCF.replace("+33 6 39 98 33 44", "+33639983344")
    result = import_contacts(user, "vcf", respaced.encode())

    assert result["skipped"] == 2
    assert models.Contact.objects.filter(owner=user).count() == 2


def test_import_keeps_distinct_email_less_people_apart():
    """The fallback key must not collapse two different contacts."""
    user = factories.UserFactory()
    two_people = NO_EMAIL_VCF.replace("Nameless But Reachable", "Someone Else")

    result = import_contacts(user, "vcf", two_people.encode())

    assert result["imported"] == 2
    assert models.Contact.objects.filter(owner=user).count() == 2


INVALID_VCF = """BEGIN:VCARD
VERSION:3.0
FN:Bad Address
EMAIL:not-an-email
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:No Way To Reach Me
ORG:Somewhere
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Fine Person
EMAIL:fine@example.org
END:VCARD
"""


def test_import_rejects_malformed_emails_and_says_which():
    """A malformed email is an error, not a contact the API would refuse."""
    user = factories.UserFactory()

    result = import_contacts(user, "vcf", INVALID_VCF.encode())

    assert counters(result) == {"imported": 2, "skipped": 0, "errors": 1, "total": 3}
    assert set(
        models.Contact.objects.filter(owner=user).values_list("full_name", flat=True)
    ) == {"No Way To Reach Me", "Fine Person"}

    assert len(result["error_details"]) == 1
    detail = result["error_details"][0]
    assert detail["reason"] == "invalid_email"
    assert detail["name"] == "Bad Address"
    assert detail["value"] == "not-an-email"
    assert detail["entry"] == 1


def test_import_error_details_are_capped():
    """A wholly broken file must not ship itself back to the browser."""
    user = factories.UserFactory()
    card = "BEGIN:VCARD\nVERSION:3.0\nFN:Person {n}\nEMAIL:bad-{n}\nEND:VCARD\n"
    many = "".join(card.format(n=index) for index in range(40))

    result = import_contacts(user, "vcf", many.encode())

    assert result["errors"] == 40
    assert len(result["error_details"]) == tasks.MAX_ERROR_DETAILS


REFUSED_VALUE_VCF = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Jean Dupont\r\n"
    "EMAIL:jean@example.org\r\nTEL:+33199000405\r\n{line}\r\nEND:VCARD\r\n"
)


@pytest.mark.parametrize(
    ("line", "reason", "value"),
    [
        (f"TEL;TYPE=CELL:{'1' * 65}", "invalid_phone", "1" * 65),
        ("URL:javascript:alert(1)", "invalid_url", "javascript:alert(1)"),
        (
            "item1.URL;TYPE=WORK,pref:ftp://example.org/x",
            "invalid_url",
            "ftp://example.org/x",
        ),
    ],
    ids=["phone", "url-script", "url-ftp"],
)
def test_import_rejects_a_card_with_a_value_that_the_api_refuses(line, reason, value):
    """The import does not remove a value. It rejects the card and says why."""
    user = factories.UserFactory()

    result = import_contacts(user, "vcf", REFUSED_VALUE_VCF.format(line=line).encode())

    assert counters(result) == {"imported": 0, "skipped": 0, "errors": 1, "total": 1}
    assert result["error_details"] == [
        {"entry": 1, "name": "Jean Dupont", "reason": reason, "value": value}
    ]
    assert not models.Contact.objects.filter(owner=user).exists()


def test_import_rejects_a_card_with_too_many_values():
    user = factories.UserFactory()
    phones = "\r\n".join(f"TEL:+3319900{index:04}" for index in range(51))

    result = import_contacts(
        user, "vcf", REFUSED_VALUE_VCF.format(line=phones).encode()
    )

    assert counters(result) == {"imported": 0, "skipped": 0, "errors": 1, "total": 1}
    assert result["error_details"][0]["reason"] == "too_many_values"


def test_import_error_detail_quotes_the_start_of_a_long_value():
    """A long value must not fill the result."""
    user = factories.UserFactory()
    line = f"ADR;TYPE=HOME:;;{'x' * 1025};;;;"

    result = import_contacts(user, "vcf", REFUSED_VALUE_VCF.format(line=line).encode())

    detail = result["error_details"][0]
    assert detail["reason"] == "value_too_long"
    assert detail["value"] == "x" * tasks.MAX_QUOTED_VALUE_LENGTH + "…"


def test_import_turns_categories_into_labels():
    user = factories.UserFactory()
    models.ContactLabel.objects.create(owner=user, name="Amis")
    data = (
        "BEGIN:VCARD\nVERSION:3.0\nFN:Ada\nEMAIL:ada@example.org\n"
        "CATEGORIES:amis,Travail\nEND:VCARD\n"
    )

    import_contacts(user, "vcf", data.encode())

    contact = models.Contact.objects.get(owner=user)
    assert sorted(label.name for label in contact.labels.all()) == ["Amis", "Travail"]
    assert "CATEGORIES" not in contact.vcard_extra


GROUPS_VCF = (
    # The group comes first: members are resolved once every card is in.
    "BEGIN:VCARD\nVERSION:3.0\nUID:team-uid\nFN:Équipe\n"
    "X-ADDRESSBOOKSERVER-KIND:group\n"
    "X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:ada-uid\n"
    "X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:bob-uid\n"
    "X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:nobody\nEND:VCARD\n"
    "BEGIN:VCARD\nVERSION:3.0\nUID:ada-uid\nFN:Ada\nEMAIL:ada@example.org\n"
    "END:VCARD\n"
    "BEGIN:VCARD\nVERSION:3.0\nUID:bob-uid\nFN:Bob\nEMAIL:bob@example.org\n"
    "END:VCARD\n"
)


def test_import_creates_the_groups_of_group_cards():
    user = factories.UserFactory()

    result = import_contacts(user, "vcf", GROUPS_VCF.encode())

    # A group card is not a contact: it is not counted as one.
    assert counters(result) == {"imported": 2, "skipped": 0, "errors": 0, "total": 2}
    assert result["groups"] == 1
    group = models.ContactGroup.objects.get(owner=user)
    assert group.name == "Équipe"
    assert sorted(member.full_name for member in group.members.all()) == [
        "Ada",
        "Bob",
    ]


def test_reimporting_group_cards_adds_nothing():
    user = factories.UserFactory()
    import_contacts(user, "vcf", GROUPS_VCF.encode())

    result = import_contacts(user, "vcf", GROUPS_VCF.encode())

    assert result["skipped"] == 2
    group = models.ContactGroup.objects.get(owner=user)
    assert group.members.count() == 2


def test_a_member_already_there_under_another_uid_joins():
    """A card skipped as a duplicate stands for the contact it matched."""
    user = factories.UserFactory()
    ada = factories.ContactFactory(
        owner=user, full_name="Ada", emails=[{"type": "", "value": "ada@example.org"}]
    )

    import_contacts(user, "vcf", GROUPS_VCF.encode())

    group = models.ContactGroup.objects.get(owner=user)
    assert ada in group.members.all()


def test_imported_group_joins_the_same_named_group():
    user = factories.UserFactory()
    existing = factories.ContactGroupFactory(owner=user, name="équipe")
    kept = factories.ContactFactory(owner=user, full_name="Kept")
    models.ContactGroupMembership.objects.create(group=existing, contact=kept)

    import_contacts(user, "vcf", GROUPS_VCF.encode())

    assert models.ContactGroup.objects.filter(owner=user).count() == 1
    assert sorted(member.full_name for member in existing.members.all()) == [
        "Ada",
        "Bob",
        "Kept",
    ]


def test_group_card_members_may_be_contacts_already_there():
    """A file holding only a group names the user's contacts by UID."""
    user = factories.UserFactory()
    by_uid = factories.ContactFactory(
        owner=user, full_name="Ada", vcard_extra={"UID": ["UID:ada-uid"]}
    )
    by_id = factories.ContactFactory(owner=user, full_name="Bob")
    factories.ContactFactory(full_name="Someone else's", vcard_extra={"UID": ["UID:x"]})
    data = (
        "BEGIN:VCARD\nVERSION:3.0\nFN:Team\nX-ADDRESSBOOKSERVER-KIND:group\n"
        "X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:ada-uid\n"
        f"X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:{by_id.pk}\n"
        "X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:x\nEND:VCARD\n"
    )

    result = import_contacts(user, "vcf", data.encode())

    assert result["total"] == 0
    group = models.ContactGroup.objects.get(owner=user)
    assert set(group.members.all()) == {by_uid, by_id}


def test_vcard4_group_card_is_imported():
    user = factories.UserFactory()
    data = GROUPS_VCF.replace("X-ADDRESSBOOKSERVER-", "")
    import_contacts(user, "vcf", data.encode())
    assert models.ContactGroup.objects.get(owner=user).members.count() == 2


def test_export_writes_labels_as_categories():
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)
    contact = factories.ContactFactory(owner=user, full_name="Ada")
    contact.labels.add(models.ContactLabel.objects.create(owner=user, name="Agent"))

    body = client.get("/api/v1.0/contacts/export-vcf/").content.decode()
    assert "\r\nCATEGORIES:Agent\r\n" in body


def test_import_reads_an_exported_contact_without_email_and_phone():
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)
    factories.ContactFactory(
        owner=user,
        full_name="Jean Dupont",
        emails=[],
        phones=[],
        addresses=[{"type": "home", "value": "1 rue de la Mairie"}],
    )
    export = client.get("/api/v1.0/contacts/export-vcf/").content

    other = factories.UserFactory()
    result = import_contacts(other, "vcf", export)
    assert counters(result) == {"imported": 1, "skipped": 0, "errors": 0, "total": 1}
    restored = models.Contact.objects.get(owner=other)
    assert (restored.full_name, restored.emails, restored.phones) == (
        "Jean Dupont",
        [],
        [],
    )
    assert "1 rue de la Mairie" in restored.addresses[0]["value"]

    # The card matches the contact of the first user on its name and its UID.
    result = import_contacts(user, "vcf", export)
    assert counters(result) == {"imported": 0, "skipped": 1, "errors": 0, "total": 1}


def test_import_keeps_two_exported_contacts_with_the_same_name():
    """The contacts have no email and no phone. Their UIDs tell them apart."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)
    streets = {"1 rue de la Mairie", "2 place du Marché"}
    for street in streets:
        factories.ContactFactory(
            owner=user,
            full_name="Jean Dupont",
            emails=[],
            phones=[],
            addresses=[{"type": "home", "value": street}],
        )
    export = client.get("/api/v1.0/contacts/export-vcf/").content

    other = factories.UserFactory()
    result = import_contacts(other, "vcf", export)
    assert counters(result) == {"imported": 2, "skipped": 0, "errors": 0, "total": 2}
    restored = models.Contact.objects.filter(owner=other)
    assert {contact.addresses[0]["value"] for contact in restored} == streets

    for target in (other, user):
        result = import_contacts(target, "vcf", export)
        assert counters(result) == {
            "imported": 0,
            "skipped": 2,
            "errors": 0,
            "total": 2,
        }


def test_import_reads_an_exported_contact_with_a_long_text():
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)
    title = "x" * 300
    line = f"TITLE:{title}"
    import_contacts(user, "vcf", REFUSED_VALUE_VCF.format(line=line).encode())
    assert models.Contact.objects.get(owner=user).job_title == title
    export = client.get("/api/v1.0/contacts/export-vcf/").content
    assert line in export.decode().replace("\r\n ", "").split("\r\n")

    other = factories.UserFactory()
    result = import_contacts(other, "vcf", export)

    assert counters(result) == {"imported": 1, "skipped": 0, "errors": 0, "total": 1}
    assert models.Contact.objects.get(owner=other).job_title == title


def test_import_maps_google_system_categories():
    """A Google Contacts export lists its system groups in CATEGORIES."""
    user = factories.UserFactory()
    data = (
        "BEGIN:VCARD\nVERSION:3.0\nFN:Ada\nEMAIL:ada@example.org\n"
        "CATEGORIES:myContacts,starred,Amis\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:3.0\nFN:Bob\nEMAIL:bob@example.org\n"
        "CATEGORIES:myContacts\nEND:VCARD\n"
    )

    import_contacts(user, "vcf", data.encode())

    ada = models.Contact.objects.get(owner=user, full_name="Ada")
    assert [label.name for label in ada.labels.all()] == ["Amis"]
    assert ada.favorited_by.filter(pk=user.pk).exists()
    bob = models.Contact.objects.get(owner=user, full_name="Bob")
    assert not bob.labels.exists()
    assert not bob.favorited_by.exists()
    assert not models.ContactLabel.objects.filter(owner=user).exclude(name="Amis")


def test_import_endpoint_queues_task():
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.post(
        "/api/v1.0/contacts/import-vcf/",
        {"file": io.BytesIO(SAMPLE_VCF.encode())},
        format="multipart",
    )
    assert response.status_code == 202, response.content
    assert "task_id" in response.json()
    # Test settings use the eager broker: the import already ran.
    assert models.Contact.objects.filter(owner=user).count() == 2


def test_import_endpoint_rejects_large_file(settings):
    settings.CONTACTS_VCF_MAX_FILE_SIZE = 10
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.post(
        "/api/v1.0/contacts/import-vcf/",
        {"file": io.BytesIO(SAMPLE_VCF.encode())},
        format="multipart",
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("endpoint", "name", "content", "error"),
    [
        ("import-vcf", "contacts.vcf", b" \r\n", "empty"),
        ("import-csv", "contacts.csv", b"", "empty"),
        ("import-vcf", "photo.vcf", b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR", "not a text"),
        ("import-csv", "sheet.csv", b"PK\x03\x04\x14\0\x06\0", "not a text file"),
        ("import-csv", "utf16.csv", "Email\na@example.org\n".encode("utf-16"), "text"),
        ("import-vcf", "contacts.vcf", b"Email\na@example.org\n", "no vCard"),
        ("import-vcf", "note.vcf", b"NOTE:BEGIN:VCARD\r\n", "no vCard"),
    ],
)
def test_import_endpoint_rejects_a_file_of_another_type(endpoint, name, content, error):
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.post(
        f"/api/v1.0/contacts/{endpoint}/",
        {"file": ContentFile(content, name=name)},
        format="multipart",
    )

    assert response.status_code == 400
    assert error in response.json()["errors"][0]["detail"]
    assert not models.Contact.objects.filter(owner=user).exists()


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("CONTACTS.VCF", SAMPLE_VCF.encode()),
        ("contacts.vcard", SAMPLE_VCF.encode()),
        ("contacts.txt", SAMPLE_VCF.encode()),
        ("bom.vcf", b"\xef\xbb\xbf\r\n" + SAMPLE_VCF.encode()),
        ("latin.vcf", SAMPLE_VCF.replace("Jeanne", "Ren\xe9e").encode("cp1252")),
        ("lower.vcf", SAMPLE_VCF.replace("BEGIN:VCARD", "begin: vcard").encode()),
        ("folded.vcf", SAMPLE_VCF.replace("BEGIN:VCARD", "BEGIN:VC\r\n ARD").encode()),
        # This card has no END. The import reports it as an error.
        ("truncated.vcf", b"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Alice\r\n"),
    ],
)
def test_import_endpoint_accepts_vcard_variants(name, content):
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.post(
        "/api/v1.0/contacts/import-vcf/",
        {"file": ContentFile(content, name=name)},
        format="multipart",
    )

    assert response.status_code == 202, response.content


def test_round_trip_preserves_fields():
    user = factories.UserFactory()
    import_contacts(user, "vcf", SAMPLE_VCF.encode())

    contacts = models.Contact.objects.filter(owner=user).order_by("sort_key")
    output = serialize_contacts(contacts)
    cards, errors = parse_vcf(output)
    assert errors == 0
    assert len(cards) == 2
    jeanne = next(c for c in cards if c["full_name"] == "Jeanne Martin")
    assert {"type": "work", "value": "jeanne@example.org"} in jeanne["emails"]
    assert jeanne["company"] == "Brigny Agglo"
    # A card imported without a UID is served with its row's id.
    row = contacts.get(full_name="Jeanne Martin")
    assert jeanne["vcard_extra"] == {
        "X-CUSTOM": ["X-CUSTOM:keepme"],
        "UID": [f"UID:{row.pk}"],
    }


# --- round-trip fidelity ---------------------------------------------

JPEG = "/9j/4AAQSkZJRgABAQ=="

RICH_VCF = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:abc-123\r\nFN:Jean Doe\r\n"
    "N:Doe;John,Paul;Q;Mr.;Jr\r\n"
    "item1.EMAIL;TYPE=INTERNET,WORK;TYPE=pref:jd@example.org\r\n"
    "item1.X-ABLabel:custom\r\nEMAIL;TYPE=HOME:j@home.example\r\n"
    "TEL;TYPE=CELL,VOICE:+33 6 39 98 00 00\r\n"
    "ADR;TYPE=HOME:;;1 rue X\\nBat B;Paris;IDF;75001;France\r\n"
    "URL;TYPE=WORK:https://x.example/a,b\r\nCATEGORIES:friends,work\r\n"
    "KEY;ENCODING=b;TYPE=X509:AAECAw==\r\nORG:Brigny Agglo;Direction;Service\r\n"
    "TITLE:Chef, projet\r\nNOTE:line1\\nline2\r\n"
    'X-FOO;X-PARAM="a:b":val\\, with comma\r\n'
    f"PHOTO;ENCODING=b;TYPE=JPEG:{JPEG}\r\nEND:VCARD\r\n"
)


def round_trip(fields):
    """Store parsed *fields* as a user's contact, serialize it, parse it back."""
    fields = dict(fields)
    label_names = fields.pop("label_names", [])
    user = factories.UserFactory()
    contact = models.Contact.objects.create(
        owner=user, organization=user.organization, **fields
    )
    contact.labels.add(*models.ContactLabel.for_names(user, label_names))
    output = serialize_contacts([contact])
    cards, errors = parse_vcf(output)
    assert errors == 0
    return cards[0], output


def test_rich_card_round_trip_is_stable():
    """parse -> serialize -> parse gives the same fields, and the second
    serialization is byte-identical to the first."""
    (card,), _errors = parse_vcf(RICH_VCF)
    again, output = round_trip(card)
    assert again == card
    assert round_trip(again)[1] == output


def test_rich_card_keeps_what_the_columns_cannot_hold():
    (card,), _errors = parse_vcf(RICH_VCF)
    _again, output = round_trip(card)
    for line in (
        "UID:abc-123",
        "N:Doe;John,Paul;Q;Mr.;Jr",
        "item1.EMAIL;TYPE=INTERNET,WORK,pref:jd@example.org",
        "item1.X-ABLABEL:custom",
        "ADR;TYPE=HOME:;;1 rue X\\nBat B;Paris;IDF;75001;France",
        "URL;TYPE=WORK:https://x.example/a,b",
        "CATEGORIES:friends,work",
        "KEY;ENCODING=b;TYPE=X509:AAECAw==",
        "ORG:Brigny Agglo;Direction;Service",
        'X-FOO;X-PARAM="a:b":val\\, with comma',
        f"PHOTO;ENCODING=b;TYPE=JPEG:{JPEG}",
    ):
        assert f"\r\n{line}\r\n" in output, line


def test_modeled_values_are_decoded():
    (card,), _errors = parse_vcf(RICH_VCF)
    assert card["given_name"] == "John Paul"  # N components may be lists
    assert card["middle_name"] == "Q"
    assert card["name_suffix"] == "Jr"
    assert card["civility"] == "mr"
    assert card["name_prefix"] == ""  # "Mr." is the civility
    assert card["company"] == "Brigny Agglo"
    assert card["department"] == "Direction, Service"
    assert card["job_title"] == "Chef, projet"  # not cut at the comma
    assert card["urls"] == [{"type": "work", "value": "https://x.example/a,b"}]
    assert card["addresses"] == [
        {"type": "home", "value": "1 rue X\nBat B\n75001 Paris\nIDF\nFrance"}
    ]
    assert card["photo"] == f"data:image/jpeg;base64,{JPEG}"


REPEATED_VCF = (
    "BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada\r\nFN;LANGUAGE=en:Ada L.\r\n"
    "N:Lovelace;Ada;;;\r\nN:Other;Name;;;\r\n"
    "ORG:Brigny Agglo\r\nORG:Mairie de Brigny\r\nTITLE:Cheffe\r\nTITLE:Élue\r\n"
    "NOTE:first\r\nNOTE:second\r\nEMAIL:ada@example.org\r\nEND:VCARD\r\n"
)


def test_repeated_single_properties_are_kept():
    """Later instances of a one-column property survive the round trip."""
    (card,), _errors = parse_vcf(REPEATED_VCF)
    assert (card["company"], card["job_title"], card["notes"]) == (
        "Brigny Agglo",
        "Cheffe",
        "first",
    )
    assert card["family_name"] == "Lovelace"
    assert card["vcard_extra"]["+"] == [
        "FN;LANGUAGE=en:Ada L.",
        "ORG:Mairie de Brigny",
        "TITLE:Élue",
        "NOTE:second",
    ]

    again, output = round_trip(card)
    del again["vcard_extra"]["UID"]  # added by the serializer
    assert again == card
    for line in ("ORG:Mairie de Brigny", "TITLE:Élue", "NOTE:second"):
        assert f"\r\n{line}\r\n" in output
    # vCard allows one N: the second is not written back.
    assert "N:Other" not in output


@pytest.mark.parametrize("prefix", ["Mlle", "Melle", "Mademoiselle", "Miss", "Ms."])
def test_mademoiselle_and_ms_read_as_madame(prefix):
    vcard = (
        f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada\r\nN:;Ada;;{prefix},Dr;\r\nEND:VCARD\r\n"
    )
    (card,), _errors = parse_vcf(vcard)
    assert card["civility"] == "mrs"
    assert card["name_prefix"] == "Dr"


def test_an_edited_column_wins_over_its_shadow_line():
    (card,), _errors = parse_vcf(RICH_VCF)
    card["addresses"] = [{"type": "work", "value": "2 rue Y\n69000 Lyon"}]
    card["family_name"] = "Smith"
    _again, output = round_trip(card)
    assert "\r\nADR;TYPE=WORK:;;2 rue Y\\n69000 Lyon;;;;\r\n" in output
    # Rebuilt from the columns, which hold the middle name and suffix too.
    assert "\r\nN:Smith;John Paul;Q;M.;Jr\r\n" in output
    assert "1 rue X" not in output


NAMES_VCF = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Hélène de La Tour\r\n"
    "N:de La Tour;Hélène;Marie;Mme,Dr;III\r\nNICKNAME:Léna\r\n"
    "EMAIL:helene@example.org\r\nEND:VCARD\r\n"
)


def test_every_n_component_has_a_column():
    (card,), _errors = parse_vcf(NAMES_VCF)
    assert {
        field: card[field]
        for field in (
            "full_name",
            "civility",
            "name_prefix",
            "given_name",
            "middle_name",
            "family_name",
            "name_suffix",
            "nickname",
        )
    } == {
        "full_name": "Hélène de La Tour",
        "civility": "mrs",
        "name_prefix": "Dr",
        "given_name": "Hélène",
        "middle_name": "Marie",
        "family_name": "de La Tour",
        "name_suffix": "III",
        "nickname": "Léna",
    }
    # Written back from the columns alone, not from a shadow line.
    assert "N" not in card["vcard_extra"]
    assert "NICKNAME" not in card["vcard_extra"]


def test_name_columns_written_on_the_web_serialize_to_n_and_nickname():
    contact = factories.ContactFactory(
        full_name="Hélène de La Tour",
        civility="mrs",
        name_prefix="Dr",
        given_name="Hélène",
        middle_name="Marie",
        family_name="de La Tour",
        name_suffix="III",
        nickname="Léna",
    )
    output = serialize_contacts([contact])
    assert "\r\nN:de La Tour;Hélène;Marie;Mme,Dr;III\r\n" in output
    assert "\r\nNICKNAME:Léna\r\n" in output


def test_a_nickname_list_is_read_whole_and_written_back_verbatim():
    card_text = NAMES_VCF.replace("NICKNAME:Léna", "NICKNAME:Léna,Lili")
    (card,), _errors = parse_vcf(card_text)
    assert card["nickname"] == "Léna, Lili"
    _again, output = round_trip(card)
    assert "\r\nNICKNAME:Léna,Lili\r\n" in output


@pytest.mark.parametrize(
    "full_name", ["Jean de La Fontaine", "Madonna", "Nguyễn Thị Minh Khai"]
)
def test_import_keeps_a_lone_fn_whole(full_name):
    """Splitting FN would misfile single names, particles and name orders."""
    user = factories.UserFactory()
    data = (
        f"BEGIN:VCARD\r\nVERSION:4.0\r\nFN:{full_name}\r\n"
        "EMAIL:someone@example.org\r\nEND:VCARD\r\n"
    )

    result = import_contacts(user, "vcf", data.encode())

    assert result["imported"] == 1, result
    contact = models.Contact.objects.get(owner=user)
    assert (contact.full_name, contact.given_name, contact.family_name) == (
        full_name,
        "",
        "",
    )


def test_row_written_on_the_web_serializes_inline_photo_and_uid():
    contact = factories.ContactFactory(
        given_name="Ada",
        family_name="Lovelace",
        full_name="",
        civility="mrs",
        department="Maths",
        photo="data:image/png;base64,iVBORw0KGgo=",
    )
    output = serialize_contacts([contact])
    assert "\r\nFN:Ada Lovelace\r\n" in output
    assert "\r\nN:Lovelace;Ada;;Mme;\r\n" in output
    assert "\r\nPHOTO;ENCODING=b;TYPE=PNG:iVBORw0KGgo=\r\n" in output
    assert f"\r\nUID:{contact.pk}\r\n" in output
    (card,), _errors = parse_vcf(output)
    assert card["civility"] == "mrs"
    assert card["photo"] == contact.photo


@pytest.mark.parametrize(
    "photo_line",
    [
        "PHOTO;VALUE=uri:http://tracker.example/p.png",
        "PHOTO:ftp://example.org/p.png",
        "PHOTO;ENCODING=b;TYPE=BMP:Qk0=",
        "PHOTO;ENCODING=b;TYPE=JPEG:not base64!",
    ],
)
def test_unacceptable_photos_are_dropped(photo_line):
    card = SAMPLE_VCF.split("END:VCARD", maxsplit=1)[0] + f"{photo_line}\nEND:VCARD\n"
    (parsed,), _errors = parse_vcf(card)
    assert parsed["photo"] == ""


def test_https_photo_is_kept_as_a_uri():
    card = SAMPLE_VCF.split("END:VCARD", maxsplit=1)[0]
    card += "PHOTO;VALUE=uri:https://example.org/p.png\nEND:VCARD\n"
    (parsed,), _errors = parse_vcf(card)
    assert parsed["photo"] == "https://example.org/p.png"
    assert "\r\nPHOTO;VALUE=uri:https://example.org/p.png\r\n" in round_trip(parsed)[1]


def test_vcard4_data_uri_photo_is_read_whole():
    card = (
        "BEGIN:VCARD\r\nVERSION:4.0\r\nFN:x\r\n"
        "PHOTO:data:image/png;base64,iVBORw0KGgo=\r\nEND:VCARD\r\n"
    )
    (parsed,), _errors = parse_vcf(card)
    assert parsed["photo"] == "data:image/png;base64,iVBORw0KGgo="


def test_stored_values_cannot_inject_lines():
    """Rows written without the API's validation (admin, ORM) may hold anything."""
    contact = models.Contact(
        full_name="A\r\nX-INJ1:1",
        emails=[{"type": "WORK\r\nX-INJ2:1", "value": "a@b.example\r\nX-INJ3:1"}],
        vcard_extra={
            "X-EVIL": ["X-EVIL:1\r\nX-INJ4:1"],
            "X-OK\r\nX-INJ5": ["1"],
        },
    )
    output = serialize_contacts([contact])
    lines = output.split("\r\n")
    assert not [line for line in lines if line.startswith("X-INJ")]
    assert "EMAIL:a@b.example\\nX-INJ3:1" in lines


def test_extra_values_that_are_not_content_lines_are_dropped():
    contact = models.Contact(full_name="Row", vcard_extra={"X-CUSTOM": ["keepme"]})
    assert "keepme" not in serialize_contacts([contact])


def test_empty_fn_round_trip_is_stable():
    card = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:1\r\nFN:\r\nEMAIL:a@b.example\r\nEND:VCARD\r\n"
    (parsed,), _errors = parse_vcf(card)
    assert round_trip(parsed)[0] == parsed


def test_windows_1252_file_is_decoded():
    card = "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Hélène Dûpont\r\nEND:VCARD\r\n"
    (parsed,), errors = parse_vcf(card.encode("cp1252"))
    assert errors == 0
    assert parsed["full_name"] == "Hélène Dûpont"


def test_truncated_file_keeps_the_cards_before_it():
    data = SAMPLE_VCF + "BEGIN:VCARD\nVERSION:3.0\nFN:Cut Off\n"
    cards, errors = parse_vcf(data)
    assert len(cards) == 2
    assert errors == 1


def test_parsed_fields_hold_no_nul_character():
    """A NUL is the literal character or a quoted-printable ``=00``."""
    data = (
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:A\0da\r\nN:Love\0lace;Ada;;;\r\n"
        "EMAIL;TYPE=WORK;X-BAR=a\0b:ada@exam\0ple.org\r\n"
        "ADR;ENCODING=QUOTED-PRINTABLE:;;1 rue X=00;Paris;;75001;France\r\n"
        "NOTE;ENCODING=QUOTED-PRINTABLE:first=00\r\nNOTE:second\0\r\n"
        "CATEGORIES:fri\0ends\r\nX-FOO;X-BAR=a\0b:c\0d\r\nEND:VCARD\r\n"
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN;ENCODING=QUOTED-PRINTABLE:Te=00am\r\n"
        "X-ADDRESSBOOKSERVER-KIND:group\r\n"
        "X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:a\0da-uid\r\n"
        "X-FOO:a\0b\r\nEND:VCARD\r\n"
    )
    without_nul = data.replace("\0", "").replace("=00", "")

    cards, groups, errors = parse_vcf_file(data)

    assert (len(cards), len(groups), errors) == (1, 1, 0)
    assert (cards, groups, errors) == parse_vcf_file(without_nul)


NUL_VCF = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada\r\nEMAIL;TYPE=WORK:ada@example.org\r\n"
    "NOTE;ENCODING=QUOTED-PRINTABLE:a=00b\r\n"
    "X-FOO;ENCODING=QUOTED-PRINTABLE:a=00b\r\nEND:VCARD\r\n"
    "BEGIN:VCARD\r\nVERSION:3.0\r\nFN;ENCODING=QUOTED-PRINTABLE:Te=00am\r\n"
    "X-ADDRESSBOOKSERVER-KIND:group\r\n"
    "X-FOO;ENCODING=QUOTED-PRINTABLE:a=00b\r\nEND:VCARD\r\n"
)


def test_import_stores_the_cards_without_their_nul_characters():
    """PostgreSQL cannot store a NUL character in a text or JSON column."""
    user = factories.UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.post(
        "/api/v1.0/contacts/import-vcf/",
        {"file": ContentFile(NUL_VCF.encode(), name="contacts.vcf")},
        format="multipart",
    )

    assert response.status_code == 202, response.content
    contact = models.Contact.objects.get(owner=user)
    assert (contact.notes, contact.vcard_extra) == ("ab", {"X-FOO": ["X-FOO:ab"]})
    group = models.ContactGroup.objects.get(owner=user)
    assert (group.name, group.vcard_extra) == ("Team", {"X-FOO": ["X-FOO:ab"]})
