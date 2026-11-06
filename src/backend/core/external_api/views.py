"""Endpoints other Suite apps call on behalf of their user.

Authenticated with the user's own access token, introspected at the
authorization server (``ResourceServerAuthentication``), and limited to the
apps listed in ``OIDC_RS_ALLOWED_AUDIENCES``. What a user sees here is what
they see in Contacts: the same visibility rules apply.
"""

import logging

from django.conf import settings
from django.db import OperationalError, connection, transaction
from django.db.models import Exists, OuterRef, Q

from drf_spectacular.utils import OpenApiParameter, extend_schema
from lasuite.oidc_resource_server.authentication import ResourceServerAuthentication
from rest_framework import exceptions, response, status, views

from core import models
from core.api.permissions import IsEntitledToAccess, channel_scope
from core.api.viewsets import Pagination
from core.authentication.channel import (
    ChannelApiKeyAuthentication,
    resolve_acting_user,
)
from core.enums import ChannelScope, Scope
from core.services import interactions as interactions_service
from core.services import jscontact
from core.services import search as search_service

from . import serializers
from .permissions import ResourceServerClientPermission
from .throttles import ExternalRateThrottle

logger = logging.getLogger(__name__)


class SearchTimeout(exceptions.APIException):
    """The search took longer than its database budget."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The search took too long. Try a longer query."
    default_code = "search_timeout"


class ExternalAPIView(views.APIView):
    """Base of the /external_api/ endpoints."""

    authentication_classes = [ResourceServerAuthentication]
    permission_classes = [ResourceServerClientPermission, IsEntitledToAccess]
    throttle_classes = [ExternalRateThrottle]


class SearchView(ExternalAPIView):
    """Search the user's contacts and groups, ranked for a share picker."""

    throttle_scope = "external_search"

    @extend_schema(
        operation_id="external_search",
        parameters=[serializers.SearchQuerySerializer],
        responses=serializers.SearchResponseSerializer,
        tags=["external"],
    )
    def get(self, request):
        """Contacts and groups matching ``q``, as JSContact cards, best first.

        Ranked by how often and how recently the user picked them (see
        ``/interactions/``), then the user's favorites, then names or
        addresses where a word starts with ``q``, then those containing it.
        Only contacts with an email address are returned.
        """
        params = serializers.SearchQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    # A limit on each SQL query of this search, not on the
                    # request as a whole, so one bad plan can't pile up.
                    # SET LOCAL ends with the transaction: other requests
                    # on this connection keep no limit.
                    cursor.execute(
                        "SET LOCAL statement_timeout = %s",
                        [settings.SEARCH_STATEMENT_TIMEOUT_MS],
                    )
                results = search_service.search(
                    request.user,
                    params.validated_data["q"],
                    kinds=params.validated_data["kind"],
                    limit=params.validated_data["limit"],
                )
                data = [serializers.card(kind, row) for kind, row in results]
        except OperationalError as exc:
            # statement_timeout cancels the query (QueryCanceled).
            logger.warning("Search timed out for user %s", request.user.pk)
            raise SearchTimeout() from exc
        return response.Response({"results": data})


class InteractionsView(ExternalAPIView):
    """Record what the user interacted with, to rank it higher next time.

    Also open to service channels with the ``interactions:write`` scope, for
    events that happen without a user session (an email received): they
    name the user with ``on_behalf_of``.
    """

    authentication_classes = [ResourceServerAuthentication, ChannelApiKeyAuthentication]
    permission_classes = [
        (ResourceServerClientPermission & IsEntitledToAccess)
        | channel_scope(ChannelScope.INTERACTIONS_WRITE.value)
    ]
    throttle_scope = "external_interactions"

    @extend_schema(
        operation_id="external_interactions",
        request=serializers.InteractionsRequestSerializer,
        responses={204: None},
        tags=["external"],
    )
    def post(self, request):
        """Record one event (a share, an email...) with the people in it.

        Called after the event happened, not on every click. Each kind
        weighs what INTERACTION_KINDS says, less per person past
        INTERACTION_MASS_THRESHOLD people. Items the user can't see are
        ignored, so the answer never tells whether an id exists.
        """
        is_channel = isinstance(request.auth, models.Channel)
        body = serializers.InteractionsRequestSerializer(
            data=request.data,
            context={
                "max_items": serializers.MAX_CHANNEL_ITEMS
                if is_channel
                else serializers.MAX_TOKEN_ITEMS
            },
        )
        body.is_valid(raise_exception=True)
        kind = body.validated_data["kind"]
        if kind not in self.allowed_kinds(request):
            raise exceptions.PermissionDenied(
                f"This application may not report {kind!r} interactions."
            )
        # The token's user, or the channel's on_behalf_of user.
        user = resolve_acting_user(request)
        interactions_service.record(user, kind, body.validated_data["items"])
        return response.Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def allowed_kinds(request):
        """The kinds the calling app or channel may report (EXTERNAL_API)."""
        config = settings.EXTERNAL_API.get("interactions", {})
        if isinstance(request.auth, models.Channel):
            return config.get("channel_kinds", [])
        audience = getattr(request, "resource_server_token_audience", None)
        return config.get("kinds", {}).get(audience, [])


