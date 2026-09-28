"""Channel API for managing integration API keys."""

import logging

from django.conf import settings

from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core import models
from core.api import serializers
from core.api.permissions import IsEntitledToAccess
from core.enums import ChannelScopeLevel, ChannelType

logger = logging.getLogger(__name__)


class ChannelViewSet(viewsets.GenericViewSet):
    """CRUD for the current user's own user-scoped channels.

    Endpoints:
        GET    /api/v1.0/channels/                          list
        POST   /api/v1.0/channels/                          create (returns api_key once)
        GET    /api/v1.0/channels/{id}/                     retrieve
        PATCH  /api/v1.0/channels/{id}/                     update name/scopes/is_active
        DELETE /api/v1.0/channels/{id}/                     delete
        POST   /api/v1.0/channels/{id}/regenerate-api-key/  rotate the key

    scope_level, type, user and organization are immutable after creation.
    Organization and global channels are managed through Django admin or
    the CLI only.
    """

    # A channel mints a credential that reads the address book, so it is
    # gated like the contact endpoints.
    permission_classes = [IsEntitledToAccess]
    # Only tells the schema generator the model; get_queryset scopes rows.
    queryset = models.Channel.objects.none()
    serializer_class = serializers.ChannelSerializer
    pagination_class = None

    def get_queryset(self):
        return models.Channel.objects.filter(
            user=self.request.user, scope_level=ChannelScopeLevel.USER
        ).select_related("organization", "user")

    def list(self, request):
        """List the current user's channels."""
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data)

    @extend_schema(
        request=serializers.ChannelCreateSerializer,
        responses={201: serializers.ChannelWithKeySerializer},
    )
    def create(self, request):
        """Create a new user-scoped channel and return the API key (once)."""
        create_serializer = serializers.ChannelCreateSerializer(data=request.data)
        create_serializer.is_valid(raise_exception=True)
        data = create_serializer.validated_data

        if data["scope_level"] != ChannelScopeLevel.USER:
            return Response(
                {
                    "detail": (
                        "Organization and global channels can only be created "
                        "via Django admin or CLI."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if data["type"] == ChannelType.CARDDAV and not settings.FEATURE_CARDDAV:
            return Response(
                {"detail": "CardDAV is disabled."},
                status=status.HTTP_403_FORBIDDEN,
            )

        channel = models.Channel(
            name=data["name"],
            type=data["type"],
            scope_level=ChannelScopeLevel.USER,
            user=request.user,
            organization=request.user.organization,
            settings={
                "scopes": data["scopes"],
                **({"groups": True} if data["groups"] else {}),
            },
        )
        api_key = channel.generate_api_key()
        channel.save()

        channel.api_key = api_key
        serializer = serializers.ChannelWithKeySerializer(
            channel, context={"request": request}
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):  # pylint: disable=unused-argument
        """Retrieve a channel (without API key)."""
        serializer = self.get_serializer(self.get_object())
        return Response(serializer.data)

    @extend_schema(request=serializers.ChannelUpdateSerializer)
    def partial_update(self, request, pk=None):  # pylint: disable=unused-argument
        """Update mutable channel fields (name, is_active, scopes)."""
        channel = self.get_object()

        update_serializer = serializers.ChannelUpdateSerializer(
            data=request.data, context={"channel": channel}
        )
        update_serializer.is_valid(raise_exception=True)
        data = update_serializer.validated_data

        update_fields = ["updated_at"]
        if "name" in data:
            channel.name = data["name"]
            update_fields.append("name")
        if "is_active" in data:
            channel.is_active = data["is_active"]
            update_fields.append("is_active")
        if "scopes" in data:
            channel.scopes = data["scopes"]
            update_fields.append("settings")

        channel.save(update_fields=update_fields)

        serializer = self.get_serializer(channel)
        return Response(serializer.data)

    def destroy(self, request, pk=None):  # pylint: disable=unused-argument
        """Delete a channel."""
        self.get_object().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=None, responses=serializers.ChannelWithKeySerializer)
    @action(detail=True, methods=["post"], url_path="regenerate-api-key")
    def regenerate_api_key(self, request, pk=None):  # pylint: disable=unused-argument
        """Replace the channel's API key with a fresh one; old keys stop working."""
        channel = self.get_object()
        channel.api_key = channel.rotate_api_key()
        serializer = serializers.ChannelWithKeySerializer(
            channel, context={"request": request}
        )
        return Response(serializer.data)
