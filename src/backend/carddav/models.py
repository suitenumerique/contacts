"""Sync bookkeeping owned by the CardDAV package.

A sync token has to answer "what changed since you last asked?" without
ever losing a row. Deriving that from ``Contact.updated_at`` cannot: two
writes inside the same microsecond are indistinguishable from none, a
backwards clock adjustment hides everything written before it, and the
stamp is set in Python before the row commits, so rows can become visible
out of timestamp order.

So every write to a contact also records a numbered entry in the change
journal. The numbers come from a per-organization counter bumped inside
the writing transaction (:mod:`carddav.signals`), which is what makes
"everything after N" exact rather than approximate.

Nothing in ``core`` reads these tables: dropping the package drops them.
"""

from django.db import models


class SyncCounter(models.Model):
    """Per-organization change counter, the source of journal numbers.

    One row per organization rather than one global row so that writes in
    unrelated organizations never queue behind each other.
    """

    organization = models.OneToOneField(
        "core.Organization",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="carddav_sync_counter",
    )
    seq = models.BigIntegerField(default=0)

    class Meta:
        db_table = "carddav_sync_counter"
        verbose_name = "CardDAV sync counter"
        verbose_name_plural = "CardDAV sync counters"

    def __str__(self):
        return f"{self.organization_id} @ {self.seq}"


class ContactChange(models.Model):
    """The latest change number for one card: a contact or a group.

    One row per card: the journal is a "what is dirty since N" index,
    not an audit log. Its number is also the card's version, which the
    ETag is derived from. A row outlives the card it names, because a
    hard-deleted card still has to be reported as removed to clients that
    had it; ``purge_carddav_changes`` drops those tombstones later.

    ``owner`` and ``from_source`` are copied from the card for the same
    reason: once the card is gone, a delta still has to decide which
    address book the removal belongs to and whether the asking user was
    allowed to see that card at all.
    """

    id = models.BigAutoField(primary_key=True)
    organization = models.ForeignKey(
        "core.Organization",
        on_delete=models.CASCADE,
        related_name="carddav_changes",
    )
    # Not a foreign key: a tombstone has to survive the card it names.
    contact_id = models.UUIDField(
        help_text="Id of the contact, or of the group when is_group is set."
    )
    is_group = models.BooleanField(default=False)
    seq = models.BigIntegerField()
    owner = models.ForeignKey(
        "core.User",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
        help_text="Owner of the contact when it changed; NULL for org rows.",
    )
    from_source = models.BooleanField(
        default=False,
        help_text="Whether the contact was provisioned from an external source.",
    )
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        db_table = "carddav_contact_change"
        verbose_name = "CardDAV contact change"
        verbose_name_plural = "CardDAV contact changes"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "contact_id"],
                name="unique_carddav_change_per_contact",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "seq"]),
            models.Index(fields=["organization", "owner", "seq"]),
        ]

    def __str__(self):
        return f"{self.contact_id} @ {self.seq}"


class PurgeMark(models.Model):
    """Highest journal number ``purge_carddav_changes`` has dropped.

    Kept per ``(owner, from_source)``, the columns that decide which
    address book a journal row belongs to, so a purge only invalidates the
    sync tokens of the books it could have affected. A token below the
    mark of its book may be missing a removal and is refused.
    """

    id = models.BigAutoField(primary_key=True)
    organization = models.ForeignKey(
        "core.Organization",
        on_delete=models.CASCADE,
        related_name="+",
    )
    owner = models.ForeignKey(
        "core.User",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    from_source = models.BooleanField(default=False)
    seq = models.BigIntegerField()

    class Meta:
        db_table = "carddav_purge_mark"
        verbose_name = "CardDAV purge mark"
        verbose_name_plural = "CardDAV purge marks"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "owner", "from_source"],
                nulls_distinct=False,
                name="unique_carddav_purge_mark",
            )
        ]

    def __str__(self):
        return f"{self.organization_id}/{self.owner_id} @ {self.seq}"


class CardName(models.Model):
    """The resource name a client chose when it created a card.

    Clients pick the href of a new card themselves (Apple uses an
    upper-case UUID, DAVx5 the card's UID) and expect to find the card
    under that exact name. Cards without a row here are addressed as
    ``<uuid>.vcf``. ``contact_id`` holds a group's id for a group card:
    ids are UUIDs, so they do not collide.

    ``contact_id`` is not a foreign key for the same reason as in
    :class:`ContactChange`: the name is still needed to report the card
    as removed once it is gone.
    """

    id = models.BigAutoField(primary_key=True)
    owner = models.ForeignKey(
        "core.User",
        on_delete=models.CASCADE,
        related_name="+",
    )
    contact_id = models.UUIDField(unique=True)
    name = models.CharField(max_length=255)

    class Meta:
        db_table = "carddav_card_name"
        verbose_name = "CardDAV card name"
        verbose_name_plural = "CardDAV card names"
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"], name="unique_carddav_card_name"
            )
        ]

    def __str__(self):
        return self.name
