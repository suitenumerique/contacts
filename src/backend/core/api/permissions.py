"""Permission handlers for the contacts core app."""

import logging

from rest_framework import permissions

from core.entitlements import EntitlementsUnavailableError, get_user_entitlements

logger = logging.getLogger(__name__)


class IsAuthenticated(permissions.BasePermission):
    """Allows access only to authenticated users.

    Service channels (X-Channel-Id / X-API-Key) are not users: channel
    calls must be granted through ``channel_scope`` so an API key can never
    reach an endpoint its scopes don't list.
    """

    def has_permission(self, request, view):
        # pylint: disable=import-outside-toplevel
        from core import models

        if isinstance(request.auth, models.Channel):
            return False
        return bool(request.auth) or request.user.is_authenticated


class IsSelf(IsAuthenticated):
    """Allows authenticated users to act on their own User object only."""

    def has_object_permission(self, request, view, obj):
        """Write permissions are only allowed to the user itself."""
        return obj == request.user


class IsEntitledToAccess(IsAuthenticated):
    """Allows access only to users with can_access entitlement.

    Fail-closed: denies access when the entitlements service is
    unavailable and no cached value exists.
    """

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        try:
            entitlements = get_user_entitlements(request.user)
            return entitlements.get("can_access", False)
        except EntitlementsUnavailableError:
            logger.warning(
                "Entitlements unavailable, denying access for user %s",
                request.user.pk,
            )
            return False


class HasChannelScope(permissions.BasePermission):
    """Scope-based permission for service calls authenticated as a Channel.

    Only inspects ``request.auth``, which must be a ``Channel`` (set by
    ``ChannelApiKeyAuthentication``), and checks that ``required_scope``
    is listed in the channel's ``settings["scopes"]``.

    OR it with a user permission so an endpoint accepts both session users
    and service channels:

        [IsEntitledToAccess | channel_scope("contacts:read")]
    """

    required_scope: str = ""

    def has_permission(self, request, view):
        # Local import to avoid a circular import (models import enums only).
        # pylint: disable=import-outside-toplevel
        from core import models

        channel = request.auth
        if not isinstance(channel, models.Channel):
            return False
        scopes = (
            channel.settings.get("scopes")
            if isinstance(channel.settings, dict)
            else None
        )
        if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
            return False
        return self.required_scope in scopes


def channel_scope(required_scope):
    """Return a ``HasChannelScope`` subclass with ``required_scope`` pre-bound.

    DRF's ``permission_classes`` expects classes, not instances, so a small
    subclass is built per scope (same pattern as Messages).
    """
    return type(
        f"HasChannelScope_{required_scope}",
        (HasChannelScope,),
        {"required_scope": str(required_scope)},
    )
