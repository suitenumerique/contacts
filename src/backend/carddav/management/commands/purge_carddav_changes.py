"""Management command trimming the CardDAV change journal.

The journal holds one row per contact, so it does not grow on its own,
except for the tombstones left behind by hard-deleted cards, which have to
outlive the contact long enough to be reported as removed.

Tombstones older than ``TRASHBIN_CUTOFF_DAYS`` are dropped. The highest
number dropped from each book is recorded as a purge mark, and any sync
token below the mark of its book is refused from then on (RFC 6578
``valid-sync-token``), so its client resyncs rather than missing the
removal. Schedule this daily, next to ``purge_trashed_contacts``.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from core.models import Contact, ContactGroup

from carddav.models import CardName, ContactChange, PurgeMark


class Command(BaseCommand):
    """Drop journal tombstones older than the corbeille retention."""

    help = "Delete CardDAV change-journal rows for long-gone contacts and groups."

    def handle(self, *args, **options):
        """Run the prune."""
        cutoff = timezone.now() - timedelta(days=int(settings.TRASHBIN_CUTOFF_DAYS))
        with transaction.atomic():
            tombstones = ContactChange.objects.filter(updated_at__lt=cutoff).exclude(
                Q(is_group=False, contact_id__in=Contact.objects.values("pk"))
                | Q(is_group=True, contact_id__in=ContactGroup.objects.values("pk"))
            )
            marks = tombstones.values("organization_id", "owner_id", "from_source")
            for mark in marks.annotate(seq=Max("seq")).order_by():
                current, _ = PurgeMark.objects.select_for_update().get_or_create(
                    organization_id=mark["organization_id"],
                    owner_id=mark["owner_id"],
                    from_source=mark["from_source"],
                    defaults={"seq": mark["seq"]},
                )
                if current.seq < mark["seq"]:
                    current.seq = mark["seq"]
                    current.save(update_fields=["seq"])
            deleted, _ = tombstones.delete()
            # A card name is kept only while something may still report it.
            CardName.objects.exclude(
                contact_id__in=Contact.objects.values("pk")
            ).exclude(contact_id__in=ContactGroup.objects.values("pk")).exclude(
                contact_id__in=ContactChange.objects.values("contact_id")
            ).delete()
        self.stdout.write(self.style.SUCCESS(f"Purged {deleted} journal row(s)."))
