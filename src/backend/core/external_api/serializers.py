"""Request and response shapes of the /external_api/ endpoints.

Contacts and groups are JSContact cards (RFC 9553), built by
``core.services.jscontact``. The card serializers below only describe them
for the OpenAPI schema. Keys that aren't Python names (``@type``, vendor
properties with their configurable prefix) are added in ``get_fields``.
"""

import uuid

from drf_spectacular.utils import PolymorphicProxySerializer
from rest_framework import serializers

from core.models import GrammaticalGender
from core.services import interactions, jscontact
from core.services import search as search_service

# pylint: disable=abstract-method

# JSContact card kinds to the search service's kinds.
SEARCH_KINDS = {
    jscontact.INDIVIDUAL: search_service.CONTACT,
    jscontact.GROUP: search_service.GROUP,
}


class NameComponentSerializer(serializers.Serializer):
    """One part of a name."""

    kind = serializers.ChoiceField(
        choices=["title", "given", "given2", "surname", "credential"]
    )
    value = serializers.CharField()


class NameSerializer(serializers.Serializer):
    """A JSContact Name: the displayed name and its parts."""

    full = serializers.CharField(help_text="Displayed name.")
    components = NameComponentSerializer(many=True, required=False)


class EmailAddressSerializer(serializers.Serializer):
    """A JSContact EmailAddress."""

    address = serializers.EmailField()
    contexts = serializers.DictField(
        child=serializers.BooleanField(),
        required=False,
        help_text='{"work": true} or {"private": true}.',
    )
    pref = serializers.IntegerField(
        required=False, help_text="1 on the preferred address."
    )
    label = serializers.CharField(required=False, help_text="Free-text type.")


class NicknameSerializer(serializers.Serializer):
    """A JSContact Nickname."""

    name = serializers.CharField()


class SpeakToAsSerializer(serializers.Serializer):
    """How to address the person."""

    grammaticalGender = serializers.ChoiceField(  # JSContact's name
        choices=[gender for gender in GrammaticalGender.values if gender]
    )


class OrgUnitSerializer(serializers.Serializer):
    """A unit (department) of an organization."""

    name = serializers.CharField()


class OrganizationSerializer(serializers.Serializer):
    """A JSContact Organization: company and department."""

    name = serializers.CharField(required=False)
    units = OrgUnitSerializer(many=True, required=False)


class TitleSerializer(serializers.Serializer):
    """A JSContact Title (job title)."""

    kind = serializers.ChoiceField(choices=["title"])
    name = serializers.CharField()


class MediaSerializer(serializers.Serializer):
    """A JSContact Media: the photo, as an https URL."""

    kind = serializers.ChoiceField(choices=["photo"])
    uri = serializers.URLField()


class _CardSerializer(serializers.Serializer):
    """Base of the card schemas: fields whose keys aren't Python names."""

    card_kind = ""

    def card_fields(self):
        """Fields specific to the kind of card, keyed by property name."""
        return {}

    def vendor_fields(self):
        """Vendor properties, by unprefixed name."""
        return {}

    def get_fields(self):
        fields = {
            "@type": serializers.ChoiceField(choices=["Card"]),
            "version": serializers.ChoiceField(choices=[jscontact.VERSION]),
            "uid": serializers.CharField(help_text='"urn:uuid:" and the row id.'),
            "kind": serializers.ChoiceField(choices=[self.card_kind]),
            "name": NameSerializer(),
            **self.card_fields(),
        }
        for name, field in self.vendor_fields().items():
            fields[jscontact.vendor(name)] = field
        return fields


class ContactCardSerializer(_CardSerializer):
    """A contact as a JSContact card (schema only).

    An overlay (the user's local copy of a read-only contact) is shown
    merged, under its own uid.
    """

    card_kind = jscontact.INDIVIDUAL

    def card_fields(self):
        return {
            "nicknames": serializers.DictField(
                child=NicknameSerializer(), required=False
            ),
            "speakToAs": SpeakToAsSerializer(required=False),
            "emails": serializers.DictField(
                child=EmailAddressSerializer(),
                required=False,
                help_text="By id (e1, e2...); the preferred one has pref 1.",
            ),
            "organizations": serializers.DictField(
                child=OrganizationSerializer(), required=False
            ),
            "titles": serializers.DictField(child=TitleSerializer(), required=False),
            "media": serializers.DictField(
                child=MediaSerializer(),
                required=False,
                help_text="Photos stored inline are left out to keep responses small.",
            ),
        }

    def vendor_fields(self):
        return {
            "isOrg": serializers.BooleanField(
                help_text="In the organization's address book, not the user's own."
            ),
            "source": serializers.CharField(
                allow_blank=True,
                help_text='"" or the directory the contact is synced from.',
            ),
        }


