"""Fill the database to the size the search is benchmarked at.

Development only. Rows are written with ``bulk_create`` for speed: no model
signals run, so the CardDAV change journal doesn't see them (irrelevant for a
benchmark). Everything lives in organizations whose ``external_id`` starts
with ``benchmark-`` and goes away with ``--clear``.

Defaults are the target of docs/resource_server.md ("Performance"): 1M
contacts, 200k in one organization, 10k in one personal book, 5k groups per
organization, 10M interactions. Seeding all of it takes a while; scale it
down with the options to try things out.
"""

import random
import time
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from core import models
from core.enums import Scope
from core.utils import build_search_text

PREFIX = "benchmark-"
BENCHMARK_EMAIL = "benchmark@benchmark.example"
BATCH = 5000

GIVEN = (
    "marie jean pierre michel anne philippe nathalie alain isabelle patrick "
    "sylvie nicolas catherine christophe sophie laurent martine eric "
    "francoise stephane valerie julien sandrine david celine olivier "
    "emilie thomas aurelie sebastien camille antoine lea hugo chloe lucas "
    "manon louis jade gabriel ines arthur zoe raphael alice paul louise"
).split()
FAMILY = (
    "martin bernard thomas petit robert richard durand dubois moreau "
    "laurent simon michel lefebvre leroy roux david bertrand morel fournier "
    "girard bonnet dupont lambert fontaine rousseau vincent muller lefevre "
    "faure andre mercier blanc guerin boyer garnier chevalier francois "
    "legrand gauthier garcia perrin robin clement morin nicolas henry "
    "roussel mathieu gautier masson marchand duval denis dumont marie lemaire"
).split()
WORDS = (
    "service equipe direction conseil comite pole bureau mission cellule "
    "projet groupe commission atelier reseau club unite antenne agence"
).split()


def _person(rng, index):
    given, family = rng.choice(GIVEN), rng.choice(FAMILY)
    # A suffix keeps addresses unique and gives rare tokens to search for.
    tag = f"{index:x}"
    email = f"{given}.{family}{tag}@{rng.choice(FAMILY)}.example"
    full_name = f"{given.title()} {family.title()}"
    return given, family, full_name, email


