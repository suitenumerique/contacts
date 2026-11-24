"""
Declare and configure the models for the contacts core application
"""
# pylint: disable=too-many-lines

import datetime
import hashlib
import secrets
import uuid
from logging import getLogger

from django.conf import settings
from django.contrib.auth import models as auth_models
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.postgres.indexes import GinIndex
from django.core import validators
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.functions import Extract, Lower, Power, Upper
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from encrypted_fields.fields import EncryptedJSONField

from core.enums import (
    CHANNEL_TYPE_SCOPES,
    ChannelScope,
    ChannelScopeLevel,
    ChannelType,
    Scope,
)
from core.phones import phone_search_forms
from core.utils import build_search_text
from core.validators import validate_photo

logger = getLogger(__name__)


class DuplicateEmailError(Exception):
    """Raised when an email is already associated with a pre-existing user."""

    def __init__(self, message=None, email=None):
        """Set message and email to describe the exception."""
        self.message = message
        self.email = email
        super().__init__(self.message)


class BaseModel(models.Model):
    """
    Serves as an abstract base model for other models, ensuring that records are validated
    before saving as Django doesn't do it by default.

    Includes fields common to all models: a UUID primary key and creation/update timestamps.
    """

    id = models.UUIDField(
        verbose_name="id",
        help_text="primary key for the record as UUID",
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    created_at = models.DateTimeField(
        verbose_name="created on",
        help_text="date and time at which a record was created",
        auto_now_add=True,
        editable=False,
    )
    updated_at = models.DateTimeField(
        verbose_name="updated on",
        help_text="date and time at which a record was last updated",
        auto_now=True,
        editable=False,
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        """Call `full_clean` before saving."""
        self.full_clean()
        super().save(*args, **kwargs)


class Organization(BaseModel):
    """Organization model, populated from OIDC claims and entitlements.

    Every user belongs to exactly one organization, determined by their
    email domain (default) or a configurable OIDC claim (e.g. ``siret``
    via the ``OIDC_USERINFO_ORGANIZATION_CLAIM`` setting). Orgs are
    created automatically on first login.
    """

    name = models.CharField(max_length=200, blank=True, default="")
    external_id = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        help_text="Organization identifier from OIDC claim or email domain.",
    )

    class Meta:
        db_table = "contacts_organization"
        verbose_name = "organization"
        verbose_name_plural = "organizations"

    def __str__(self):
        return self.name or self.external_id


class UserManager(auth_models.UserManager):
    """Custom manager for User model with additional methods."""

    def get_user_by_sub_or_email(self, sub, email):
        """Fetch existing user by sub or email."""
        try:
            return self.get(sub=sub)
        except self.model.DoesNotExist as err:
            if not email:
                return None

            if settings.OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION:
                try:
                    return self.get(email=email)
                except self.model.DoesNotExist:
                    pass
            elif (
                self.filter(email=email).exists()
                and not settings.OIDC_ALLOW_DUPLICATE_EMAILS
            ):
                raise DuplicateEmailError(
                    "We couldn't find a user with this sub but the email is already "
                    "associated with a registered user."
                ) from err
        return None


class User(AbstractBaseUser, BaseModel, auth_models.PermissionsMixin):
    """User model to work with OIDC only authentication."""

    sub_validator = validators.RegexValidator(
        regex=r"^[\w.@+-:]+\Z",
        message=(
            "Enter a valid sub. This value may contain only letters, "
            "numbers, and @/./+/-/_/: characters."
        ),
    )

    sub = models.CharField(
        "sub",
        help_text=(
            "Required. 255 characters or fewer."
            " Letters, numbers, and @/./+/-/_/: characters only."
        ),
        max_length=255,
        unique=True,
        validators=[sub_validator],
        blank=True,
        null=True,
    )

    full_name = models.CharField("full name", max_length=100, null=True, blank=True)

    email = models.EmailField(
        "identity email address", blank=True, null=True, db_index=True
    )

    # Unlike the "email" field which stores the email coming from the OIDC token, this field
    # stores the email used by staff users to login to the admin site
    admin_email = models.EmailField(
        "admin email address", unique=True, blank=True, null=True
    )

    language = models.CharField(
        max_length=10,
        choices=settings.LANGUAGES,
        default=None,
        verbose_name="language",
        help_text="The language in which the user wants to see the interface.",
        null=True,
        blank=True,
    )
    is_staff = models.BooleanField(
        "staff status",
        default=False,
        help_text="Whether the user can log into this admin site.",
    )
    is_active = models.BooleanField(
        "active",
        default=True,
        help_text=(
            "Whether this user should be treated as active. "
            "Unselect this instead of deleting accounts."
        ),
    )

    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="members",
        help_text="The organization this user belongs to.",
    )

    claims = models.JSONField(
        blank=True,
        default=dict,
        help_text="Claims from the OIDC token.",
    )

    objects = UserManager()

    USERNAME_FIELD = "admin_email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "contacts_user"
        verbose_name = "user"
        verbose_name_plural = "users"
        indexes = [
            # Serves the case-insensitive ``email__iexact`` lookups
            # (channel on_behalf_of, CardDAV Basic Auth username).
            models.Index(Upper("email"), name="contacts_user_email_upper"),
        ]

    def __str__(self):
        return self.email or self.admin_email or str(self.id)


