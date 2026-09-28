"""URL configuration for the core app."""

from django.conf import settings
from django.urls import include, path

from lasuite.oidc_login.urls import urlpatterns as oidc_urls
from rest_framework.routers import DefaultRouter

from core.api import viewsets
from core.api.viewsets_channels import ChannelViewSet
from core.api.viewsets_task import TaskDetailView

# - Main endpoints
router = DefaultRouter()
router.register("users", viewsets.UserViewSet, basename="users")
router.register("contacts", viewsets.ContactViewSet, basename="contacts")
router.register("groups", viewsets.ContactGroupViewSet, basename="groups")
router.register("labels", viewsets.ContactLabelViewSet, basename="labels")
router.register("channels", ChannelViewSet, basename="channels")

urlpatterns = [
    path(
        f"api/{settings.API_VERSION}/",
        include(
            [
                *router.urls,
                *oidc_urls,
                # Task status polling endpoint
                path(
                    "tasks/<str:task_id>/",
                    TaskDetailView.as_view(),
                    name="task-detail",
                ),
            ]
        ),
    ),
    path(f"api/{settings.API_VERSION}/config/", viewsets.ConfigView.as_view()),
]
