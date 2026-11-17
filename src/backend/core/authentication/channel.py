"""DRF authentication classes for the contacts API."""

import logging

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from core import models
from core.entitlements import EntitlementsUnavailableError, get_user_entitlements
from core.enums import ChannelScopeLevel, ChannelType

logger = logging.getLogger(__name__)


class ChannelApiKeyAuthentication(BaseAuthentication):
    """Authenticate as an api_key Channel via X-Channel-Id + X-API-Key.

    Mirrors the suitenumerique/messages contract:

        X-Channel-Id: <uuid>     (public, identifies which channel)
        X-API-Key: <raw secret>  (the shared secret, hashed at rest)

    On success ``request.user`` is set to ``AnonymousUser`` (there is no
    associated session user) and ``request.auth`` is set to the
    authenticated ``Channel`` instance. Views must combine this with
    ``HasChannelScope`` permissions and call ``resolve_acting_user`` to
    turn the channel + ``on_behalf_of`` parameter into a User.
    """

    def authenticate(self, request):
        channel_id = request.headers.get("X-Channel-Id")
        api_key = request.headers.get("X-API-Key")

        # Missing either header: this auth scheme does not apply; let DRF
        # try the next class in authentication_classes.
        if not channel_id or not api_key:
            return None

        try:
            channel = models.Channel.objects.select_related("user", "organization").get(
                pk=channel_id, type=ChannelType.API_KEY, is_active=True
            )
        except (models.Channel.DoesNotExist, ValueError, DjangoValidationError) as exc:
            # ValueError / ValidationError handle malformed UUIDs.
            raise AuthenticationFailed("Invalid channel or API key.") from exc

        if not channel.verify_api_key(api_key):
            raise AuthenticationFailed("Invalid channel or API key.")

        if channel.is_expired:
            raise AuthenticationFailed("API key has expired.")

        channel.mark_used()
        return (AnonymousUser(), channel)

    def authenticate_header(self, request):
        # DRF takes the challenge of the first authentication class. Without
        # one, an anonymous request gets a 403 instead of a 401.
        return "X-API-Key"


def _check_entitled(user):
    """Refuse a user the entitlements backend does not let into the app.

    Fail-closed when the backend is unreachable, like ``IsEntitledToAccess``.
    """
    try:
        entitlements = get_user_entitlements(user)
    except EntitlementsUnavailableError as exc:
        logger.warning("Entitlements unavailable, denying channel user %s", user.pk)
        raise PermissionDenied("Entitlements unavailable.") from exc
    if not entitlements.get("can_access", False):
        raise PermissionDenied("User is not entitled to access this app.")


def resolve_acting_user(request):
    """Return the User a request acts for, session- or channel-authenticated.

    - Session/OIDC requests act as ``request.user``.
    - Channel requests act as the channel's bound user (scope_level=user),
      who must be entitled to the app, or as the user named by the
      ``on_behalf_of`` parameter (email or sub, looked up in that order),
      constrained to the channel's organization when
      scope_level=organization. Only active users resolve, and an email
      matching several active users is rejected.

    Raises PermissionDenied when the acting user cannot be resolved:
    channel calls without a resolvable user are always rejected.
    """
    channel = request.auth if isinstance(request.auth, models.Channel) else None
    if channel is None:
        if request.user and request.user.is_authenticated:
            return request.user
        raise PermissionDenied("Authentication required.")

    if channel.scope_level == ChannelScopeLevel.USER:
        if channel.user is None:
            raise PermissionDenied("Channel has no bound user.")
        if not channel.user.is_active:
            # Same rule as the on_behalf_of path below: only active users
            # resolve, so deactivating an account revokes its channels too.
            raise PermissionDenied("Channel user is inactive.")
        # The user's own credential: losing access to the app revokes it,
        # as for CardDAV passwords.
        _check_entitled(channel.user)
        return channel.user

    on_behalf_of = request.query_params.get("on_behalf_of") or (
        request.data.get("on_behalf_of") if isinstance(request.data, dict) else None
    )
    if not on_behalf_of:
        raise PermissionDenied(
            "on_behalf_of is required for organization/global channels."
        )

    active_users = models.User.objects.filter(is_active=True)
    email_matches = list(active_users.filter(email__iexact=on_behalf_of)[:2])
    if len(email_matches) > 1:
        # User.email is not unique: refuse to pick one arbitrarily. Same
        # message as the unknown case so the response doesn't reveal
        # which emails are duplicated.
        raise PermissionDenied("Unknown on_behalf_of user.")
    user = (
        email_matches[0]
        if email_matches
        else (active_users.filter(sub=on_behalf_of).first())
    )
    if user is None:
        raise PermissionDenied("Unknown on_behalf_of user.")

    if (
        channel.scope_level == ChannelScopeLevel.ORGANIZATION
        and user.organization_id != channel.organization_id
    ):
        raise PermissionDenied("User is outside the channel's organization.")

    return user
