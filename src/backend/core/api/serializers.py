"""Client serializers for the contacts core app."""

import re

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator, validate_email

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core import models
from core.entitlements import EntitlementsUnavailableError, get_user_entitlements
from core.enums import ChannelScope, ChannelScopeLevel, ChannelType, Scope

# Upper bound of entries in each multi-valued contact field.
MAX_TYPED_VALUES = 50

_URL_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_http_url = URLValidator(schemes=["http", "https"])


def _acting_user(context):
    """Resolve the user a request acts for (session user or on_behalf_of).

    The viewsets inject ``acting_user`` into the serializer context so
    channel/on-behalf-of reads report writability for the right user;
    fall back to ``request.user`` for plain instantiations.
    """
    if context.get("acting_user") is not None:
        return context["acting_user"]
    request = context.get("request")
    return request.user if request else None


def visible_groups(user):
    """Groups *user* may see: their own plus their organization's."""
    return models.ContactGroup.objects.visible_to(user)


class OrganizationSerializer(serializers.ModelSerializer):
    """Serialize organizations."""

    class Meta:
        model = models.Organization
        fields = ["id", "name", "external_id"]
        read_only_fields = fields


class UserSerializer(serializers.ModelSerializer):
    """Serialize users."""

    email = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = models.User
        fields = [
            "id",
            "email",
            "full_name",
            "language",
        ]
        read_only_fields = ["id", "email", "full_name"]

    def get_email(self, user) -> str | None:
        """Return OIDC email, falling back to admin_email for staff users."""
        return user.email or user.admin_email