def _normalize_email(value):
    """Lowercase and strip an email address for indexing/matching."""
    return (value or "").strip().lower()


# Structured name parts, in display order (vCard N minus the prefix).
NAME_PARTS = ("given_name", "middle_name", "family_name", "name_suffix")


def compose_name(*parts):
    """Join name parts with single spaces, skipping the empty ones."""
    return " ".join(part.strip() for part in parts if part and part.strip())


class Civility(models.TextChoices):
    """Form of address shown before a contact's name."""

    MR = "mr", "M."
    MRS = "mrs", "Mme"
    UNSPECIFIED = "", "Ne pas préciser"


def _scope_constraint(name):
    """A row is personal with an owner, or its organization's without one."""
    return models.CheckConstraint(
        name=name,
        condition=(
            models.Q(scope=Scope.PERSONAL.value, owner__isnull=False)
            | models.Q(scope=Scope.ORGANIZATION.value, owner__isnull=True)
        ),
    )


def _scope_field():
    return models.CharField(
        max_length=16,
        choices=[(scope.value, scope.value) for scope in Scope],
        # Filled from ``owner`` in clean() when left empty.
        blank=True,
        default="",
        db_index=True,
    )


def _author_field(help_text):
    return models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=help_text,
    )


class ScopedQuerySet(models.QuerySet):
    """Rows scoped personal/organization (contacts and groups)."""

    def visible_to(self, user):
        """The rows *user* may see: their own, and their organization's.

        The one place that decides it. Trashed rows included (callers
        filter them).
        """
        return self.filter(
            models.Q(scope=Scope.PERSONAL.value, owner=user)
            | models.Q(scope=Scope.ORGANIZATION.value),
            organization_id=user.organization_id,
        )


class ContactQuerySet(ScopedQuerySet):
    """Contacts, detaching the overlays of the rows they delete."""

    def delete(self):
        """Delete the rows, detaching their overlays first (see Contact.delete)."""
        for contact in self.filter(overlays__isnull=False).distinct():
            contact.detach_overlays()
        return super().delete()


