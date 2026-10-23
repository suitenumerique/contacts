"""Permissions of the /external_api/ endpoints."""

from django.conf import settings

from lasuite.oidc_resource_server.authentication import ResourceServerAuthentication
from rest_framework import permissions


class ResourceServerClientPermission(permissions.BasePermission):
    """The request carries a user token issued to an allowed Suite app.

    The authentication must be the resource server one (an introspected
    access token, never a session or a channel key), and the app the token
    was issued to (its audience) must be in ``OIDC_RS_ALLOWED_AUDIENCES``.
    """

    message = "This application may not call the Contacts API."

    def has_permission(self, request, view):
        if not isinstance(
            request.successful_authenticator, ResourceServerAuthentication
        ):
            return False
        if not request.user or not request.user.is_authenticated:
            return False
        audience = getattr(request, "resource_server_token_audience", None)
        return audience in settings.OIDC_RS_ALLOWED_AUDIENCES
