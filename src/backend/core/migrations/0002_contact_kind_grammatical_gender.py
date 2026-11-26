from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="contact",
            name="kind",
            field=models.CharField(
                blank=True,
                choices=[
                    ("individual", "Individual"),
                    ("org", "Organization"),
                    ("", "Not stated"),
                ],
                default="",
                max_length=16,
            ),
        ),
        migrations.RenameField(
            model_name="contact",
            old_name="civility",
            new_name="grammatical_gender",
        ),
        migrations.AlterField(
            model_name="contact",
            name="grammatical_gender",
            field=models.CharField(
                blank=True,
                choices=[
                    ("animate", "Animate"),
                    ("common", "Common"),
                    ("feminine", "Feminine"),
                    ("inanimate", "Inanimate"),
                    ("masculine", "Masculine"),
                    ("neuter", "Neuter"),
                    ("", "Not stated"),
                ],
                default="",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="contact",
            name="name_prefix",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Title before the name (Dr, Pr, Me).",
            ),
        ),
    ]