class Contact(BaseModel):
    """A contact card.

    ``scope`` says whose card it is:

    - **Personal** (``owner`` set): the owner's address book, writable by
      them through the API/frontend.
    - **Organization** (``owner`` NULL): the organization's shared address
      book, visible to all its members and edited by its administrators.

    And, across both:

    - **Synced contacts** (``source`` set): provisioned by an external
      source; unique on ``(organization, source, external_id)``. Read-only.
    - **Overlays** (``overlay_of`` set, personal): a user's local
      field-level edits on top of a contact they cannot edit (see
      ``overlay_of``).

    Multi-valued vCard properties (emails, phones, addresses, urls) are
    stored as JSONB lists of ``{"type": ..., "value": ...}`` objects, in
    vCard property order. Properties we don't model explicitly survive
    round-trips in ``vcard_extra``.
    """

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="contacts",
        help_text="Organization this contact is scoped to.",
    )
    scope = _scope_field()
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="contacts",
        help_text="Owner of a personal contact; NULL for an organization contact.",
    )
    created_by = _author_field("User who created the contact, when known.")
    updated_by = _author_field("User who last edited the contact, when known.")

    # Names follow vCard: ``full_name`` is FN, the name as displayed, and
    # the other parts are the optional N components, plus NICKNAME. Only a
    # name is required, in whatever parts the user knows (see
    # ContactSerializer.validate).
    civility = models.CharField(
        max_length=8, choices=Civility.choices, blank=True, default=""
    )
    full_name = models.CharField(blank=True, default="")
    name_prefix = models.CharField(
        blank=True,
        default="",
        help_text="Title before the name (Dr, Pr, Me), civility aside.",
    )
    given_name = models.CharField(blank=True, default="")
    middle_name = models.CharField(blank=True, default="")
    family_name = models.CharField(blank=True, default="")
    name_suffix = models.CharField(
        blank=True,
        default="",
        help_text="Suffix after the name (Jr., III).",
    )
    nickname = models.CharField(blank=True, default="")

    emails = models.JSONField(
        default=list,
        blank=True,
        help_text='List of {"type": "work|home|other", "value": "a@example.com"}.',
    )
    phones = models.JSONField(
        default=list,
        blank=True,
        help_text='List of {"type": "work|home|cell|other", "value": "+33..."}.',
    )
    addresses = models.JSONField(
        default=list,
        blank=True,
        help_text='List of {"type": ..., "value": "postal address as text"}.',
    )
    urls = models.JSONField(
        default=list,
        blank=True,
        help_text='List of {"type": ..., "value": "https://..."}.',
    )

    company = models.CharField(blank=True, default="")
    department = models.CharField(
        blank=True,
        default="",
        help_text="Direction / service within the organization.",
    )
    job_title = models.CharField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    photo = models.TextField(
        blank=True,
        default="",
        validators=[validate_photo],
        help_text="Avatar as a base64 image data: URI or an https URL.",
    )

    labels = models.ManyToManyField(
        "ContactLabel",
        related_name="contacts",
        blank=True,
    )

    vcard_extra = models.JSONField(
        default=dict,
        blank=True,
        help_text="Unmodeled vCard properties, preserved for round-tripping.",
    )

    # Per user: org-wide rows are shared, and starring one needs no overlay.
    favorited_by = models.ManyToManyField(
        User,
        related_name="favorite_contacts",
        blank=True,
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Soft-deletion timestamp (corbeille). Trashed contacts are "
            "restorable until purged after TRASHBIN_CUTOFF_DAYS."
        ),
    )
    # Provenance for rows provisioned from an external source. Empty for
    # user-created rows. A non-empty source marks the row read-only.
    source = models.CharField(max_length=64, blank=True, default="")
    external_id = models.CharField(max_length=255, blank=True, default="")

    # Per-user local overlay on top of a synced or organization contact:
    # ``overlay_of`` points at the parent, ``overridden_fields`` lists the
    # fields the user changed. Every other field is read live from the
    # parent, so source and administrator edits keep flowing through.
    # Overlays are personal (``owner`` set, ``source`` empty) and never
    # pushed back to the parent. Groups are never overlaid (only persons).
    overlay_of = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="overlays",
        help_text="Read-only parent contact this overlay locally edits.",
    )
    overridden_fields = models.JSONField(
        default=list,
        blank=True,
        help_text="Names of fields edited locally; others come from the parent.",
    )

    # Denormalized search fields, maintained in save().
    search_text = models.TextField(
        blank=True,
        default="",
        editable=False,
        help_text="Concatenated searchable text (names, emails, phones, company).",
    )
    sort_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        editable=False,
        help_text="Lowercased name used for alphabetical ordering.",
    )

    objects = ContactQuerySet.as_manager()

    class Meta:
        db_table = "contacts_contact"
        verbose_name = "contact"
        verbose_name_plural = "contacts"
        ordering = ["sort_key", "created_at"]
        constraints = [
            _scope_constraint("contact_scope_matches_owner"),
            models.UniqueConstraint(
                fields=["organization", "source", "external_id"],
                condition=~models.Q(source=""),
                name="unique_synced_contact_source",
            ),
            models.UniqueConstraint(
                fields=["owner", "overlay_of"],
                condition=models.Q(overlay_of__isnull=False),
                name="unique_overlay_per_owner_parent",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "owner", "sort_key"]),
            GinIndex(
                name="contacts_contact_search_trgm",
                fields=["search_text"],
                opclasses=["gin_trgm_ops"],
            ),
        ]

    # Card fields a user may override through a local overlay (everything
    # else on the row is provenance/bookkeeping, not user-facing content).
    OVERLAYABLE_FIELDS = (
        "civility",
        "full_name",
        "name_prefix",
        "given_name",
        "middle_name",
        "family_name",
        "name_suffix",
        "nickname",
        "emails",
        "phones",
        "addresses",
        "urls",
        "company",
        "department",
        "job_title",
        "notes",
        "photo",
    )
    TYPED_LIST_FIELDS = frozenset({"emails", "phones", "addresses", "urls"})

    def __str__(self):
        return self.display_name

    @property
    def is_org_contact(self):
        """Whether the contact is in its organization's shared address book."""
        return self.scope == Scope.ORGANIZATION

    def is_writable(self, user):
        """Whether *user* may edit this row (fields, trash, deletion).

        The one place that decides it. Synced rows are read-only; a
        personal row (the user's own overlays included) is its owner's; an
        organization row is writable by the organization's administrators
        (``is_admin`` entitlement).
        """
        # pylint: disable-next=import-outside-toplevel
        from core.entitlements import user_is_admin  # noqa: PLC0415

        if user is None or getattr(user, "pk", None) is None or self.source:
            return False
        if self.scope == Scope.PERSONAL:
            return self.owner_id == user.id
        return self.organization_id == user.organization_id and user_is_admin(user)

    def delete(self, *args, **kwargs):
        """Delete the row, detaching its overlays first.

        An overlay holds a user's own edits on this contact: once it is
        gone, the user keeps the card they were seeing as a personal one
        rather than losing it with the row.
        """
        self.detach_overlays()
        return super().delete(*args, **kwargs)

    def share_with_organization(self, by):
        """Copy this personal contact into its organization's address book.

        Everything is copied but the notes and the labels, which stay
        private. The owner's groups, star and Interaction rows move to the
        shared contact (they are stored on canonical rows). This row then
        shows the shared one: it is deleted, or kept as the owner's overlay
        when it holds notes or labels. Returns the shared contact.
        """
        shared = Contact(
            scope=Scope.ORGANIZATION,
            organization_id=self.organization_id,
            created_by=by,
            updated_by=by,
            vcard_extra=self.vcard_extra,
            **{
                field: getattr(self, field)
                for field in self.OVERLAYABLE_FIELDS
                if field != "notes"
            },
        )
        shared.save()
        # Saved one by one: the groups' cards change with their member.
        for membership in self.memberships.all():
            membership.contact = shared
            membership.save()
        if self.favorited_by.filter(pk=self.owner_id).exists():
            shared.favorited_by.add(self.owner_id)
            self.favorited_by.remove(self.owner_id)
        # Before the delete below would cascade them away. Left here, they
        # would sit on an overlay, where search reads no rank. The shared
        # row is new, so no unique (user, contact) pair can clash.
        self.interactions.update(contact=shared)

        if not (self.notes or self.labels.exists()):
            self.delete()
            return shared
        self.overlay_of = shared
        values = self.diff_overlay(
            {field: getattr(self, field) for field in self.OVERLAYABLE_FIELDS}
        )
        for field, value in values.items():
            setattr(self, field, value)
        self.vcard_extra = {}
        self.updated_by = by
        self.save()
        return shared

    def detach_overlays(self):
        """Turn the overlays of this row into standalone personal contacts.

        Each one takes the merged values it was showing, and what was
        stored on this canonical row for its owner: their personal groups'
        memberships, their star and their Interaction row.
        """
        memberships = list(
            self.memberships.filter(group__scope=Scope.PERSONAL).values_list(
                "group_id", "group__owner_id"
            )
        )
        starred = set(self.favorited_by.values_list("pk", flat=True))
        for overlay in self.overlays.all():
            overlay.overlay_of = self
            values = {
                field: overlay.merged_value(field) for field in self.OVERLAYABLE_FIELDS
            }
            for field, value in values.items():
                setattr(overlay, field, value)
            overlay.vcard_extra = self.vcard_extra
            overlay.overlay_of = None
            overlay.overridden_fields = []
            overlay.save()
            ContactGroupMembership.objects.bulk_create(
                [
                    ContactGroupMembership(group_id=group_id, contact=overlay)
                    for group_id, owner_id in memberships
                    if owner_id == overlay.owner_id
                ],
                ignore_conflicts=True,
            )
            if overlay.owner_id in starred:
                overlay.favorited_by.add(overlay.owner_id)
            # The overlay is now its owner's canonical row. It only holds an
            # Interaction of its own if one was written past record().
            if not overlay.interactions.exists():
                self.interactions.filter(user_id=overlay.owner_id).update(
                    contact=overlay
                )

    def merged_value(self, field):
        """Value of *field* in this row's presented (merged) view.

        For an overlay, fields listed in ``overridden_fields`` come from the
        overlay itself and everything else is read live from the parent; for
        a normal row it is simply the field's own value.
        """
        if self.overlay_of_id and field not in (self.overridden_fields or []):
            return getattr(self.overlay_of, field)
        return getattr(self, field)

    @classmethod
    def _normalize_typed_list(cls, value):
        return [
            {"type": item.get("type") or "", "value": item.get("value") or ""}
            for item in (value or [])
            if isinstance(item, dict)
        ]

    def diff_overlay(self, values):
        """Turn a write of *values* on this overlay into stored values.

        An overlayable field equal to the parent's value is un-overridden
        (its own value cleared so the card reads the live source value);
        one that differs becomes a local override. Non-overlayable fields
        pass through. Returns a new dict including ``overridden_fields``.
        """
        parent = self.overlay_of
        overridden = set(self.overridden_fields or [])
        result = dict(values)
        for field in self.OVERLAYABLE_FIELDS:
            if field not in values:
                continue
            new, current = values[field], getattr(parent, field)
            if field in self.TYPED_LIST_FIELDS:
                new = self._normalize_typed_list(new)
                current = self._normalize_typed_list(current)
            if new == current:
                overridden.discard(field)
                result[field] = [] if field in self.TYPED_LIST_FIELDS else ""
            else:
                overridden.add(field)
        result["overridden_fields"] = sorted(overridden)
        return result

    @property
    def composed_name(self):
        """The name its structured parts spell out (merged for overlays)."""
        return compose_name(*(self.merged_value(f) for f in NAME_PARTS))

    @property
    def display_name(self):
        """Best available human-readable name (merged for overlays)."""
        full_name = self.merged_value("full_name")
        if full_name:
            return full_name
        if composed := self.composed_name:
            return composed
        if self.email_values:
            return self.email_values[0]
        return str(self.pk)

    @property
    def email_values(self):
        """Normalized list of this contact's email addresses (merged)."""
        emails = self.merged_value("emails") or []
        return [
            _normalize_email(e.get("value"))
            for e in emails  # pylint: disable=not-an-iterable
            if isinstance(e, dict) and e.get("value")
        ]

    def _validate_overlay(self):
        """Enforce the invariants of a local overlay row."""
        if self.source:
            raise ValidationError({"overlay_of": "An overlay cannot itself be synced."})
        if self.owner_id is None:
            raise ValidationError({"overlay_of": "An overlay must have an owner."})
        parent = self.overlay_of
        if parent.overlay_of_id is not None:
            raise ValidationError({"overlay_of": "Cannot overlay an overlay."})
        if not (parent.source or parent.scope == Scope.ORGANIZATION):
            raise ValidationError(
                {"overlay_of": "Only synced or organization contacts can be overlaid."}
            )
        if parent.organization_id != self.organization_id:
            raise ValidationError(
                {"overlay_of": "Overlay must share the parent's organization."}
            )
        unknown = set(self.overridden_fields or []) - set(self.OVERLAYABLE_FIELDS)
        if unknown:
            raise ValidationError(
                {"overridden_fields": f"Unknown fields: {sorted(unknown)}"}
            )

    def clean(self):
        """Validate JSON list fields and refresh denormalized search fields."""
        if not self.scope:
            self.scope = Scope.PERSONAL if self.owner_id else Scope.ORGANIZATION
        for field in ("emails", "phones", "addresses", "urls"):
            value = getattr(self, field)
            if not isinstance(value, list):
                raise ValidationError({field: "Must be a list."})
            for item in value:  # pylint: disable=not-an-iterable
                if not isinstance(item, dict) or "value" not in item:
                    raise ValidationError(
                        {field: 'Each entry must be an object with a "value" key.'}
                    )

        if self.overlay_of_id is not None:
            self._validate_overlay()

        self.search_text, self.sort_key = self.compute_search_fields()

    def compute_search_fields(self):
        """Return ``(search_text, sort_key)`` from the merged card.

        ``search_text`` is lowercased and accent-folded so searches can use
        a plain ``contains`` covered by the trigram index (see
        ``core.utils.build_search_text``).
        """
        phones = self.merged_value("phones") or []
        parts = [
            self.merged_value("full_name"),
            *(self.merged_value(f) for f in NAME_PARTS),
            self.merged_value("nickname"),
            self.merged_value("company"),
            self.merged_value("department"),
            *self.email_values,
            *(
                form
                for phone in phones  # pylint: disable=not-an-iterable
                if isinstance(phone, dict)
                for form in phone_search_forms(phone.get("value"))
            ),
        ]
        search_text = build_search_text(*parts)
        # Filed under the family name when there is one, like a paper
        # directory; otherwise under the displayed name.
        family_name = self.merged_value("family_name")
        if family_name:
            sort_name = compose_name(
                family_name,
                self.merged_value("given_name"),
                self.merged_value("middle_name"),
            )
        else:
            sort_name = self.display_name
        return search_text, sort_name.lower()[:255]


