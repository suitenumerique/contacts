"""Rate limits for the CardDAV endpoint.

DRF's ``SimpleRateThrottle`` implements the sliding window and the cache
handling, so it is reused even though the view is a plain Django ``View``:
the throttles are driven explicitly from ``dispatch``.

``CardDAVAuthFailureThrottle``
    Counts failed authentications per client address. Once over budget,
    further failures from that address get 429 instead of 401. Requests
    without credentials are not failures (clients probe without them
    first), and valid credentials are never refused by it, so a guesser
    behind a shared address cannot lock the other users out.

``CardDAVRequestThrottle``
    Counts authenticated requests per credential, so one misconfigured
    device in a tight poll loop cannot saturate the backend. DAVx5
    downloads 10 cards per request, so an initial sync costs about one
    request per 10 cards in both books; the default budget has to cover
    that for the largest directory served.

The client address comes from DRF's ``get_ident``: ``REMOTE_ADDR``, or the
``X-Forwarded-For`` entry added by the outermost trusted proxy when the
``NUM_PROXIES`` setting says how many there are.
"""

from rest_framework.throttling import SimpleRateThrottle

from core.throttling import AuthFailureThrottle


class CardDAVAuthFailureThrottle(AuthFailureThrottle):
    """Throttle repeated authentication failures from one address."""

    scope = "carddav_auth"


class CardDAVRequestThrottle(SimpleRateThrottle):
    """Throttle authenticated request volume, per credential.

    Keyed on the channel, so two devices behind one address get their own
    budgets and one leaked credential cannot exhaust anybody else's.
    """

    scope = "carddav"

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    def get_cache_key(self, request, view):
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"channel:{self.channel.pk}",
        }