class UserMeSerializer(UserSerializer):
    """Serialize users for me endpoint."""

    can_access = serializers.SerializerMethodField(read_only=True)
    can_admin = serializers.SerializerMethodField(read_only=True)
    organization = OrganizationSerializer(read_only=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._entitlements_cache = {}

    class Meta:
        model = models.User
        fields = [
            *UserSerializer.Meta.fields,
            "can_access",
            "can_admin",
            "organization",
        ]
        read_only_fields = [
            *UserSerializer.Meta.read_only_fields,
            "can_access",
            "can_admin",
            "organization",
        ]

    def _get_entitlements(self, user):
        """Get cached entitlements for the user, keyed by user.sub.

        Cache is per-serializer-instance (request-scoped) to avoid
        duplicate calls when both can_access and can_admin are serialized.
        """
        if user.sub not in self._entitlements_cache:
            try:
                self._entitlements_cache[user.sub] = get_user_entitlements(
                    user.sub, user.email
                )
            except EntitlementsUnavailableError:
                self._entitlements_cache[user.sub] = None
        return self._entitlements_cache[user.sub]

    def get_can_access(self, user) -> bool:
        """Check entitlements for the current user."""
        entitlements = self._get_entitlements(user)
        if entitlements is None:
            return False  # fail-closed
        return entitlements.get("can_access", False)

    def get_can_admin(self, user) -> bool:
        """Check admin entitlement for the current user."""
        entitlements = self._get_entitlements(user)
        if entitlements is None:
            return False  # fail-closed
        return entitlements.get("can_admin", False)


class TypedValueSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A {"type": ..., "value": ...} entry (emails, phones, addresses, urls)."""

    type = serializers.RegexField(
        r"^[A-Za-z0-9-]{0,32}$",
        max_length=32,
        required=False,
        allow_blank=True,
        default="",
    )
    value = serializers.CharField(max_length=1024)


def _typed_values():
    return TypedValueSerializer(many=True, required=False, max_length=MAX_TYPED_VALUES)


class ContactLabelSerializer(serializers.ModelSerializer):
    """Serialize contact labels (libellés)."""

    contacts_count = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = models.ContactLabel
        fields = ["id", "name", "color", "contacts_count"]
        read_only_fields = ["id", "contacts_count"]

    def get_contacts_count(self, label) -> int:
        """Number of non-trashed contacts wearing the label."""
        annotated = getattr(label, "contacts_count", None)
        if annotated is not None:
            return annotated
        return label.contacts.filter(deleted_at__isnull=True).count()

    def validate_name(self, value):
        """Refuse a name another of the user's labels already carries."""
        user = self.context["request"].user
        clashes = models.ContactLabel.objects.filter(owner=user, name=value)
        if self.instance is not None:
            clashes = clashes.exclude(pk=self.instance.pk)
        if clashes.exists():
            raise serializers.ValidationError(
                "You already have a label with this name."
            )
        return value

    def create(self, validated_data):
        validated_data["owner"] = self.context["request"].user
        return super().create(validated_data)


class ContactSerializer(serializers.ModelSerializer):
    """Serialize contacts (full card)."""

    emails = _typed_values()
    phones = _typed_values()
    addresses = _typed_values()
    urls = _typed_values()
    display_name = serializers.CharField(read_only=True)
    is_org_contact = serializers.BooleanField(
        default=False,
        help_text=(
            "In the organization's shared address book. Set at creation, by "
            "an administrator only; fixed afterwards."
        ),
    )
    is_writable = serializers.SerializerMethodField(read_only=True)
    is_favorite = serializers.SerializerMethodField(read_only=True)
    group_ids = serializers.SerializerMethodField(read_only=True)
    label_ids = serializers.PrimaryKeyRelatedField(
        source="labels",
        many=True,
        required=False,
        queryset=models.ContactLabel.objects.none(),
    )

    class Meta:
        model = models.Contact
        fields = [
            "id",
            "display_name",
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
            "is_favorite",
            "is_org_contact",
            "is_writable",
            "source",
            "external_id",
            "overlay_of",
            "overridden_fields",
            "group_ids",
            "label_ids",
            "created_at",
            "updated_at",
            "deleted_at",
        ]
        read_only_fields = [
            "id",
            "display_name",
            "is_favorite",
            "is_writable",
            "source",
            "external_id",
            "overlay_of",
            "overridden_fields",
            "group_ids",
            "created_at",
            "updated_at",
            "deleted_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        label_ids = self.fields.get("label_ids")
        user = _acting_user(self.context)
        if label_ids is not None and not label_ids.read_only and user is not None:
            label_ids.child_relation.queryset = models.ContactLabel.objects.filter(
                owner_id=user.pk
            )

    def get_is_writable(self, contact) -> bool:
        """Whether the acting user may edit this contact."""
        return contact.is_writable(_acting_user(self.context))

    def get_is_favorite(self, contact) -> bool:
        """Whether the acting user starred this contact or the row it overlays."""
        annotated = getattr(contact, "is_favorite", None)
        if annotated is not None:
            return annotated
        user = _acting_user(self.context)
        if user is None or user.pk is None:
            return False
        rows = (
            [contact.pk, contact.overlay_of_id]
            if contact.overlay_of_id
            else [contact.pk]
        )
        return models.Contact.favorited_by.through.objects.filter(
            contact_id__in=rows, user_id=user.pk
        ).exists()

    @extend_schema_field(serializers.ListField(child=serializers.UUIDField()))
    def get_group_ids(self, contact):
        """Groups the acting user can see that hold this contact.

        An overlay also lists its parent's groups: memberships are stored
        on the canonical row.
        """
        rows = [contact, contact.overlay_of] if contact.overlay_of_id else [contact]
        prefetched = [getattr(row, "visible_groups", None) for row in rows]
        if all(groups is not None for groups in prefetched):
            groups = {group.pk: group for batch in prefetched for group in batch}
            ordered = sorted(groups.values(), key=lambda group: group.name)
            return [str(group.pk) for group in ordered]
        user = _acting_user(self.context)
        if user is None or user.pk is None:
            return []
        return [
            str(pk)
            for pk in visible_groups(user)
            .filter(memberships__contact__in=[row.pk for row in rows])
            .order_by("name")
            .values_list("pk", flat=True)
            .distinct()
        ]

    def to_representation(self, instance):
        """Merge an overlay on top of its parent for the presented card."""
        data = super().to_representation(instance)
        if instance.overlay_of_id:
            for field in models.Contact.OVERLAYABLE_FIELDS:
                if field not in data:
                    continue
                value = instance.merged_value(field)
                if field in models.Contact.TYPED_LIST_FIELDS:
                    value = [
                        {"type": item.get("type", ""), "value": item.get("value", "")}
                        for item in (value or [])
                        if isinstance(item, dict)
                    ]
                data[field] = value
        return data

    def validate_emails(self, value):
        """Every address must be a valid email."""
        for item in value:
            try:
                validate_email(item["value"])
            except DjangoValidationError as exc:
                raise serializers.ValidationError(
                    f"Enter a valid email address: {item['value']}"
                ) from exc
        return value

    def validate_phones(self, value):
        """Phone numbers are short; anything longer is not a phone number."""
        for item in value:
            if len(item["value"]) > 64:
                raise serializers.ValidationError(
                    "Phone numbers are 64 characters max."
                )
        return value

    def validate_urls(self, value):
        """Accept http(s) URLs and scheme-less ones ("example.com").

        The frontend prefixes scheme-less values with https://, so those are
        validated as such; every other scheme (javascript:, data:, ...) is
        refused.
        """
        for item in value:
            url = item["value"].strip()
            candidate = url if _URL_SCHEME.match(url) else f"https://{url}"
            try:
                _http_url(candidate)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(f"Enter a valid URL: {url}") from exc
            item["value"] = url
        return value

    def validate(self, attrs):
        """Enforce the core-field rules (user-facing writes only).

        - a name is required: ``full_name`` or any structured part. A blank
          ``full_name`` is filled from the parts, like a vCard FN;
        - at least one of email / phone must be filled.

        On partial updates the rules apply to the merged result of the
        fields being modified, so updating one field never demands the
        whole card again, but a field cannot be blanked below the rules.
        """

        def effective(field):
            if field in attrs:
                return attrs[field]
            if self.instance is None:
                return ""
            if self.instance.overlay_of_id:
                # Unspecified fields of an overlay come from its parent.
                return self.instance.merged_value(field)
            return getattr(self.instance, field, "")

        creating = self.instance is None
        name_fields = ("full_name", *models.NAME_PARTS)
        if creating or any(field in attrs for field in name_fields):
            if not str(effective("full_name") or "").strip():
                composed = models.compose_name(
                    *(str(effective(field) or "") for field in models.NAME_PARTS)
                )
                if not composed:
                    raise serializers.ValidationError(
                        {"full_name": "A name is required."}
                    )
                attrs["full_name"] = composed[:255]  # the column's max_length
        if creating or "emails" in attrs or "phones" in attrs:
            if not (effective("emails") or effective("phones")):
                raise serializers.ValidationError(
                    "At least an email address or a phone number is required."
                )
        return self._validate_scope(attrs)

    def _validate_scope(self, attrs):
        """Keep the scope fixed, and labels off organization contacts.

        Labels are per user; on a shared row they are not supported yet.
        """
        is_org_contact = attrs.get("is_org_contact", False)
        if self.instance is not None:
            if "is_org_contact" in self.initial_data and (
                is_org_contact != self.instance.is_org_contact
            ):
                raise serializers.ValidationError(
                    {"is_org_contact": "A contact's sharing cannot be changed."}
                )
            is_org_contact = self.instance.is_org_contact
        if is_org_contact and attrs.get("labels"):
            raise serializers.ValidationError(
                {"label_ids": "An organization contact carries no labels."}
            )
        return attrs

    def create(self, validated_data):
        """A contact is the user's, or their organization's when shared."""
        request = self.context["request"]
        is_org_contact = validated_data.pop("is_org_contact", False)
        validated_data["owner"] = None if is_org_contact else request.user
        validated_data["organization"] = request.user.organization
        validated_data["created_by"] = validated_data["updated_by"] = request.user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        """Apply a write, recomputing overlay provenance when relevant."""
        validated_data.pop("is_org_contact", None)
        if instance.overlay_of_id:
            validated_data = instance.diff_overlay(validated_data)
        validated_data["updated_by"] = _acting_user(self.context)
        return super().update(instance, validated_data)


class ContactListSerializer(ContactSerializer):
    """Lighter serializer for contact lists."""

    class Meta(ContactSerializer.Meta):
        fields = [
            "id",
            "display_name",
            "civility",
            "full_name",
            "given_name",
            "family_name",
            "emails",
            "phones",
            "company",
            "department",
            "job_title",
            "photo",
            "is_favorite",
            "is_org_contact",
            "is_writable",
            "source",
            "overlay_of",
            "group_ids",
            "label_ids",
        ]
        read_only_fields = fields


class ContactGroupSerializer(serializers.ModelSerializer):
    """Serialize contact groups."""

    members_count = serializers.SerializerMethodField(read_only=True)
    is_org_group = serializers.BooleanField(
        default=False,
        help_text=(
            "Shared with the whole organization. Set at creation, by an "
            "administrator only; fixed afterwards."
        ),
    )
    is_writable = serializers.SerializerMethodField(read_only=True)
    is_favorite = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = models.ContactGroup
        fields = [
            "id",
            "name",
            "description",
            "email",
            "source",
            "members_count",
            "is_org_group",
            "is_writable",
            "is_favorite",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "email",
            "source",
            "members_count",
            "is_writable",
            "is_favorite",
            "created_at",
            "updated_at",
        ]

    def validate_name(self, value):
        """Refuse an empty name."""
        name = value.strip()
        if not name:
            raise serializers.ValidationError("A group name is required.")
        return name

    def validate(self, attrs):
        """Keep the sharing fixed, and names unique where they are listed.

        Two groups with the same name are indistinguishable in the sidebar,
        so the collision is caught here rather than left to the user:
        among the user's own groups, or among the organization's for a
        shared one. Compared case-insensitively, which is how a person
        reads them.
        """
        is_org_group = attrs.get("is_org_group", False)
        if self.instance is not None:
            if "is_org_group" in self.initial_data and (
                is_org_group != self.instance.is_org_group
            ):
                raise serializers.ValidationError(
                    {"is_org_group": "A group's sharing cannot be changed."}
                )
            is_org_group = self.instance.is_org_group
        user = _acting_user(self.context)
        if user is None or "name" not in attrs:
            return attrs
        if is_org_group:
            clashes = models.ContactGroup.objects.filter(
                scope=Scope.ORGANIZATION, organization_id=user.organization_id
            )
            message = "Your organization already has a group with this name."
        else:
            clashes = models.ContactGroup.objects.filter(owner=user)
            message = "You already have a group with this name."
        clashes = clashes.filter(name__iexact=attrs["name"])
        if self.instance is not None:
            clashes = clashes.exclude(pk=self.instance.pk)
        if clashes.exists():
            raise serializers.ValidationError({"name": [message]})
        return attrs

    def get_is_writable(self, group) -> bool:
        """Whether the acting user may edit this group."""
        return group.is_writable(_acting_user(self.context))

    def get_members_count(self, group) -> int:
        """Number of contacts in the group (annotated by the viewset)."""
        annotated = getattr(group, "members_count", None)
        if annotated is not None:
            return annotated
        return group.members.filter(deleted_at__isnull=True).count()

    def get_is_favorite(self, group) -> bool:
        """Whether the acting user starred this group (annotated by the viewset)."""
        annotated = getattr(group, "is_favorite", None)
        if annotated is not None:
            return annotated
        user = _acting_user(self.context)
        if user is None or user.pk is None:
            return False
        return group.favorited_by.filter(pk=user.pk).exists()

    def create(self, validated_data):
        """A group is the user's, or their organization's when shared."""
        request = self.context["request"]
        is_org_group = validated_data.pop("is_org_group", False)
        validated_data["owner"] = None if is_org_group else request.user
        validated_data["organization"] = request.user.organization
        validated_data["created_by"] = validated_data["updated_by"] = request.user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("is_org_group", None)
        validated_data["updated_by"] = _acting_user(self.context)
        return super().update(instance, validated_data)


def _validate_channel_scopes(channel_type, scopes):
    """Run the model's type/scope check, reported as a DRF error."""
    try:
        models.Channel.validate_scopes(channel_type, scopes)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(exc.message_dict) from exc


class ChannelSerializer(serializers.ModelSerializer):
    """Read serializer for Channel model."""

    scopes = serializers.SerializerMethodField()
    groups = serializers.SerializerMethodField()

    class Meta:
        model = models.Channel
        fields = [
            "id",
            "name",
            "type",
            "scope_level",
            "organization",
            "user",
            "scopes",
            "groups",
            "is_active",
            "settings",
            "last_used_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_scopes(self, obj) -> list[str]:
        """Get scopes from settings."""
        return obj.scopes

    def get_groups(self, obj) -> bool:
        """Whether a carddav channel also syncs the groups."""
        return bool((obj.settings or {}).get("groups"))


class ChannelCreateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Write serializer for creating a Channel."""

    name = serializers.CharField(max_length=255)
    type = serializers.ChoiceField(
        choices=[(t.value, t.value) for t in ChannelType],
        default=ChannelType.API_KEY.value,
    )
    scope_level = serializers.ChoiceField(
        choices=[(s.value, s.value) for s in ChannelScopeLevel],
        default=ChannelScopeLevel.USER.value,
    )
    scopes = serializers.ListField(
        child=serializers.ChoiceField(
            choices=[(s.value, s.value) for s in ChannelScope],
        ),
    )
    groups = serializers.BooleanField(
        default=False,
        help_text=(
            "carddav only, fixed at creation: also sync the groups, as group "
            "cards (iPhone, Mac, DAVx5 set to groups as separate vCards)."
        ),
    )

    def validate(self, attrs):
        _validate_channel_scopes(attrs.get("type"), attrs.get("scopes"))
        if attrs.get("groups") and attrs.get("type") != ChannelType.CARDDAV:
            raise serializers.ValidationError(
                {"groups": "Only a carddav channel can sync groups."}
            )
        return attrs


class ChannelWithKeySerializer(ChannelSerializer):
    """Serializer that includes the plaintext API key (only on creation)."""

    api_key = serializers.CharField(read_only=True)
    carddav_password = serializers.SerializerMethodField()

    class Meta(ChannelSerializer.Meta):
        fields = [*ChannelSerializer.Meta.fields, "api_key", "carddav_password"]

    def get_carddav_password(self, obj) -> str | None:
        """The ready-to-paste Basic Auth password; null for REST channels."""
        return obj.carddav_password(getattr(obj, "api_key", None))


class ChannelUpdateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Serializer for updating mutable channel fields.

    Expects the channel being updated in ``context["channel"]`` so a
    scope change is checked against its (immutable) type.
    """

    name = serializers.CharField(max_length=255, required=False)
    is_active = serializers.BooleanField(required=False)
    scopes = serializers.ListField(
        child=serializers.ChoiceField(
            choices=[(s.value, s.value) for s in ChannelScope],
        ),
        required=False,
    )

    def validate(self, attrs):
        if "scopes" in attrs:
            _validate_channel_scopes(self.context["channel"].type, attrs["scopes"])
        return attrs


class GroupMemberSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Payload of group member management actions."""

    contact_id = serializers.UUIDField()


class FileUploadSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Multipart payload of import endpoints."""

    file = serializers.FileField()
    scope = serializers.ChoiceField(
        choices=[(scope.value, scope.value) for scope in Scope],
        default=Scope.PERSONAL.value,
        help_text=(
            "Import into the user's address book, or into their "
            "organization's (administrators only)."
        ),
    )


class TaskQueuedSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Response of async import endpoints."""

    task_id = serializers.CharField()


class ImportErrorDetailSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Why one entry of an imported file was rejected."""

    entry = serializers.IntegerField(
        allow_null=True, help_text="1-based position; null for the whole file."
    )
    name = serializers.CharField(allow_blank=True)
    reason = serializers.ChoiceField(
        choices=[
            "unreadable",
            "no_name",
            "invalid_email",
            "no_email_or_phone",
            "failed",
        ]
    )
    value = serializers.CharField(required=False, allow_blank=True)


class ImportResultSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Summary of an import run."""

    imported = serializers.IntegerField()
    skipped = serializers.IntegerField()
    errors = serializers.IntegerField()
    total = serializers.IntegerField()
    groups = serializers.IntegerField(help_text="Groups imported from group cards.")
    error_details = ImportErrorDetailSerializer(many=True)


class TaskStatusSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Status of an async task, as polled by the frontend."""

    status = serializers.ChoiceField(
        choices=["PENDING", "PROGRESS", "SUCCESS", "FAILURE"]
    )
    result = ImportResultSerializer(allow_null=True)
    error = serializers.CharField(allow_null=True)
    progress = serializers.IntegerField(required=False, allow_null=True)
    message = serializers.CharField(required=False, allow_null=True)
    timestamp = serializers.FloatField(required=False, allow_null=True)


class ConfigSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Public settings served to the frontend by ``GET /config/``."""

    FRONTEND_THEME = serializers.CharField(allow_null=True)
    FRONTEND_MORE_LINK = serializers.CharField(allow_null=True)
    FRONTEND_TERMS_OF_SERVICE_URL = serializers.CharField(allow_null=True)
    FRONTEND_FEEDBACK_BUTTON_SHOW = serializers.BooleanField()
    FRONTEND_FEEDBACK_BUTTON_IDLE = serializers.BooleanField()
    FRONTEND_FEEDBACK_ITEMS = serializers.DictField()
    FRONTEND_FEEDBACK_MESSAGES_WIDGET_ENABLED = serializers.BooleanField()
    FRONTEND_FEEDBACK_MESSAGES_WIDGET_API_URL = serializers.CharField(allow_null=True)
    FRONTEND_FEEDBACK_MESSAGES_WIDGET_CHANNEL = serializers.CharField(allow_null=True)
    FRONTEND_FEEDBACK_MESSAGES_WIDGET_PATH = serializers.CharField(allow_null=True)
    FRONTEND_LAGAUFRE_ENABLED = serializers.BooleanField()
    FRONTEND_LAGAUFRE_WIDGET_PATH = serializers.CharField(allow_null=True)
    FRONTEND_LAGAUFRE_WIDGET_API_URL = serializers.CharField(allow_null=True)
    FRONTEND_MESSAGES_COMPOSE_URL = serializers.CharField(allow_blank=True)
    TRASHBIN_CUTOFF_DAYS = serializers.IntegerField()
    FEATURE_CARDDAV = serializers.BooleanField()
    LANGUAGES = serializers.ListField(
        child=serializers.ListField(child=serializers.CharField())
    )
    LANGUAGE_CODE = serializers.CharField()
    theme_customization = serializers.DictField()
