from django.db import migrations

GENDERS = {"mr": "masculine", "mrs": "feminine"}


def _rename_overridden(Contact, old, new):
    overlays = list(Contact.objects.filter(overridden_fields__contains=[old]))
    for overlay in overlays:
        overlay.overridden_fields = [
            new if field == old else field for field in overlay.overridden_fields
        ]
    Contact.objects.bulk_update(overlays, ["overridden_fields"])


def civility_to_gender(apps, schema_editor):
    Contact = apps.get_model("core", "Contact")
    for civility, gender in GENDERS.items():
        Contact.objects.filter(grammatical_gender=civility).update(
            grammatical_gender=gender
        )
    _rename_overridden(Contact, "civility", "grammatical_gender")


def gender_to_civility(apps, schema_editor):
    Contact = apps.get_model("core", "Contact")
    Contact.objects.exclude(grammatical_gender__in=GENDERS.values()).update(
        grammatical_gender=""
    )
    for civility, gender in GENDERS.items():
        Contact.objects.filter(grammatical_gender=gender).update(
            grammatical_gender=civility
        )
    _rename_overridden(Contact, "grammatical_gender", "civility")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_contact_kind_grammatical_gender"),
    ]

    operations = [
        migrations.RunPython(civility_to_gender, gender_to_civility),
    ]
