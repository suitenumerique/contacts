"""Management command creating demo data for local development.

Aligned with the Keycloak dev realm (docker/auth/realm.json): logging in
as user1@example.local / user2 / user3 lands on an account that already
owns contacts, groups and labels. user1 gets a town hall address book
(see _demo_data.py); the organization also has org-wide rows and rows
synced from an external source, one of them overlaid by user1.

All names, domains and numbers are fictional.
"""

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand

from core import models

from ._demo_data import (
    ADDRESSES,
    FAVORITE_CONTACTS,
    NAME_SAMPLES,
    NOTES,
    PERSONAL_CONTACTS,
    PERSONAL_GROUPS,
)

# Matches the "siret" attribute of the dev realm users. Fails the SIREN
# checksum, so no real organization can have it.
DEMO_ORG_EXTERNAL_ID = "12345678900011"
DEMO_ORG_NAME = "Mairie de Brigny"

DEMO_USERS = [
    ("user1@example.local", "User One"),
    ("user2@example.local", "User Two"),
    ("user3@example.local", "User Three"),
]

# Org-wide rows (owner NULL): writable by the organization's admins only.
# (full_name, email, company)
ORG_CONTACTS = [
    ("Support informatique", "support@brigny.example", "Mairie de Brigny"),
    ("Accueil de la mairie", "accueil@brigny.example", "Mairie de Brigny"),
]

# Rows provisioned by an external source (source set): read-only, and the
# first one gets a local overlay for user1.
# (external_id, given_name, family_name, email, company, job_title)
DEMO_SYNCED_SOURCE = "directory"
DEMO_SYNCED_CONTACTS = [
    (
        "uid=claire.dubois",
        "Claire",
        "Dubois",
        "claire.dubois@brigny.example",
        "Mairie de Brigny",
        "Secrétaire générale",
    ),
    (
        "uid=hugo.fontaine",
        "Hugo",
        "Fontaine",
        "hugo.fontaine@brigny.example",
        "Mairie de Brigny",
        "Administrateur système",
    ),
]
# (external_id, name, email)
DEMO_SYNCED_GROUP = (
    "cn=annuaire-brigny",
    "Annuaire de la mairie",
    "annuaire@brigny.example",
)


class Command(BaseCommand):
    """Create demo organization, users, contacts, groups and labels."""

    help = "Create demo data for local development"

    def handle(self, *args, **options):
        """Create the demo dataset (idempotent)."""
        org, _ = models.Organization.objects.get_or_create(
            external_id=DEMO_ORG_EXTERNAL_ID, defaults={"name": DEMO_ORG_NAME}
        )

        users = []
        for email, full_name in DEMO_USERS:
            user, _ = models.User.objects.get_or_create(
                email=email,
                defaults={
                    "full_name": full_name,
                    "organization": org,
                    "password": make_password(None),
                },
            )
            models.ContactLabel.ensure_defaults(user)
            users.append(user)
        user1 = users[0]

        contacts = self._create_personal_contacts(user1, org)
        self._create_personal_groups(user1, org, contacts)
        self._create_directory_rows(user1, org)

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo data ready: org '{org.name}', {len(users)} users, "
                f"{models.Contact.objects.filter(organization=org).count()} contacts."
            )
        )

    @staticmethod
    def _create_personal_contacts(user, org):
        """user1's address book, keyed by email."""
        labels = {label.name: label for label in user.contact_labels.all()}
        contacts = {}
        for row in PERSONAL_CONTACTS:
            civility, given, family, job, company, department, email, phone, label = row
            address = ADDRESSES.get(email)
            # Saved one by one (not QuerySet.update) so the search fields
            # include every field.
            contact, _ = models.Contact.objects.get_or_create(
                owner=user,
                organization=org,
                given_name=given,
                family_name=family,
                defaults={
                    "civility": civility,
                    "full_name": f"{given} {family}",
                    "emails": [{"type": "work", "value": email}],
                    "phones": [{"type": "cell", "value": phone}] if phone else [],
                    "addresses": (
                        [{"type": address[0], "value": address[1]}] if address else []
                    ),
                    "company": company,
                    "department": department,
                    "job_title": job,
                    "notes": NOTES.get(email, ""),
                },
            )
            if label in labels:
                contact.labels.add(labels[label])
            if email in FAVORITE_CONTACTS:
                contact.favorited_by.add(user)
            contacts[email] = contact
        for sample in NAME_SAMPLES:
            models.Contact.objects.get_or_create(
                owner=user,
                organization=org,
                full_name=sample["full_name"],
                defaults=sample,
            )
        return contacts

    @staticmethod
    def _create_personal_groups(user, org, contacts):
        for name, description, favorite, member_emails in PERSONAL_GROUPS:
            group, _ = models.ContactGroup.objects.get_or_create(
                owner=user,
                organization=org,
                name=name,
                defaults={"description": description},
            )
            if favorite:
                group.favorited_by.add(user)
            for email in member_emails:
                models.ContactGroupMembership.objects.get_or_create(
                    group=group, contact=contacts[email]
                )

    @staticmethod
    def _create_directory_rows(user, org):
        for name, email, company in ORG_CONTACTS:
            models.Contact.objects.get_or_create(
                owner=None,
                organization=org,
                full_name=name,
                defaults={
                    "emails": [{"type": "work", "value": email}],
                    "company": company,
                },
            )

        synced = []
        for external_id, given, family, email, company, job in DEMO_SYNCED_CONTACTS:
            row, _ = models.Contact.objects.get_or_create(
                source=DEMO_SYNCED_SOURCE,
                external_id=external_id,
                defaults={
                    "owner": None,
                    "organization": org,
                    "full_name": f"{given} {family}",
                    "given_name": given,
                    "family_name": family,
                    "emails": [{"type": "work", "value": email}],
                    "company": company,
                    "job_title": job,
                },
            )
            synced.append(row)

        # Renamed locally; every other field still follows the source.
        models.Contact.objects.get_or_create(
            owner=user,
            overlay_of=synced[0],
            defaults={
                "organization": org,
                "full_name": "Claire D. (annuaire)",
                "overridden_fields": ["full_name"],
            },
        )

        group_extid, group_name, group_email = DEMO_SYNCED_GROUP
        group, _ = models.ContactGroup.objects.get_or_create(
            source=DEMO_SYNCED_SOURCE,
            external_id=group_extid,
            defaults={
                "owner": None,
                "organization": org,
                "name": group_name,
                "email": group_email,
            },
        )
        for row in synced:
            models.ContactGroupMembership.objects.get_or_create(
                group=group, contact=row
            )