# Predefined labels seeded for every user (name -> badge color).
DEFAULT_LABELS = [
    ("Élu", "#B34000"),
    ("Agent", "#0063CB"),
    ("Prestataire", "#695240"),
    ("Partenaire", "#006A6F"),
    ("Association", "#6E445A"),
]


class ContactLabel(BaseModel):
    """A user-managed label ("libellé"), used as both status and filter.

    Every user starts with the predefined set (DEFAULT_LABELS, seeded when
    the user is created) and can create/rename/delete their own.
    """

    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="contact_labels",
    )
    name = models.CharField(max_length=100)
    color = models.CharField(
        max_length=7,
        default="#0063CB",
        validators=[
            validators.RegexValidator(regex=r"^#[0-9A-Fa-f]{6}$"),
        ],
    )

    class Meta:
        db_table = "contacts_contactlabel"
        verbose_name = "contact label"
        verbose_name_plural = "contact labels"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"], name="unique_label_owner_name"
            ),
        ]

    def __str__(self):
        return self.name

    @classmethod
    def for_names(cls, user, names):
        """*user*'s labels called *names*, created when missing.

        Matched case-insensitively, so "amis" from a phone finds "Amis".
        """
        names = [name.strip()[:100] for name in names if name.strip()]
        existing = {
            label.name.casefold(): label
            for label in cls.objects.filter(owner=user).order_by("created_at")
        }
        labels = []
        for name in names:
            label = existing.get(name.casefold())
            if label is None:
                label, _ = cls.objects.get_or_create(owner=user, name=name)
                existing[name.casefold()] = label
            if label not in labels:
                labels.append(label)
        return labels

    @classmethod
    def ensure_defaults(cls, user):
        """Seed the predefined labels for *user*, keeping existing ones."""
        cls.objects.bulk_create(
            [cls(owner=user, name=name, color=color) for name, color in DEFAULT_LABELS],
            ignore_conflicts=True,
        )


