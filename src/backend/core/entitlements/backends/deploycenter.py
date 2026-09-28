"""DeployCenter (Espace Operateur) entitlements backend."""

import logging

from django.conf import settings
from django.core.cache import cache

import requests

from core.entitlements import EntitlementsUnavailableError
from core.entitlements.backends.base import EntitlementsBackend

logger = logging.getLogger(__name__)


class DeployCenterEntitlementsBackend(EntitlementsBackend):
    """Backend that fetches entitlements from the DeployCenter API.

    Args:
        base_url: Full URL of the entitlements endpoint
            (e.g. "https://dc.example.com/api/v1.0/entitlements/").
        service_id: The service identifier in DeployCenter.
        api_key: API key for X-Service-Auth header.
        timeout: HTTP request timeout in seconds.
        oidc_claims: List of OIDC claim names to extract from user_info
            and forward as query params (e.g. ["siret"]).
        stale_timeout: Seconds the last known answer stays usable when
            DeployCenter is unreachable.
    """

    def __init__(  # noqa: PLR0913  # pylint: disable=too-many-arguments
        self,
        base_url,
        service_id,
        api_key,
        *,
        timeout=10,
        oidc_claims=None,
        stale_timeout=24 * 60 * 60,
    ):
        self.base_url = base_url
        self.service_id = service_id
        self.api_key = api_key
        self.timeout = timeout
        self.oidc_claims = oidc_claims or []
        self.stale_timeout = stale_timeout

    def _cache_key(self, user_sub):
        return f"entitlements:user:{user_sub}"

    def _stale_cache_key(self, user_sub):
        return f"entitlements:user:{user_sub}:stale"

    def _make_request(self, user_email, user_info=None):
        """Make a request to the DeployCenter entitlements API.

        Returns:
            dict | None: The response data, or None on failure.
        """
        params = {
            "service_id": self.service_id,
            "account_type": "user",
            "account_email": user_email,
        }

        # Forward configured OIDC claims as query params
        if user_info:
            for claim in self.oidc_claims:
                if claim in user_info:
                    params[claim] = user_info[claim]

        headers = {
            "X-Service-Auth": f"Bearer {self.api_key}",
        }

        try:
            response = requests.get(
                self.base_url,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException, ValueError:
            email = user_email or ""
            email_domain = email.split("@")[-1] if "@" in email else "?"
            logger.warning(
                "DeployCenter entitlements request failed for user@%s",
                email_domain,
                exc_info=True,
            )
            return None

    def get_user_entitlements(
        self, user_sub, user_email, user_info=None, force_refresh=False
    ):
        """Fetch user entitlements from DeployCenter with caching.

        Answers are cached for ENTITLEMENTS_CACHE_TIMEOUT, and a second copy
        is kept for ``stale_timeout``. On cache miss or force_refresh the
        API is called; when it fails, the stale copy is returned if there
        is one, else EntitlementsUnavailableError is raised.

        A user without a ``sub`` is never cached: users without one would
        otherwise share a single cache entry.
        """
        cacheable = bool(user_sub)
        cache_key = self._cache_key(user_sub)
        stale_key = self._stale_cache_key(user_sub)

        if cacheable and not force_refresh:
            cached = cache.get(cache_key)
            if cached is not None:
                return cached

        data = self._make_request(user_email, user_info=user_info)

        if data is None:
            stale = cache.get(stale_key) if cacheable else None
            if stale is not None:
                return stale
            raise EntitlementsUnavailableError(
                "Failed to fetch user entitlements from DeployCenter"
            )

        entitlements = data.get("entitlements", {})
        result = {
            "can_access": entitlements.get("can_access", False),
            "can_admin": entitlements.get("can_admin", False),
        }

        # Organization name from DeployCenter response (if present)
        org = data.get("organization") or {}
        org_name = org.get("name", "")
        if org_name:
            result["organization_name"] = org_name

        if cacheable:
            cache.set(cache_key, result, settings.ENTITLEMENTS_CACHE_TIMEOUT)
            cache.set(stale_key, result, self.stale_timeout)
        return result
