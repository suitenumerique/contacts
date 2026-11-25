"""Admin site with a limit on failed password logins.

A failed login counts against the client address and against the submitted
username. When one of the two budgets is spent, the login form answers 429
before it checks the password. A correct password is then refused too,
or the limit would not slow a guesser down.
"""

import hashlib
import unicodedata
from typing import Any

from django.contrib import admin
from django.contrib.admin.apps import AdminConfig
from django.http import HttpRequest, HttpResponse

from core.throttling import AuthFailureThrottle, retry_after


class AdminLoginAddressThrottle(AuthFailureThrottle):
    """Throttle failed admin logins from one address."""

    scope = "admin_login"


class AdminLoginUsernameThrottle(AuthFailureThrottle):
    """Throttle failed admin logins for one username, from any address."""

    scope = "admin_login_username"

    def get_cache_key(self, request: HttpRequest, view: object) -> str | None:
        # The login form reads the username in NFKC, without outer spaces.
        # The key uses the same form, in lower case, so that each spelling
        # of one username counts against the same budget.
        username = unicodedata.normalize(
            "NFKC", request.POST.get("username", "").strip()
        ).lower()
        if not username:
            return None
        # The digest keeps the cache key short and free of unsafe characters.
        digest = hashlib.sha256(username.encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class ContactsAdminSite(admin.AdminSite):
    """Default admin site, with the login limit."""

    def login(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        if request.method != "POST":
            return super().login(request, extra_context)

        throttles = [AdminLoginAddressThrottle(), AdminLoginUsernameThrottle()]
        spent = [t for t in throttles if t.exhausted(request, self)]
        if spent:
            response = HttpResponse(
                "Too many failed login attempts. Try again later.",
                status=429,
                content_type="text/plain",
            )
            response["Retry-After"] = max(int(retry_after(t)) for t in spent)
            return response

        response = super().login(request, extra_context)
        # A successful login redirects. All other responses show the form
        # again with an error.
        if response.status_code != 302:
            for throttle in throttles:
                throttle.record_failure(request, self)
        return response


class ContactsAdminConfig(AdminConfig):
    """Admin application that uses ``ContactsAdminSite`` as ``admin.site``."""

    default_site = "core.admin_site.ContactsAdminSite"