class ContactGroup(BaseModel):
    """A named set of contacts.

    Like contacts, groups are personal or their organization's (``scope``).
    Organization groups are created by administrators, or provisioned by an
    external source, optionally with an ``email`` when the group is
    addressable. A non-empty ``source`` marks the group read-only. Unlike
    contacts, groups are never overlaid.

    Memberships are stored on canonical rows: a member the user has
    overlaid stays a member through its parent.
    """

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="contact_groups",
    )
    scope = _scope_field()
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="contact_groups",
        help_text="Owner of a personal group; NULL for an organization group.",
    )
    created_by = _author_field("User who created the group, when known.")
    updated_by = _author_field("User who last edited the group, when known.")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    email = models.EmailField(
        blank=True,
        default="",
        help_text="Address of addressable groups (e.g. a shared mailbox).",
    )
    # Provenance for groups provisioned from an external source; empty for
    # user-created groups.
    source = models.CharField(max_length=64, blank=True, default="")
    external_id = models.CharField(max_length=255, blank=True, default="")
    # Denormalized for search (name, email, description), maintained in
    # clean(); same format as Contact.search_text.
    search_text = models.TextField(
        blank=True,
        default="",
        editable=False,
        help_text="Concatenated searchable text (name, email, description).",
    )

    members = models.ManyToManyField(
        Contact,
        through="ContactGroupMembership",
        related_name="groups",
        blank=True,
    )
    vcard_extra = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Unmodeled properties of the group's vCard (its client UID, "
            "members not known yet), preserved for round-tripping."
        ),
    )
    # Per user rather than a flag on the row: org-wide groups are shared.
    favorited_by = models.ManyToManyField(
        User,
        related_name="favorite_groups",
        blank=True,
    )

    objects = ScopedQuerySet.as_manager()

    class Meta:
        db_table = "contacts_contactgroup"
        verbose_name = "contact group"
        verbose_name_plural = "contact groups"
        ordering = ["name"]
        constraints = [
            _scope_constraint("group_scope_matches_owner"),
            models.UniqueConstraint(
                fields=["organization", "source", "external_id"],
                condition=~models.Q(source=""),
                name="unique_synced_group_source",
            ),
            # Two groups sharing a name are indistinguishable in the
            # sidebar: unique among a user's own groups, and among the
            # organization's own. Synced organization groups are left out:
            # their source owns its naming.
            models.UniqueConstraint(
                "owner",
                Lower("name"),
                condition=models.Q(owner__isnull=False),
                name="unique_personal_group_name",
            ),
            models.UniqueConstraint(
                "organization",
                Lower("name"),
                condition=models.Q(scope=Scope.ORGANIZATION.value, source=""),
                name="unique_organization_group_name",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "owner", "name"]),
            GinIndex(
                name="contacts_group_search_trgm",
                fields=["search_text"],
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def __str__(self):
        return self.name

    @classmethod
    def free_name(cls, user, name, exclude=None, scope=Scope.PERSONAL):
        """*name*, numbered when another group of the same list has it.

        Group names are unique regardless of case among *user*'s own groups,
        and among their organization's. For writers that cannot ask the
        user for another name (a phone, a file).
        """
        name = " ".join(name.split())[:255]
        if scope == Scope.ORGANIZATION:
            taken = cls.objects.filter(
                scope=Scope.ORGANIZATION, organization_id=user.organization_id
            )
        else:
            taken = cls.objects.filter(owner=user)
        if exclude is not None:
            taken = taken.exclude(pk=exclude.pk)
        candidate, number = name, 1
        while taken.filter(name__iexact=candidate).exists():
            number += 1
            suffix = f" ({number})"
            candidate = f"{name[: 255 - len(suffix)]}{suffix}"
        return candidate

    @property
    def is_org_group(self):
        """Whether the group is shared with the whole organization."""
        return self.scope == Scope.ORGANIZATION

    def clean(self):
        """Fill ``scope`` from ``owner`` and refresh ``search_text``."""
        if not self.scope:
            self.scope = Scope.PERSONAL if self.owner_id else Scope.ORGANIZATION
        self.search_text = build_search_text(self.name, self.email, self.description)

    def is_writable(self, user):
        """Whether *user* may edit this group (name, members, deletion).

        The one place that decides it. Synced groups are read-only; a
        personal group is its owner's; an organization group is writable by
        the organization's administrators (``is_admin`` entitlement).
        """
        # pylint: disable-next=import-outside-toplevel
        from core.entitlements import user_is_admin  # noqa: PLC0415

        if user is None or getattr(user, "pk", None) is None or self.source:
            return False
        if self.scope == Scope.PERSONAL:
            return self.owner_id == user.id
        return self.organization_id == user.organization_id and user_is_admin(user)


class ContactGroupMembership(BaseModel):
    """Membership of a contact in a group (with timestamps)."""

    group = models.ForeignKey(
        ContactGroup, on_delete=models.CASCADE, related_name="memberships"
    )
    contact = models.ForeignKey(
        Contact, on_delete=models.CASCADE, related_name="memberships"
    )

    class Meta:
        db_table = "contacts_contactgroupmembership"
        constraints = [
            models.UniqueConstraint(
                fields=["group", "contact"], name="unique_group_contact"
            ),
        ]

    def __str__(self):
        return f"{self.contact_id} in {self.group_id}"


class Channel(BaseModel):
    """Integration channel for external service access to contacts.

    Follows the suitenumerique/messages Channel pattern. ``api_key``
    channels authenticate REST calls with two headers:

        X-Channel-Id: <uuid>     (public, identifies which channel)
        X-API-Key: <raw secret>  (the shared secret, hashed at rest)

    ``carddav`` channels authenticate CardDAV clients over Basic Auth (see
    ``carddav.auth``).

    Configuration is split between ``settings`` (public: scopes, expiry)
    and ``encrypted_settings`` (the SHA-256 hashes of the active API keys,
    in ``api_key_hashes``).
    """

    name = models.CharField(
        max_length=255,
        help_text="Human-readable name for this channel.",
    )

    type = models.CharField(
        max_length=255,
        help_text="Type of channel: api_key (REST) or carddav.",
        default=ChannelType.API_KEY.value,
    )

    scope_level = models.CharField(
        max_length=20,
        default=ChannelScopeLevel.USER.value,
        db_index=True,
        help_text="Access scope: global (instance-wide), organization, or user.",
    )

    user = models.ForeignKey(
        "User",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="channels",
        help_text="Target user, set only for scope_level=user.",
    )

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="channels",
        help_text=(
            "Target organization (scope_level=organization); the user's "
            "organization for scope_level=user; empty for global channels."
        ),
    )

    is_active = models.BooleanField(default=True)

    settings = models.JSONField(
        "settings",
        default=dict,
        blank=True,
        help_text="Channel-specific configuration settings (e.g. scopes).",
    )

    encrypted_settings = EncryptedJSONField(
        "encrypted settings",
        default=dict,
        blank=True,
        help_text="Encrypted channel settings (e.g. api_key_hashes).",
    )

    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "contacts_channel"
        verbose_name = "channel"
        verbose_name_plural = "channels"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                name="channel_scope_level_targets",
                condition=(
                    models.Q(
                        scope_level=ChannelScopeLevel.GLOBAL.value,
                        user__isnull=True,
                        organization__isnull=True,
                    )
                    | models.Q(
                        scope_level=ChannelScopeLevel.USER.value, user__isnull=False
                    )
                    | models.Q(
                        scope_level=ChannelScopeLevel.ORGANIZATION.value,
                        organization__isnull=False,
                        user__isnull=True,
                    )
                ),
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def scopes(self):
        """Get the scopes list from settings."""
        if not isinstance(self.settings, dict):
            return []
        return self.settings.get("scopes", [])

    @scopes.setter
    def scopes(self, value):
        """Set the scopes in settings."""
        self.settings["scopes"] = value

    @staticmethod
    def validate_scopes(channel_type, scopes):
        """Check *scopes* against *channel_type*; raise ValidationError."""
        if channel_type not in CHANNEL_TYPE_SCOPES:
            raise ValidationError({"type": f"Unknown channel type: {channel_type}"})
        if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
            raise ValidationError({"scopes": "Scopes must be a list of strings."})
        if not scopes:
            raise ValidationError({"scopes": "At least one scope is required."})
        unknown = sorted(set(scopes) - {s.value for s in ChannelScope})
        if unknown:
            raise ValidationError({"scopes": f"Invalid scopes: {', '.join(unknown)}"})
        if not set(scopes) <= CHANNEL_TYPE_SCOPES[channel_type]:
            allowed = ", ".join(sorted(CHANNEL_TYPE_SCOPES[channel_type]))
            raise ValidationError(
                {"scopes": f"{channel_type} channels only accept: {allowed}."}
            )

    def clean(self):
        """Validate scope_level targets, type and scopes."""
        level = self.scope_level
        if level not in {s.value for s in ChannelScopeLevel}:
            raise ValidationError({"scope_level": f"Unknown scope level: {level}"})
        if level == ChannelScopeLevel.USER and not self.user_id:
            raise ValidationError({"user": "User is required for scope_level=user."})
        if level != ChannelScopeLevel.USER and self.user_id:
            raise ValidationError(
                {"user": "Only scope_level=user channels have a user."}
            )
        if level == ChannelScopeLevel.ORGANIZATION and not self.organization_id:
            raise ValidationError(
                {
                    "organization": "Organization is required for scope_level=organization."
                }
            )
        if level == ChannelScopeLevel.GLOBAL and self.organization_id:
            raise ValidationError(
                {"organization": "Global channels have no organization."}
            )
        if not isinstance(self.settings, dict):
            raise ValidationError({"settings": "Settings must be an object."})
        self.validate_scopes(self.type, self.settings.get("scopes", []))

    def generate_api_key(self):
        """Generate a new API key, store its hash, and return the raw key.

        The raw key is shown exactly once (at creation/rotation). Only its
        SHA-256 hash is persisted. Several hashes may coexist to allow
        zero-downtime rotation.
        """
        raw_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        hashes = list(self.encrypted_settings.get("api_key_hashes") or [])
        hashes.append(key_hash)
        self.encrypted_settings = {
            **self.encrypted_settings,
            "api_key_hashes": hashes,
        }
        return raw_key

    def rotate_api_key(self):
        """Replace every stored key with a fresh one, save, return it raw."""
        self.encrypted_settings = {**self.encrypted_settings, "api_key_hashes": []}
        raw_key = self.generate_api_key()
        self.save(update_fields=["encrypted_settings", "updated_at"])
        return raw_key

    def carddav_password(self, raw_key):
        """The Basic Auth password of a carddav channel, None otherwise.

        Clients have one password box, so the fixed-width channel id and
        the key travel as a single string (see ``carddav.auth``).
        """
        if self.type != ChannelType.CARDDAV or not raw_key:
            return None
        return f"{self.pk.hex}{raw_key}"

    @property
    def is_expired(self):
        """Whether this channel's optional ``expires_at`` has passed.

        A value that cannot be parsed counts as expired: settings are
        edited by hand in the admin, and a typo must not yield a key
        that never expires.
        """
        raw = (self.settings or {}).get("expires_at")
        if not raw:
            return False
        try:
            expires_at = parse_datetime(str(raw))
        except ValueError:
            return True
        if expires_at is None:
            return True
        if timezone.is_naive(expires_at):
            expires_at = timezone.make_aware(expires_at, datetime.timezone.utc)
        return expires_at < timezone.now()

    def verify_api_key(self, raw_key):
        """Check that *raw_key* matches one of the stored hashes."""
        if not raw_key:
            return False
        provided = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        stored_hashes = self.encrypted_settings.get("api_key_hashes") or []
        # Iterate every stored hash without early exit so the timing is
        # constant with respect to *which* slot matched.
        matched = False
        for stored in stored_hashes:
            if isinstance(stored, str) and secrets.compare_digest(stored, provided):
                matched = True
        return matched

    def mark_used(self):
        """Throttled update of last_used_at (at most every 5 minutes)."""
        now = timezone.now()
        if self.last_used_at and (now - self.last_used_at).total_seconds() < 300:
            return
        self.last_used_at = now
        # Direct queryset update: skips full_clean and updated_at churn.
        Channel.objects.filter(pk=self.pk).update(last_used_at=now)


