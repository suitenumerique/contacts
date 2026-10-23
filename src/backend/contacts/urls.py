"""URL configuration for the contacts project"""

import logging

from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.db import connection
from django.db.utils import OperationalError
from django.http import HttpResponse
from django.urls import include, path

from drf_spectacular.views import (
    SpectacularJSONAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

logger = logging.getLogger(__name__)


def lbheartbeat(request):
    """Load-balancer probe: the process answers HTTP."""
    return HttpResponse("OK")


def heartbeat(request):
    """Healthcheck: the process answers HTTP and the database is reachable."""
    try:
        connection.ensure_connection()
    except OperationalError as exc:
        logger.error("Database error: %s", exc)
        return HttpResponse("DB Unavailable", status=500)
    return HttpResponse("OK")


urlpatterns = [
    path(f"{settings.ADMIN_URL}/", admin.site.urls),
    path("", include("core.urls")),
    # CardDAV lives outside the versioned REST API: clients address it by
    # path shape, and its URLs are a wire contract with sync clients that
    # must not move when the REST API is versioned up.
    path("", include("carddav.urls")),
    # Both paths are listed in Production.SECURE_REDIRECT_EXEMPT.
    path("__lbheartbeat__/", lbheartbeat, name="lbheartbeat"),
    path("__heartbeat__/", heartbeat, name="heartbeat"),
]

# API for other Suite apps, called with their user's access token
# (docs/resource_server.md).
if settings.OIDC_RESOURCE_SERVER_ENABLED:
    urlpatterns.append(path("", include("core.external_api.urls")))

if settings.DEBUG:
    from debug_toolbar.toolbar import debug_toolbar_urls

    urlpatterns = urlpatterns + staticfiles_urlpatterns() + debug_toolbar_urls()

if settings.USE_SWAGGER or settings.DEBUG:
    urlpatterns += [
        path(
            f"api/{settings.API_VERSION}/swagger.json",
            SpectacularJSONAPIView.as_view(
                api_version=settings.API_VERSION,
                urlconf="core.urls",
            ),
            name="client-api-schema",
        ),
        path(
            f"api/{settings.API_VERSION}/swagger/",
            SpectacularSwaggerView.as_view(url_name="client-api-schema"),
            name="swagger-ui-schema",
        ),
        path(
            f"api/{settings.API_VERSION}/redoc/",
            SpectacularRedocView.as_view(url_name="client-api-schema"),
            name="redoc-schema",
        ),
    ]
    if settings.OIDC_RESOURCE_SERVER_ENABLED:
        urlpatterns += [
            path(
                f"external_api/{settings.API_VERSION}/swagger.json",
                SpectacularJSONAPIView.as_view(
                    api_version=settings.API_VERSION,
                    urlconf="core.external_api.urls",
                    custom_settings={
                        "TITLE": "contacts external API",
                        "DESCRIPTION": "People and group suggestions for other "
                        "Suite apps, called with their user's access token.",
                    },
                ),
                name="external-api-schema",
            ),
            path(
                f"external_api/{settings.API_VERSION}/swagger/",
                SpectacularSwaggerView.as_view(url_name="external-api-schema"),
                name="external-swagger-ui-schema",
            ),
        ]
