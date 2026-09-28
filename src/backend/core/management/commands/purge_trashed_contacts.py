"""Management command purging the contacts corbeille.

Permanently deletes contacts that have been in the trash for more than
``TRASHBIN_CUTOFF_DAYS`` days. Schedule it daily (cron).
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Contact


class Command(BaseCommand):
    """Hard-delete contacts trashed longer than the retention policy."""

    help = "Permanently delete contacts trashed for more than TRASHBIN_CUTOFF_DAYS."

    def handle(self, *args, **options):
        """Run the purge."""
        cutoff = timezone.now() - timedelta(days=int(settings.TRASHBIN_CUTOFF_DAYS))
        deleted, _ = Contact.objects.filter(deleted_at__lt=cutoff).delete()
        self.stdout.write(self.style.SUCCESS(f"Purged {deleted} contact(s)."))
