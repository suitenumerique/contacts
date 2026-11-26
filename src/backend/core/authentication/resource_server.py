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
from django.core.exceptions import SuspiciousOperation, ValidationError
from django.db import IntegrityError

from joserfc.errors import JoseError
from lasuite.oidc_resource_server.backend import ResourceServerBackend
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError, Timeout
from rest_framework import status
from rest_framework.exceptions import APIException, AuthenticationFailed

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "oidc-rs-introspection"


class AuthorizationServerUnavailable(APIException):
    """The authorization server didn't answer the introspection in time."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The authorization server is unavailable. Try again later."
    default_code = "authorization_server_unavailable"


def _is_server_error(exc):
    """Tell if *exc* is an HTTP error answer of status 5xx."""
    if not isinstance(exc, HTTPError) or exc.response is None:
        return False
    return exc.response.status_code >= 500


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

    def get_or_create_user(self, access_token, id_token, payload):
        """Return the user of the token. Create the user for an allowed app only.

        With ``OIDC_RS_CREATE_USER``, lasuite creates a user that it does not
        know. The call of an app that is not in ``OIDC_RS_ALLOWED_AUDIENCES``
        is refused after the authentication, and a refused call must not write.
        """
        if self.token_origin_audience not in settings.OIDC_RS_ALLOWED_AUDIENCES:
            return self.get_user(access_token, id_token, payload)
        try:
            return super().get_or_create_user(access_token, id_token, payload)
        except (IntegrityError, ValidationError) as exc:
            # Usually a concurrent call of the same person created the user
            # first. The model validates the unique sub before the insert, so
            # either error can occur.
            user = self.get_user(access_token, id_token, payload)
            if user is None:
                raise
            logger.debug("User created by a concurrent call: %s", exc)
            return user
        except (RequestsConnectionError, Timeout) as exc:
            logger.warning("Userinfo failed: %s", exc)
            raise AuthorizationServerUnavailable() from exc
        except SuspiciousOperation as exc:
            # lasuite turns an HTTP error of the userinfo into this error. An
            # error of the server is not the token's fault.
            if not _is_server_error(exc.__cause__):
                raise
            logger.warning("Userinfo failed: %s", exc.__cause__)
            raise AuthorizationServerUnavailable() from exc

    def _introspect_or_401(self, access_token):
        """Introspect: 401 for a rejected token, 503 if the server is down.

        lasuite raises ``SuspiciousOperation`` for an unscoped or
        audience-less token (and joserfc errors for invalid claims), which
        Django answers with a 400 HTML page; API clients expect a 401
        telling them to get a new token. For an inactive token, lasuite
        raises a 401 with its own message. Each cause gets the same message
        here.
        """
        try:
            return super().get_user_info_with_introspection(access_token)
        except (SuspiciousOperation, JoseError, AuthenticationFailed) as exc:
            # JoseError: claims that fail validation, e.g. a past ``exp``.
            logger.info("Resource server token refused: %s", exc)
            raise AuthenticationFailed("Invalid or expired access token.") from exc
        except (RequestsConnectionError, Timeout) as exc:
            # Not the token's fault: the caller should retry, not refresh.
            logger.warning("Introspection failed: %s", exc)
            raise AuthorizationServerUnavailable() from exc