class GroupCardSerializer(_CardSerializer):
    """A group as a JSContact card of kind "group" (schema only).

    Members aren't listed (``members`` would hold all of them): their count
    is, and the group members endpoint lists them.
    """

    card_kind = jscontact.GROUP

    def card_fields(self):
        return {
            "emails": serializers.DictField(
                child=EmailAddressSerializer(),
                required=False,
                help_text="Only for an addressable group (a shared mailbox).",
            ),
        }

    def vendor_fields(self):
        return {
            "isOrg": serializers.BooleanField(
                help_text="Shared with the whole organization, not the user's own."
            ),
            "source": serializers.CharField(allow_blank=True),
            "membersCount": serializers.IntegerField(
                help_text="Visible members; the members endpoint lists them."
            ),
        }


def card(kind, row):
    """One search result, as a card."""
    if kind == search_service.CONTACT:
        return jscontact.contact_card(row)
    return jscontact.group_card(row)


class SearchResponseSerializer(serializers.Serializer):
    """Response of the search endpoint (schema only)."""

    results = PolymorphicProxySerializer(
        component_name="Card",
        serializers={
            jscontact.INDIVIDUAL: ContactCardSerializer,
            jscontact.GROUP: GroupCardSerializer,
        },
        resource_type_field_name="kind",
        many=True,
    )


class SearchQuerySerializer(serializers.Serializer):
    """Query parameters of the search endpoint."""

    q = serializers.CharField(
        required=False,
        default="",
        allow_blank=True,
        max_length=200,
        trim_whitespace=True,
        help_text="Empty: the contacts and groups the user interacts with most.",
    )
    kind = serializers.CharField(
        required=False,
        default=",".join(jscontact.KINDS),
        help_text='Comma-separated card kinds to return: "individual", "group".',
    )
    limit = serializers.IntegerField(
        required=False, default=10, min_value=1, max_value=50
    )

    def validate_kind(self, value):
        """A non-empty subset of the card kinds, as the search's kinds."""
        kinds = [kind.strip() for kind in value.split(",") if kind.strip()]
        unknown = sorted(set(kinds) - set(jscontact.KINDS))
        if unknown or not kinds:
            raise serializers.ValidationError(
                f"Use a comma-separated list of: {', '.join(jscontact.KINDS)}."
            )
        return tuple(dict.fromkeys(SEARCH_KINDS[kind] for kind in kinds))


# Items per interactions call: an email to a whole department, reported by a
# service channel, can name hundreds of people.
MAX_TOKEN_ITEMS = 50
MAX_CHANNEL_ITEMS = 500


class InteractionItemSerializer(serializers.Serializer):
    """A card the user interacted with, by uid or by address.

    ``{"uid": …}`` for a card picked from the search, contact or group alike
    (their ids never collide); ``{"email": …}`` when the app only knows the
    address: Contacts finds the contact or group behind it, if any.
    """

    uid = serializers.CharField(
        required=False, help_text='A card uid ("urn:uuid:…") from the search.'
    )
    email = serializers.EmailField(required=False)

    def validate(self, attrs):
        """Exactly one of uid and email, as the interactions service's item."""
        if bool(attrs.get("uid")) == bool(attrs.get("email")):
            raise serializers.ValidationError("Give either a uid or an email.")
        if attrs.get("email"):
            return {"type": interactions.EMAIL, "value": attrs["email"]}
        try:
            row_id = uuid.UUID(jscontact.parse_uid(attrs["uid"]))
        except ValueError as exc:
            raise serializers.ValidationError(
                {"uid": "Not a card uid from this service."}
            ) from exc
        return {"type": interactions.UID, "id": row_id}


class InteractionsRequestSerializer(serializers.Serializer):
    """Body of the interactions endpoint: one event and the people in it."""

    kind = serializers.CharField(
        required=False,
        default="share",
        help_text="What happened: share, email_sent, email_received, "
        "event_invite, mention... (see INTERACTION_KINDS). Each app may "
        "report its own kinds only.",
    )
    items = InteractionItemSerializer(
        many=True,
        min_length=1,
        max_length=MAX_CHANNEL_ITEMS,
        help_text=f"Up to {MAX_TOKEN_ITEMS} with a user token, "
        f"{MAX_CHANNEL_ITEMS} with a service channel.",
    )
    on_behalf_of = serializers.CharField(
        required=False,
        help_text="Service channels only: the email or sub of the user the "
        "interaction is for.",
    )

    def validate_items(self, value):
        """No more items than the caller may send."""
        limit = self.context.get("max_items", MAX_TOKEN_ITEMS)
        if len(value) > limit:
            raise serializers.ValidationError(f"Send at most {limit} items.")
        return value

    def validate_kind(self, value):
        """A kind this instance knows."""
        if value not in interactions.kinds():
            raise serializers.ValidationError(
                f"Unknown kind. Use one of: {', '.join(interactions.kinds())}."
            )
        return value


class AddContactSerializer(serializers.Serializer):
    """Body of the "Add as contact" endpoint."""

    email = serializers.EmailField()
    name = serializers.CharField(
        required=False,
        default="",
        allow_blank=True,
        max_length=255,
        help_text="Shown name; the address is shown when empty.",
    )


class GroupMembersResponseSerializer(serializers.Serializer):
    """Response of the group members endpoint (schema only)."""

    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    group = GroupCardSerializer()
    results = ContactCardSerializer(many=True)
