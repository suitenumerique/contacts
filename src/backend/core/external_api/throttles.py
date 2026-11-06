"""Rate limits of the /external_api/ endpoints."""

from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle

from core.models import Channel


class ExternalRateThrottle(ScopedRateThrottle):
    """``ScopedRateThrottle``, counted per channel for service calls.

    A user-token request is limited per user under the view's
    ``throttle_scope``. A channel request acts for many users (a whole
    organization's incoming mail), so it is limited per channel, under
    ``<throttle_scope>_channel``.
    """

    def allow_request(self, request, view):
        scope = getattr(view, self.scope_attr, None)
        if not scope:
            return True
        if isinstance(request.auth, Channel):
            scope = f"{scope}_channel"
        self.scope = scope
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return SimpleRateThrottle.allow_request(self, request, view)

    def get_cache_key(self, request, view):
        if isinstance(request.auth, Channel):
            return self.cache_format % {
                "scope": self.scope,
                "ident": f"channel-{request.auth.pk}",
            }
        return super().get_cache_key(request, view)
