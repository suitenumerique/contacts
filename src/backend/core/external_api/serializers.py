"""Response and request shapes of the /external_api/ endpoints.

A contact has the same shape everywhere (search results, group members) and
a group its own; ``type`` tells them apart. Both carry what a picker needs
to draw a row without branching: ``type``, ``id``, ``display_name``,
``email``, ``is_org``, ``source``.
"""

from drf_spectacular.utils import PolymorphicProxySerializer
from rest_framework import serializers

from core.enums import Scope
from core.services import interactions
from core.services.search import CONTACT, GROUP, KINDS


class EmailSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One address of a contact."""

    type = serializers.CharField(help_text='"work", "home", "other" or free text.')
    value = serializers.CharField()


def _or_none(value):
    return value or None


class ContactResultSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A contact as other apps see it.

    An overlay (the user's local copy of a read-only contact) is shown
    merged, under its own id.
    """

    type = serializers.SerializerMethodField()
    id = serializers.UUIDField()
    display_name = serializers.CharField()
    email = serializers.SerializerMethodField(
        help_text="Preferred address: the first of `emails`."
    )
    emails = serializers.SerializerMethodField()
    company = serializers.SerializerMethodField()
    job_title = serializers.SerializerMethodField()
    photo = serializers.SerializerMethodField(
        help_text="https URL of the photo. Photos stored inline are left out "
        "to keep responses small."
    )
    is_org = serializers.SerializerMethodField(
        help_text="In the organization's address book, not the user's own."
    )
    source = serializers.CharField(
        help_text='"" or the directory the contact is synced from.'
    )

    def get_type(self, contact) -> str:  # pylint: disable=unused-argument
        """Always "contact"."""
        return CONTACT

    def get_emails(self, contact) -> list[EmailSerializer]:
        """Every address, as typed."""
        return [
            {"type": item.get("type") or "", "value": item["value"]}
            for item in contact.merged_value("emails") or []
            if isinstance(item, dict) and item.get("value")
        ]

    def get_email(self, contact) -> str | None:
        """The first address."""
        emails = self.get_emails(contact)
        return emails[0]["value"] if emails else None

    def get_company(self, contact) -> str | None:
        """Company, merged for an overlay."""
        return _or_none(contact.merged_value("company"))

    def get_job_title(self, contact) -> str | None:
        """Job title, merged for an overlay."""
        return _or_none(contact.merged_value("job_title"))

    def get_photo(self, contact) -> str | None:
        """The photo when it is a URL; inline (data:) photos are left out."""
        photo = contact.merged_value("photo") or ""
        return photo if photo.startswith("https://") else None

    def get_is_org(self, contact) -> bool:
        """Whether the row is the organization's."""
        return contact.scope == Scope.ORGANIZATION


class GroupResultSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A group as other apps see it."""

    type = serializers.SerializerMethodField()
    id = serializers.UUIDField()
    display_name = serializers.CharField(source="name")
    email = serializers.SerializerMethodField(
        help_text="Set only for an addressable group (a shared mailbox)."
    )
    members_count = serializers.IntegerField()
    is_org = serializers.SerializerMethodField(
        help_text="Shared with the whole organization, not the user's own."
    )
    source = serializers.CharField()

    def get_type(self, group) -> str:  # pylint: disable=unused-argument
        """Always "group"."""
        return GROUP

    def get_email(self, group) -> str | None:
        """The group's address, if any."""
        return _or_none(group.email)

    def get_is_org(self, group) -> bool:
        """Whether the group is the organization's."""
        return group.scope == Scope.ORGANIZATION


def serialize_result(kind, row):
    """One search result."""
    if kind == CONTACT:
        return ContactResultSerializer(row).data
    return GroupResultSerializer(row).data


class SearchResponseSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Response of the search endpoint (for the schema only)."""

    results = PolymorphicProxySerializer(
        component_name="SearchResult",
        serializers={CONTACT: ContactResultSerializer, GROUP: GroupResultSerializer},
        resource_type_field_name="type",
        many=True,
    )


class SearchQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Query parameters of the search endpoint."""

    q = serializers.CharField(
        required=False,
        default="",
        allow_blank=True,
        max_length=200,
        trim_whitespace=True,
        help_text="Empty: the contacts and groups the user interacts with most.",
    )
    types = serializers.CharField(
        required=False,
        default=",".join(KINDS),
        help_text='Comma-separated kinds to return: "contact", "group".',
    )
    limit = serializers.IntegerField(
        required=False, default=10, min_value=1, max_value=50
    )

    def validate_types(self, value):
        """A non-empty subset of the known kinds."""
        kinds = [kind.strip() for kind in value.split(",") if kind.strip()]
        unknown = sorted(set(kinds) - set(KINDS))
        if unknown or not kinds:
            raise serializers.ValidationError(
                f"Use a comma-separated list of: {', '.join(KINDS)}."
            )
        return tuple(dict.fromkeys(kinds))


class InteractionItemSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One contact or group the user interacted with."""

    type = serializers.ChoiceField(choices=interactions.ITEM_TYPES)
    id = serializers.UUIDField()


class InteractionsRequestSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Body of the interactions endpoint: one event and the people in it."""

    kind = serializers.CharField(
        required=False,
        default="share",
        help_text="What happened: share, email_sent, email_received, "
        "event_invite, mention... (see INTERACTION_KINDS). Each app may "
        "report its own kinds only.",
    )
    items = InteractionItemSerializer(many=True, min_length=1, max_length=50)

    def validate_kind(self, value):
        """A kind this instance knows."""
        if value not in interactions.kinds():
            raise serializers.ValidationError(
                f"Unknown kind. Use one of: {', '.join(interactions.kinds())}."
            )
        return value


class GroupMembersResponseSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Response of the group members endpoint (for the schema only)."""

    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    group = GroupResultSerializer()
    results = ContactResultSerializer(many=True)
