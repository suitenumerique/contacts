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
from django.utils import timezone

from drf_spectacular.utils import OpenApiParameter, extend_schema
from lasuite.oidc_resource_server.authentication import ResourceServerAuthentication
from rest_framework import exceptions, response, status, views

from core import models
from core.api.permissions import IsEntitledToAccess
from core.api.viewsets import Pagination
from core.enums import Scope
from core.services import search as search_service

from . import serializers
from .permissions import ResourceServerClientPermission

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
        """Contacts and groups matching ``q``, best first.

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
                    kinds=params.validated_data["types"],
                    limit=params.validated_data["limit"],
                )
                data = [
                    serializers.serialize_result(kind, row) for kind, row in results
                ]
        except OperationalError as exc:
            # statement_timeout cancels the query (QueryCanceled).
            logger.warning("Search timed out for user %s", request.user.pk)
            raise SearchTimeout() from exc
        return response.Response({"results": data})


class InteractionsView(ExternalAPIView):
    """Record what the user picked, to rank it higher next time."""

    throttle_scope = "external_interactions"

    @extend_schema(
        operation_id="external_interactions",
        request=serializers.InteractionsRequestSerializer,
        responses={204: None},
        tags=["external"],
    )
    def post(self, request):
        """Count one pick of each item, after the share succeeded.

        Items the user can't see are ignored, so the answer never tells
        whether an id exists.
        """
        body = serializers.InteractionsRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        user = request.user
        wanted = {search_service.CONTACT: set(), search_service.GROUP: set()}
        for item in body.validated_data["items"]:
            wanted[item["type"]].add(item["id"])

        contacts = (
            models.Contact.objects.visible_to(user)
            .filter(pk__in=wanted[search_service.CONTACT], deleted_at__isnull=True)
            .select_related("overlay_of")
        )
        groups = models.ContactGroup.objects.visible_to(user).filter(
            pk__in=wanted[search_service.GROUP]
        )
        # One timestamp for the whole share: the people picked together get
        # the same rank, so the name orders them, not microseconds apart.
        now = timezone.now()
        for contact in contacts:
            models.Interaction.record(user, contact=contact, now=now)
        for group in groups:
            models.Interaction.record(user, group=group, now=now)
        return response.Response(status=status.HTTP_204_NO_CONTENT)


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
        """Visible members, alphabetical, with members that have no email.

        A member the user has a local copy of (an overlay) is listed as that
        copy. Members without an address keep ``"email": null``, so the app
        can tell the user who can't be invited.
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
        paginator = GroupMembersPagination(
            serializers.GroupResultSerializer(group).data
        )
        page = paginator.paginate_queryset(members, request, view=self)
        data = serializers.ContactResultSerializer(page, many=True).data
        return paginator.get_paginated_response(data)
