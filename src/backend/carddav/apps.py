"""CardDAV application."""

from django.apps import AppConfig


class CardDAVConfig(AppConfig):
    """Configuration class for the CardDAV app."""

    name = "carddav"
    verbose_name = "CardDAV"

    def ready(self):
        """Attach the change-journal receivers to ``core.models.Contact``.

        Imported here rather than at module level: the receivers name
        ``core.models.Contact``, which does not exist yet while the app
        registry is still being populated.
        """
        # pylint: disable=import-outside-toplevel,unused-import
        from carddav import signals
