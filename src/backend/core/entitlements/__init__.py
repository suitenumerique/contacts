"""Entitlements service layer."""

from django.conf import settings
from django.utils.functional import SimpleLazyObject

from core.entitlements.factory import get_entitlements_backend


class EntitlementsUnavailableError(Exception):
    """Raised when the entitlements backend cannot be reached or returns an error."""


def _organization_claims(user):
    """Return the organization claim of *user* as a mapping.

    The key is the name in OIDC_USERINFO_ORGANIZATION_CLAIM. The value is
    the ``external_id`` of the organization of the user. The mapping is
    empty when the setting is empty.
    """
    claim = settings.OIDC_USERINFO_ORGANIZATION_CLAIM
    if not claim:
        return {}
    return {claim: user.organization.external_id}


def get_user_entitlements(user, user_info=None, force_refresh=False):
    """Get user entitlements, delegating to the configured backend.

    Args:
        user: The user that the entitlements are for.
        user_info: The claims to forward to the backend. The default is
            the organization claim of the stored user.
        force_refresh: If True, do not read the fresh cache entry and
            call the backend. When the call fails, the backend can still
            return its last known answer.

    Returns:
        dict: {"can_access": bool, "is_admin": bool, ...}

    Raises:
        EntitlementsUnavailableError: If the backend cannot be reached
            and no cache exists.
    """
    if user_info is None:
        # The claims are lazy: a backend that does not read them makes no query.
        user_info = SimpleLazyObject(lambda: _organization_claims(user))
    backend = get_entitlements_backend()
    return backend.get_user_entitlements(
        user.sub, user.email, user_info=user_info, force_refresh=force_refresh
    )


def user_is_admin(user):
    """Whether *user* administers their organization's shared data.

    False when no entitlements are available: the backend gives no answer
    and it has no last known answer.
    """
    try:
        return bool(get_user_entitlements(user).get("is_admin"))
    except EntitlementsUnavailableError:
        return False