class Command(BaseCommand):
    """Seed (or --clear) benchmark data."""

    help = __doc__

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started = time.monotonic()
        self.rng = random.Random(42)  # noqa: S311  # fake data, reseeded in handle

    def add_arguments(self, parser):
        parser.add_argument("--contacts", type=int, default=1_000_000)
        parser.add_argument("--org-contacts", type=int, default=200_000)
        parser.add_argument("--personal-contacts", type=int, default=10_000)
        parser.add_argument("--groups", type=int, default=5_000)
        parser.add_argument("--interactions", type=int, default=10_000_000)
        parser.add_argument("--users", type=int, default=1_000)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument(
            "--clear", action="store_true", help="Delete the benchmark data only."
        )

    def handle(self, *args, **options):
        self.started = time.monotonic()
        if options["clear"]:
            self._clear()
            return
        if models.Organization.objects.filter(external_id__startswith=PREFIX).exists():
            raise CommandError("Benchmark data exists already: run with --clear first.")
        if options["org_contacts"] > options["contacts"]:
            raise CommandError("--org-contacts can't exceed --contacts.")
        self.rng = random.Random(options["seed"])  # noqa: S311  # fake data
        self._seed(options)

    def _log(self, message):
        elapsed = time.monotonic() - self.started
        self.stdout.write(f"[{elapsed:7.1f}s] {message}")

    def _clear(self):
        orgs = models.Organization.objects.filter(external_id__startswith=PREFIX)
        for model in (models.Interaction,):
            model.objects.filter(user__organization__in=orgs).delete()
        models.ContactGroupMembership.objects.filter(
            group__organization__in=orgs
        ).delete()
        models.ContactGroup.objects.filter(organization__in=orgs).delete()
        # Raw delete: Contact.delete() detaches overlays row by row.
        models.Contact.objects.filter(organization__in=orgs)._raw_delete("default")  # noqa: SLF001  # pylint: disable=protected-access
        models.User.objects.filter(organization__in=orgs).delete()
        orgs.delete()
        self._log("Benchmark data deleted.")

    def _seed(self, options):
        # One big organization (the target), then filler ones for the rest.
        target = models.Organization.objects.create(
            external_id=f"{PREFIX}0", name="Benchmark target"
        )
        users = [
            models.User(
                sub=f"{PREFIX}user-{index}",
                email=BENCHMARK_EMAIL if index == 0 else f"u{index}@benchmark.example",
                full_name=f"Benchmark user {index}",
                organization=target,
            )
            for index in range(options["users"])
        ]
        models.User.objects.bulk_create(users, batch_size=BATCH)
        users = list(models.User.objects.filter(organization=target).order_by("sub"))
        main = next(user for user in users if user.email == BENCHMARK_EMAIL)
        self._log(f"{len(users)} users; benchmark user: {BENCHMARK_EMAIL}")

        personal = self._contacts(
            options["personal_contacts"], target, owner=main, start=0
        )
        org_rows = self._contacts(
            options["org_contacts"], target, owner=None, start=10**7
        )
        rest = (
            options["contacts"] - options["personal_contacts"] - options["org_contacts"]
        )
        filler_index = 1
        while rest > 0:
            org = models.Organization.objects.create(
                external_id=f"{PREFIX}{filler_index}", name=f"Filler {filler_index}"
            )
            size = min(rest, 100_000)
            self._contacts(size, org, owner=None, start=10**8 * filler_index)
            rest -= size
            filler_index += 1
        self._log("Contacts done.")

        groups = self._groups(options["groups"], target, org_rows or personal)
        self._interactions(options["interactions"], users, personal + org_rows, groups)
        # Fresh statistics, or the first plans are built for empty tables.
        with connection.cursor() as cursor:
            for table in (
                models.Contact._meta.db_table,  # noqa: SLF001
                models.ContactGroup._meta.db_table,  # noqa: SLF001
                models.ContactGroupMembership._meta.db_table,  # noqa: SLF001
                models.Interaction._meta.db_table,  # noqa: SLF001
            ):
                cursor.execute(f'ANALYZE "{table}"')
        self._log("Done. Run: python manage.py benchmark_search")

    def _contacts(self, count, org, *, owner, start):
        """Create *count* contacts in *org*; return their ids."""
        ids = []
        scope = Scope.PERSONAL if owner else Scope.ORGANIZATION
        for offset in range(0, count, BATCH):
            batch = []
            for index in range(start + offset, start + min(offset + BATCH, count)):
                given, family, full_name, email = _person(self.rng, index)
                phone = f"06{self.rng.randrange(10**8):08d}"
                row = models.Contact(
                    organization=org,
                    owner=owner,
                    scope=scope,
                    full_name=full_name,
                    given_name=given.title(),
                    family_name=family.title(),
                    emails=[{"type": "work", "value": email}],
                    phones=[{"type": "cell", "value": phone}] if index % 3 == 0 else [],
                    sort_key=f"{family} {given}",
                )
                row.search_text = build_search_text(
                    full_name, given, family, email, phone if row.phones else ""
                )
                batch.append(row)
            with transaction.atomic():
                models.Contact.objects.bulk_create(batch, batch_size=BATCH)
            ids += [row.pk for row in batch]
            if (offset // BATCH) % 20 == 0:
                self._log(f"{org.external_id}: {offset + len(batch)}/{count} contacts")
        return ids

    def _groups(self, count, org, member_pool):
        """Create *count* organization groups of up to 50 members."""
        groups = []
        for index in range(count):
            name = f"{self.rng.choice(WORDS).title()} {self.rng.choice(FAMILY).title()} {index}"
            row = models.ContactGroup(
                organization=org, scope=Scope.ORGANIZATION, name=name
            )
            row.search_text = build_search_text(name)
            groups.append(row)
        models.ContactGroup.objects.bulk_create(groups, batch_size=BATCH)
        memberships = [
            models.ContactGroupMembership(group=row, contact_id=contact_id)
            for row in groups
            for contact_id in self.rng.sample(
                member_pool, min(len(member_pool), self.rng.randrange(1, 50))
            )
        ]
        models.ContactGroupMembership.objects.bulk_create(
            memberships, batch_size=BATCH, ignore_conflicts=True
        )
        self._log(f"{len(groups)} groups, {len(memberships)} memberships")
        return [row.pk for row in groups]

    def _interactions(self, count, users, contact_ids, group_ids):
        """Spread *count* interactions over *users*; the benchmark user gets 1000."""
        main = next(user for user in users if user.email == BENCHMARK_EMAIL)
        now = timezone.now()
        main_share = min(1000, count)
        per_user = max((count - main_share) // max(len(users) - 1, 1), 0)
        total = 0
        for user in users:
            wanted = main_share if user.pk == main.pk else per_user
            if not wanted:
                continue
            batch = []
            contacts = self.rng.sample(contact_ids, min(wanted, len(contact_ids)))
            for contact_id in contacts:
                days = self.rng.random() * 365
                batch.append(
                    models.Interaction(
                        user=user,
                        contact_id=contact_id,
                        count=self.rng.randrange(1, 20),
                        score=self.rng.random() * 10,
                        last_interaction_at=now - timedelta(days=days),
                    )
                )
            for group_id in self.rng.sample(
                group_ids, min(len(group_ids), wanted // 20)
            ):
                batch.append(
                    models.Interaction(
                        user=user,
                        group_id=group_id,
                        count=1,
                        score=1,
                        last_interaction_at=now
                        - timedelta(days=self.rng.random() * 90),
                    )
                )
            models.Interaction.objects.bulk_create(batch, batch_size=BATCH)
            total += len(batch)
            if total and total % 500_000 < len(batch):
                self._log(f"{total}/{count} interactions")
        self._log(f"{total} interactions")
