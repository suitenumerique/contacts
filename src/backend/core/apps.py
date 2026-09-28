"""Contacts Core application"""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Configuration class for the contacts core app."""

    name = "core"
    app_label = "core"
    verbose_name = "contacts core application"

    def ready(self):
        """Register signal receivers and OpenAPI extensions."""
        # pylint: disable=import-outside-toplevel, unused-import
        from . import signals  # noqa: PLC0415
        from .api import schema  # noqa: PLC0415
