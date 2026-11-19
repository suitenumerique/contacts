"""Entitlements service layer."""

from core.entitlements.factory import get_entitlements_backend


class EntitlementsUnavailableError(Exception):
    """Raised when the entitlements backend cannot be reached or returns an error."""


def get_user_entitlements(user, force_refresh=False):
    """Get user entitlements, delegating to the configured backend.

    The backend gets the claims that the login stored on the user (see
    OIDC_STORE_CLAIMS).

    Args:
        force_refresh: If True, call the backend and do not read the fresh
            cache entry. When the call fails, the backend can still return
            its last known answer.

    Returns:
        dict: {"can_access": bool, "is_admin": bool, ...}

    Raises:
        EntitlementsUnavailableError: If the backend cannot be reached
            and no cache exists.
    """
    backend = get_entitlements_backend()
    return backend.get_user_entitlements(
        user.sub, user.email, user_info=user.claims, force_refresh=force_refresh
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
