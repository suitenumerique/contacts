"""URL wiring for the CardDAV endpoint.

A single catch-all: DAV addresses resources by path shape, not by named
routes, and clients walk the tree themselves (root → principal → home →
collection → card). Routing each shape separately would only duplicate the
parsing the view has to do anyway.

``/.well-known/carddav`` is the RFC 6764 bootstrap: a user types a bare
host name and the client finds the service from there.
"""

from django.urls import re_path
from django.views.decorators.csrf import csrf_exempt

from carddav.views import CardDAVView, well_known

urlpatterns = [
    re_path(
        r"^\.well-known/carddav/?$", csrf_exempt(well_known), name="carddav-well-known"
    ),
    re_path(r"^carddav/(?P<path>.*)$", CardDAVView.as_view(), name="carddav"),
]