class AddContactView(ExternalAPIView):
    """Add an address to the user's contacts ("Add as contact" buttons)."""

    throttle_scope = "external_contacts"

    @extend_schema(
        operation_id="external_add_contact",
        request=serializers.AddContactSerializer,
        responses={
            200: serializers.ContactCardSerializer,
            201: serializers.ContactCardSerializer,
        },
        tags=["external"],
    )
    def post(self, request):
        """Create a personal contact for an address, or return the known one.

        ``201`` with the new contact, or ``200`` with the contact the user
        already sees under that address (their own or the organization's):
        calling twice is harmless. A new contact gets a one-time boost in
        the ranking (the ``added_as_contact`` kind).
        """
        body = serializers.AddContactSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        user = request.user
        email = body.validated_data["email"].strip()
        if interactions_service.is_own_address(user, email):
            raise exceptions.ValidationError({"email": "This is your own address."})

        with transaction.atomic():
            # One add at a time per user: a double click can't make two.
            models.User.objects.select_for_update().filter(pk=user.pk).first()
            existing = interactions_service.find_contact(user, email)
            if existing is not None:
                return response.Response(jscontact.contact_card(existing))
            contact = models.Contact(
                owner=user,
                organization_id=user.organization_id,
                scope=Scope.PERSONAL,
                full_name=body.validated_data["name"].strip(),
                emails=[{"type": "", "value": email}],
                created_by=user,
                updated_by=user,
            )
            contact.save()
            if "added_as_contact" in settings.INTERACTION_KINDS:
                models.Interaction.record(
                    user,
                    contact=contact,
                    weight=interactions_service.weight("added_as_contact"),
                )
        return response.Response(
            jscontact.contact_card(contact), status=status.HTTP_201_CREATED
        )


class GroupMembersPagination(Pagination):
    """Members page, with the group itself next to the results."""

    page_size = 100

    def __init__(self, group):
        super().__init__()
        self.group = group

    def get_paginated_response(self, data):
        return response.Response(
            {
                "count": self.page.paginator.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "group": self.group,
                "results": data,
            }
        )


class GroupMembersView(ExternalAPIView):
    """The members of a group, for apps that invite people one by one."""

    throttle_scope = "external_group_members"

    @extend_schema(
        operation_id="external_group_members",
        parameters=[
            OpenApiParameter("page", int, required=False),
            OpenApiParameter(
                "page_size", int, required=False, description="Up to MAX_PAGE_SIZE."
            ),
        ],
        responses=serializers.GroupMembersResponseSerializer,
        tags=["external"],
    )
    def get(self, request, group_id):
        """Visible members as JSContact cards, alphabetical, with the group.

        A member the user has a local copy of (an overlay) is listed as that
        copy. Members without an address are listed too (a card without
        ``emails``), so the app can tell the user who can't be invited.
        """
        user = request.user
        groups = search_service.with_members_count(
            models.ContactGroup.objects.visible_to(user).filter(pk=group_id), user
        )
        group = groups.first()
        if group is None:
            raise exceptions.NotFound("Group not found.")

        memberships = models.ContactGroupMembership.objects.filter(group=group)
        members = (
            models.Contact.objects.visible_to(user)
            .filter(deleted_at__isnull=True)
            .filter(
                Exists(memberships.filter(contact_id=OuterRef("pk")))
                # The user's overlay of a member: memberships are stored
                # on the row it overlays.
                | (
                    Q(scope=Scope.PERSONAL, owner=user)
                    & Exists(memberships.filter(contact_id=OuterRef("overlay_of_id")))
                )
            )
            .exclude(overlays__owner=user)
            .select_related("overlay_of")
            .order_by("sort_key", "pk")
        )
        paginator = GroupMembersPagination(jscontact.group_card(group))
        page = paginator.paginate_queryset(members, request, view=self)
        return paginator.get_paginated_response(
            [jscontact.contact_card(member) for member in page]
        )
