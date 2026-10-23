"""Resource server authentication for /external_api/.

Other Suite apps call Contacts with the access token their user logged in
with. Each call is checked by introspection at the authorization server
(lasuite's ``ResourceServerBackend``). A share modal searches on every
keystroke, so a successful introspection is reused for a short while.
"""

import hashlib
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import SuspiciousOperation

from joserfc.errors import JoseError
from lasuite.oidc_resource_server.backend import ResourceServerBackend
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout
from rest_framework import status
from rest_framework.exceptions import APIException, AuthenticationFailed

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "oidc-rs-introspection"


class AuthorizationServerUnavailable(APIException):
    """The authorization server didn't answer the introspection in time."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The authorization server is unavailable. Try again later."
    default_code = "authorization_server_unavailable"


def introspection_cache_key(access_token):
    """Cache key of *access_token*'s introspection: a hash, never the token."""
    digest = hashlib.sha256(access_token.encode("utf-8")).hexdigest()
    return f"{CACHE_KEY_PREFIX}:{digest}"


class ContactsResourceServerBackend(ResourceServerBackend):
    """lasuite's resource server backend, with an introspection cache.

    Only introspections that passed every check are cached (an inactive,
    unscoped or audience-less token raises before reaching ``cache.set``),
    for ``OIDC_RS_INTROSPECTION_CACHE_TIMEOUT`` seconds, never past the
    token's own ``exp``. A token revoked at the authorization server keeps
    working here until its entry expires.
    """

    def get_user_info_with_introspection(self, access_token):
        timeout = settings.OIDC_RS_INTROSPECTION_CACHE_TIMEOUT
        if not timeout:
            return self._introspect_or_401(access_token)

        key = introspection_cache_key(access_token)
        user_info = cache.get(key)
        if user_info is not None:
            self.token_origin_audience = str(user_info[settings.OIDC_RS_AUDIENCE_CLAIM])
            return user_info

        user_info = self._introspect_or_401(access_token)
        ttl = timeout
        if (expires_at := user_info.get("exp")) is not None:
            try:
                ttl = min(ttl, int(float(expires_at) - time.time()))
            except TypeError, ValueError:
                # An unreadable expiry: don't guess, don't cache.
                ttl = 0
        if ttl > 0:
            cache.set(key, user_info, ttl)
        return user_info

    def _introspect_or_401(self, access_token):
        """Introspect: 401 for a rejected token, 503 if the server is down.

        lasuite raises ``SuspiciousOperation`` for an inactive, unscoped or
        audience-less token (and joserfc errors for invalid claims), which
        Django answers with a 400 HTML page; API clients expect a 401
        telling them to get a new token.
        """
        try:
            return super().get_user_info_with_introspection(access_token)
        except (SuspiciousOperation, JoseError) as exc:
            # JoseError: claims that fail validation, e.g. a past ``exp``.
            logger.info("Resource server token refused: %s", exc)
            raise AuthenticationFailed("Invalid or expired access token.") from exc
        except (RequestsConnectionError, Timeout) as exc:
            # Not the token's fault: the caller should retry, not refresh.
            logger.warning("Introspection failed: %s", exc)
            raise AuthorizationServerUnavailable() from exc
