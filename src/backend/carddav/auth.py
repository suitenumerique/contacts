"""HTTP Basic Auth for CardDAV, backed by ``core.models.Channel``.

CardDAV clients (DAVx⁵, Apple Contacts, Thunderbird, Evolution) speak
Basic Auth and nothing else, so a channel is presented as a
username/password pair:

    username = the user's email address
    password = <channel_id><api_key>

``channel_id`` is the channel UUID as 32 hex characters, immediately
followed by the raw API key: the id is fixed-width, so the split is
unambiguous. The email as username lets the server answer
``current-user-principal`` without any client configuration. Keys are
checked with ``Channel.verify_api_key`` against the stored hashes.
"""

import base64
import binascii
import uuid

from core.entitlements import EntitlementsUnavailableError, get_user_entitlements
from core.enums import ChannelScope, ChannelScopeLevel, ChannelType
from core.models import Channel, User

# A UUID rendered as bare hex (no dashes).
CHANNEL_ID_LEN = 32


class AuthError(Exception):
    """Raised when a request cannot be authenticated (→ 401)."""


class ForbiddenError(Exception):
    """Raised when credentials are valid but not for this user (→ 403)."""


def _split_credentials(password):
    """Split ``<channel_id><api_key>`` into its two halves."""
    if len(password) <= CHANNEL_ID_LEN:
        return None, None
    raw_id, api_key = password[:CHANNEL_ID_LEN], password[CHANNEL_ID_LEN:]
    try:
        channel_id = uuid.UUID(hex=raw_id)
    except ValueError:
        return None, None
    return channel_id, api_key


def authenticate(request):
    """Authenticate a CardDAV request.

    Returns ``(user, channel)``. Only channel credentials are accepted: a
    browser session would skip the entitlement check and the channel
    scopes on a CSRF-exempt view, and the web app never calls CardDAV.

    Raises :class:`AuthError` when no usable credentials were presented and
    :class:`ForbiddenError` when they identify someone else.
    """
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Basic "):
        raise AuthError("Basic auth required")

    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
        email, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise AuthError("malformed Basic credentials") from exc

    channel_id, api_key = _split_credentials(password)
    if not email or channel_id is None or not api_key:
        raise AuthError("malformed CardDAV password")

    try:
        channel = Channel.objects.select_related("user", "user__organization").get(
            pk=channel_id, is_active=True, type=ChannelType.CARDDAV
        )
    except Channel.DoesNotExist as exc:
        raise AuthError("unknown channel") from exc

    if channel.is_expired or not channel.verify_api_key(api_key):
        raise AuthError("invalid credentials")

    user = _resolve_user(channel, email)
    _check_entitled(user)
    channel.mark_used()
    return user, channel


def _check_entitled(user):
    """Refuse a user the entitlements backend does not let into the app.

    Gating only the REST API would be a hole: a sync password minted
    earlier would still read and write the whole address book. Fail-closed,
    like the REST permission classes.
    """
    try:
        entitlements = get_user_entitlements(user)
    except EntitlementsUnavailableError as exc:
        raise ForbiddenError("entitlements unavailable") from exc
    if not entitlements.get("can_access", False):
        raise ForbiddenError("user is not entitled to access this app")


def _resolve_user(channel, email):
    """Resolve the acting user for *channel* given the Basic Auth *email*."""
    if channel.scope_level == ChannelScopeLevel.USER:
        if channel.user is None:
            raise ForbiddenError("channel has no user")
        if (channel.user.email or "").lower() != email.lower():
            raise ForbiddenError("email does not match channel user")
        if not channel.user.is_active:
            # Deactivating an account must revoke every credential bound to
            # it, including long-lived device sync passwords.
            raise ForbiddenError("user is inactive")
        return channel.user

    # Organization/global channels act on behalf of the named user. The
    # email is the only identifier Basic Auth gives us, and User.email is
    # not unique, so an ambiguous address must not resolve to an arbitrary
    # row. Same rule as core.authentication.channel.resolve_acting_user.
    matches = list(
        User.objects.select_related("organization").filter(
            email__iexact=email, is_active=True
        )[:2]
    )
    if len(matches) != 1:
        raise ForbiddenError("unknown user")
    user = matches[0]

    if (
        channel.scope_level == ChannelScopeLevel.ORGANIZATION
        and user.organization_id != channel.organization_id
    ):
        raise ForbiddenError("user outside channel organization")
    return user


def check_scope(channel, *, write):
    """Authorize a read or write against the channel's scopes."""
    required = ChannelScope.CARDDAV_WRITE if write else ChannelScope.CARDDAV_READ
    scopes = set(channel.scopes)
    if required.value in scopes:
        return
    # Write implies read: a sync client always PROPFINDs before it PUTs.
    if not write and ChannelScope.CARDDAV_WRITE.value in scopes:
        return
    raise ForbiddenError(f"channel lacks the {required.value} scope")
