"""Time the people & group search on benchmark data (see seed_benchmark).

Calls ``core.services.search.search`` directly, as the benchmark user, for a
fixed list of common and rare queries: no HTTP and no authentication, only
what Contacts owns. Reports p50 / p95 per query and overall, against the
50 ms target of docs/resource_server.md ("Performance").
"""

import statistics
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from core import models
from core.services.search import search

from .seed_benchmark import BENCHMARK_EMAIL

TARGET_MS = 50

QUERIES = {
    "empty (top picks)": "",
    "1 letter": "m",
    "2 letters": "ma",
    "common prefix": "mar",
    "common name": "martin",
    "two words": "jean dup",
    "inside a word": "ichel",
    "rare token": "fontaine1a2",
    "no match": "zzzqqq",
    "phone digits": "0612",
    "group word": "service",
}


class _QueryCounter:
    """``connection.execute_wrapper`` counting the SQL queries run."""

    def __init__(self):
        self.count = 0

    # The signature is Django's (connection.execute_wrapper).
    def __call__(self, execute, sql, params, many, context):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self.count += 1
        return execute(sql, params, many, context)


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


class Command(BaseCommand):
    """Run the search benchmark."""

    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--runs", type=int, default=30)
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--email", default=BENCHMARK_EMAIL)

    def handle(self, *args, **options):
        try:
            user = models.User.objects.get(email=options["email"])
        except models.User.DoesNotExist as exc:
            raise CommandError(
                f"No user {options['email']}: run seed_benchmark first."
            ) from exc

        counts = (
            models.Contact.objects.count(),
            models.Contact.objects.filter(organization=user.organization).count(),
            models.Interaction.objects.count(),
        )
        self.stdout.write(
            f"{counts[0]} contacts, {counts[1]} in the user's organization, "
            f"{counts[2]} interactions; {options['runs']} runs per query\n"
        )
        self.stdout.write(
            f"{'query':<20} {'results':>7} {'SQL':>4} {'p50 ms':>8} {'p95 ms':>8}"
        )

        everything = []
        for label, text in QUERIES.items():
            timings, sql_count, found = [], 0, 0
            for run in range(options["runs"] + 1):
                counter = _QueryCounter()
                with transaction.atomic(), connection.execute_wrapper(counter):
                    started = time.perf_counter()
                    results = search(user, text, limit=options["limit"])
                    elapsed = (time.perf_counter() - started) * 1000
                if run == 0:
                    continue  # warm-up
                timings.append(elapsed)
                sql_count, found = counter.count, len(results)
            everything += timings
            p95 = _percentile(timings, 0.95)
            flag = "" if p95 < TARGET_MS else "  ← over target"
            self.stdout.write(
                f"{label:<20} {found:>7} {sql_count:>4} "
                f"{statistics.median(timings):>8.1f} {p95:>8.1f}{flag}"
            )

        p95 = _percentile(everything, 0.95)
        verdict = "OK" if p95 < TARGET_MS else "OVER TARGET"
        self.stdout.write(
            f"\nAll queries: p50 {statistics.median(everything):.1f} ms, "
            f"p95 {p95:.1f} ms (target < {TARGET_MS} ms): {verdict}"
        )
