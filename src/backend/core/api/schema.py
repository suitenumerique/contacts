"""drf-spectacular extensions describing the API's authentication schemes."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class OIDCAuthenticationScheme(OpenApiAuthenticationExtension):
    """OIDC bearer tokens (mozilla-django-oidc)."""

    target_class = "mozilla_django_oidc.contrib.drf.OIDCAuthentication"
    name = "oidcAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer"}


class ChannelApiKeyScheme(OpenApiAuthenticationExtension):
    """Service channel keys: X-Channel-Id and X-API-Key headers, sent together."""

    target_class = "core.authentication.channel.ChannelApiKeyAuthentication"
    name = ["channelId", "channelApiKey"]

    def get_security_requirement(self, auto_schema):
        return {"channelId": [], "channelApiKey": []}

    def get_security_definition(self, auto_schema):
        return [
            {"type": "apiKey", "in": "header", "name": "X-Channel-Id"},
            {"type": "apiKey", "in": "header", "name": "X-API-Key"},
        ]
