"""Enums for the contacts core application."""

from enum import StrEnum


class Scope(StrEnum):
    """Who a contact or a group belongs to, and so who sees it.

    - PERSONAL: its ``owner``'s, seen by them alone.
    - ORGANIZATION: its organization's shared address book, seen by every
      member and edited by the organization's administrators (unless it
      comes from a ``source``). No ``owner``.
    """

    PERSONAL = "personal"
    ORGANIZATION = "organization"


class ChannelType(StrEnum):
    """Kind of credential a Channel holds.

    ``Channel.type`` is a free-form CharField, as in suitenumerique/messages,
    so adding a type needs no migration.
    """

    API_KEY = "api_key"
    CARDDAV = "carddav"


class ChannelScopeLevel(StrEnum):
    """Resource a Channel is bound to.

    - GLOBAL: nothing; acts for any user through ``on_behalf_of``.
      Created through Django admin or CLI only.
    - ORGANIZATION: one Organization; ``on_behalf_of`` users must belong to it.
    - USER: one User; acts only for that user.
    """

    GLOBAL = "global"
    ORGANIZATION = "organization"
    USER = "user"


class ChannelScope(StrEnum):
    """Capabilities granted to a Channel, stored in ``settings["scopes"]``."""

    CONTACTS_READ = "contacts:read"
    GROUPS_READ = "groups:read"
    CARDDAV_READ = "carddav:read"
    CARDDAV_WRITE = "carddav:write"


# Scopes usable by each channel type. The two sets are disjoint so a CardDAV
# device password can never be used against the REST API, and vice versa.
CHANNEL_TYPE_SCOPES = {
    ChannelType.API_KEY: frozenset(
        {ChannelScope.CONTACTS_READ, ChannelScope.GROUPS_READ}
    ),
    ChannelType.CARDDAV: frozenset(
        {ChannelScope.CARDDAV_READ, ChannelScope.CARDDAV_WRITE}
    ),
}