class InteractionQuerySet(models.QuerySet):
    """Interactions, with their rank decayed to now."""

    def with_rank(self, now=None):
        """Annotate ``rank``: the stored score decayed to *now*.

        ``score × 0.5 ^ (seconds since last pick / half-life in seconds)``,
        computed in SQL so rows can be ordered by it.
        """
        now = now or timezone.now()
        half_life = settings.INTERACTION_HALF_LIFE_DAYS * 86400
        age = models.Value(now.timestamp()) - Extract("last_interaction_at", "epoch")
        return self.annotate(
            rank=models.ExpressionWrapper(
                models.F("score")
                * Power(models.Value(0.5), age / models.Value(float(half_life))),
                output_field=models.FloatField(),
            )
        )


class Interaction(BaseModel):
    """How often, and how recently, a user interacted with a contact or group.

    Feeds the ranking of the people and groups suggested to other Suite
    apps (``/external_api/v1.0/search/``). An interaction is a share, an
    email sent or received, an invitation... each kind with its weight
    (``INTERACTION_KINDS``). ``score`` loses half its value every
    ``INTERACTION_HALF_LIFE_DAYS``: each interaction adds its weight on top
    of the previous score decayed to that day, so a regular contact of this
    month outranks one picked often long ago. ``count`` is the plain
    lifetime total.

    A pick of an overlay is stored on the row it overlays (like group
    memberships), so the history survives dropping the overlay.
    """

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="interactions"
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="interactions",
    )
    group = models.ForeignKey(
        ContactGroup,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="interactions",
    )
    count = models.PositiveIntegerField(default=0, help_text="Lifetime picks.")
    score = models.FloatField(
        default=0, help_text="Picks decayed to last_interaction_at."
    )
    last_interaction_at = models.DateTimeField()

    objects = InteractionQuerySet.as_manager()

    class Meta:
        db_table = "contacts_interaction"
        verbose_name = "interaction"
        verbose_name_plural = "interactions"
        constraints = [
            models.CheckConstraint(
                name="interaction_one_target",
                condition=(
                    models.Q(contact__isnull=False, group__isnull=True)
                    | models.Q(contact__isnull=True, group__isnull=False)
                ),
            ),
            models.UniqueConstraint(
                fields=["user", "contact"],
                condition=models.Q(contact__isnull=False),
                name="unique_interaction_user_contact",
            ),
            models.UniqueConstraint(
                fields=["user", "group"],
                condition=models.Q(group__isnull=False),
                name="unique_interaction_user_group",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-last_interaction_at"]),
        ]

    def __str__(self):
        return f"{self.user_id} → {self.contact_id or self.group_id} ({self.count})"

    @staticmethod
    def decayed(score, since, now):
        """*score*, last bumped at *since*, as it stands at *now*."""
        half_life = settings.INTERACTION_HALF_LIFE_DAYS * 86400
        elapsed = max((now - since).total_seconds(), 0)
        return score * 0.5 ** (elapsed / half_life)

    @classmethod
    def record(cls, user, *, contact=None, group=None, weight=1.0, now=None):  # pylint: disable=too-many-arguments
        """Count one interaction of *user* with *contact* or *group*.

        *weight* is what it adds to the score (see INTERACTION_KINDS). The
        row is locked while it is bumped, so two interactions landing at
        the same time both count.
        """
        now = now or timezone.now()
        if contact is not None and contact.overlay_of_id:
            contact = contact.overlay_of
        with transaction.atomic():
            interaction, _created = cls.objects.select_for_update().get_or_create(
                user=user,
                contact=contact,
                group=group,
                defaults={"last_interaction_at": now},
            )
            score = (
                cls.decayed(interaction.score, interaction.last_interaction_at, now)
                + weight
            )
            # A plain update: full_clean would re-validate every constraint.
            cls.objects.filter(pk=interaction.pk).update(
                score=score,
                count=models.F("count") + 1,
                last_interaction_at=now,
                updated_at=now,
            )
