"""
Declare and configure the signals for the contacts core application
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import NAME_PARTS, Contact

# Fields feeding Contact.compute_search_fields.
_SEARCH_SOURCE_FIELDS = frozenset(
    {
        "full_name",
        *NAME_PARTS,
        "nickname",
        "company",
        "department",
        "emails",
        "phones",
    }
)


@receiver(post_save, sender=Contact, dispatch_uid="core_refresh_overlay_search")
def refresh_overlay_search_fields(sender, instance, created, raw=False, **kwargs):  # pylint: disable=unused-argument
    """Recompute the search fields of the overlays of a saved contact.

    An overlay's ``search_text`` and ``sort_key`` include fields read from
    its parent, so they go stale when the parent changes. Written with
    ``QuerySet.update``, which fires no signal. Bulk updates of parents
    (``QuerySet.update``) are not covered.
    """
    if raw or created or instance.overlay_of_id is not None:
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and not _SEARCH_SOURCE_FIELDS & set(update_fields):
        return
    for overlay in instance.overlays.all():
        overlay.overlay_of = instance
        search_text, sort_key = overlay.compute_search_fields()
        if (search_text, sort_key) != (overlay.search_text, overlay.sort_key):
            Contact.objects.filter(pk=overlay.pk).update(
                search_text=search_text, sort_key=sort_key
            )
