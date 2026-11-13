"""Rate limit for failed authentications.

DRF's ``SimpleRateThrottle`` implements the sliding window and the cache
handling. It is reused for plain Django views, which drive the throttle
explicitly.

The client address comes from DRF's ``get_ident``: ``REMOTE_ADDR``, or the
``X-Forwarded-For`` entry added by the outermost trusted proxy when the
``NUM_PROXIES`` setting says how many there are.
"""

import math

from django.http import HttpRequest

from rest_framework.throttling import SimpleRateThrottle


def retry_after(throttle: SimpleRateThrottle) -> str:
    """Seconds to announce in ``Retry-After`` for a refused *throttle*."""
    wait = throttle.wait()
    if wait is None:
        wait = throttle.duration
    return str(max(1, math.ceil(wait)))


class AuthFailureThrottle(SimpleRateThrottle):
    """Throttle repeated authentication failures from one address.

    A subclass sets ``scope``. It can override ``get_cache_key`` to count
    the failures against something other than the client address.
    """

    def __init__(self) -> None:
        super().__init__()
        # Set by exhausted() / allow_request(), read by wait().
        self.key = None
        self.now = None
        self.history = []

    def get_cache_key(self, request: HttpRequest, view: object) -> str | None:
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }

    def exhausted(self, request: HttpRequest, view: object) -> bool:
        """Whether the failure budget is spent, without recording a hit."""
        if self.rate is None:
            return False
        self.key = self.get_cache_key(request, view)
        if self.key is None:
            return False
        self.now = self.timer()
        self.history = [
            stamp
            for stamp in self.cache.get(self.key, [])
            if stamp > self.now - self.duration
        ]
        return len(self.history) >= self.num_requests

    def record_failure(self, request: HttpRequest, view: object) -> None:
        """Count one failed authentication."""
        self.allow_request(request, view)
