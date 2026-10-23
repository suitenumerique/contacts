"""URLs of /external_api/, mounted when OIDC_RESOURCE_SERVER_ENABLED is on.

Kept out of ``core.urls`` so the web app's schema (and the frontend client
generated from it) doesn't list them; this module is the urlconf of the
external API's own schema.
"""

from django.conf import settings
from django.urls import include, path

from . import views

ENDPOINTS = {
    "search": path("search/", views.SearchView.as_view(), name="external-search"),
    "interactions": path(
        "interactions/", views.InteractionsView.as_view(), name="external-interactions"
    ),
    "group_members": path(
        "groups/<uuid:group_id>/members/",
        views.GroupMembersView.as_view(),
        name="external-group-members",
    ),
}

urlpatterns = [
    path(
        f"external_api/{settings.API_VERSION}/",
        include(
            [
                pattern
                for name, pattern in ENDPOINTS.items()
                if settings.EXTERNAL_API.get(name, {}).get("enabled", False)
            ]
        ),
    ),
]
