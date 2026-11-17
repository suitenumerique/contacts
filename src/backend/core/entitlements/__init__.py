"""Entitlements service layer."""

from django.conf import settings
from django.utils.functional import SimpleLazyObject

from core.entitlements.factory import get_entitlements_backend


class EntitlementsUnavailableError(Exception):
    """Raised when the entitlements backend cannot be reached or returns an error."""


def _organization_claims(user):
    """Return the organization claim of *user*, from the stored organization.

    The identifier of an organization is the value of the claim that
    OIDC_USERINFO_ORGANIZATION_CLAIM names. An organization from an email
    domain has no claim, and the result is empty.
    """
    claim = settings.OIDC_USERINFO_ORGANIZATION_CLAIM
    if not claim:
        return {}
    return {claim: user.organization.external_id}


def get_user_entitlements(user, user_info=None, force_refresh=False):
    """Get user entitlements, delegating to the configured backend.

    Args:
        user: The user that the entitlements are for.
        user_info: The full OIDC user_info dict (forwarded to backend).
            The default is the organization claim of the stored user.
        force_refresh: If True, bypass backend cache and fetch fresh data.

    Returns:
        dict: {"can_access": bool, "can_admin": bool, ...}

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


def user_can_admin(user):
    """Whether *user* administers their organization's shared data.

    Fail-closed: False when the entitlements service is unavailable.
    """
    try:
        return bool(get_user_entitlements(user).get("can_admin"))
    except EntitlementsUnavailableError:
        return False
