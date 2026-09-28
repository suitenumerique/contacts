"""Management command to create a Channel and print its secret once.

Organization- and global-scoped channels cannot be created over the API
(see ChannelViewSet.create): they act on behalf of other users, so minting
one is an operator action. This is the CLI half of that, used to hand a
service (the directory service, Messages) its credentials.
"""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from core.enums import ChannelScope, ChannelScopeLevel, ChannelType
from core.models import Channel, Organization, User


class Command(BaseCommand):
    """Create a channel and print its API key (shown exactly once)."""

    help = "Create an integration channel and print its API key once"

    def add_arguments(self, parser):
        """Define the channel's name, type, scope level, target and scopes."""
        parser.add_argument("--name", required=True, help="Human-readable name.")
        parser.add_argument(
            "--type",
            default=ChannelType.API_KEY.value,
            choices=[t.value for t in ChannelType],
            help="api_key for REST service keys, carddav for sync credentials.",
        )
        parser.add_argument(
            "--scope-level",
            default=ChannelScopeLevel.GLOBAL.value,
            choices=[level.value for level in ChannelScopeLevel],
            help="global (any user), organization (one org), user (one user).",
        )
        parser.add_argument(
            "--scopes",
            action="append",
            default=[],
            help=(
                "Scope to grant; repeat the flag or pass a comma-separated "
                f"list. One of: {', '.join(s.value for s in ChannelScope)}."
            ),
        )
        parser.add_argument(
            "--organization",
            help="Organization external_id (required for --scope-level organization).",
        )
        parser.add_argument(
            "--user",
            help="User identity email (required for --scope-level user).",
        )
        parser.add_argument(
            "--expires-at",
            help="Optional ISO-8601 datetime after which the channel stops working.",
        )
        parser.add_argument(
            "--groups",
            action="store_true",
            help="carddav only: also sync the groups, as group cards.",
        )

    def handle(self, *args, **options):
        """Validate the arguments, create the channel, print the secret."""
        scopes = _split_scopes(options["scopes"])
        channel_type = options["type"]
        try:
            Channel.validate_scopes(channel_type, scopes)
        except ValidationError as exc:
            raise CommandError(_flatten(exc)) from exc

        scope_level = options["scope_level"]
        user = _resolve_user(options.get("user"), scope_level)
        organization = _resolve_organization(options.get("organization"), scope_level)
        # A user-scoped channel inherits its user's organization, the way
        # the API does, so org filtering works the same on both paths.
        if scope_level == ChannelScopeLevel.USER:
            organization = user.organization

        settings = {"scopes": scopes}
        if options.get("groups"):
            if channel_type != ChannelType.CARDDAV:
                raise CommandError("--groups only applies to --type carddav.")
            settings["groups"] = True
        if options.get("expires_at"):
            settings["expires_at"] = _parse_expires_at(options["expires_at"])

        channel = Channel(
            name=options["name"],
            type=channel_type,
            scope_level=scope_level,
            user=user,
            organization=organization,
            settings=settings,
        )
        api_key = channel.generate_api_key()
        try:
            channel.full_clean(exclude=["encrypted_settings"])
        except ValidationError as exc:
            raise CommandError(_flatten(exc)) from exc
        channel.save()

        self._report(channel, api_key)

    def _report(self, channel, api_key):
        """Print the channel identity and its one-time secret."""
        self.stdout.write(self.style.SUCCESS(f"Channel created: {channel.pk}"))
        self.stdout.write(f"name:        {channel.name}")
        self.stdout.write(f"type:        {channel.type}")
        self.stdout.write(f"scope_level: {channel.scope_level}")
        self.stdout.write(f"scopes:      {', '.join(channel.scopes)}")
        self.stdout.write("")
        if channel.type == ChannelType.CARDDAV:
            self.stdout.write("CardDAV Basic Auth credentials (shown once):")
            self.stdout.write("  username: <the user's identity email>")
            self.stdout.write(f"  password: {channel.carddav_password(api_key)}")
        else:
            self.stdout.write("REST credentials (shown once):")
            self.stdout.write(f"  X-Channel-Id: {channel.pk}")
            self.stdout.write(f"  X-API-Key:    {api_key}")


def _split_scopes(values):
    """Accept both repeated flags and comma-separated lists."""
    scopes = []
    for value in values:
        scopes.extend(item.strip() for item in value.split(",") if item.strip())
    unknown = sorted(set(scopes) - {s.value for s in ChannelScope})
    if unknown:
        raise CommandError(f"Unknown scope(s): {', '.join(unknown)}")
    return scopes


def _resolve_user(email, scope_level):
    """Resolve --user, required for (and only for) a user-scoped channel."""
    if scope_level != ChannelScopeLevel.USER:
        if email:
            raise CommandError("--user only applies to --scope-level user.")
        return None
    if not email:
        raise CommandError("--user is required for --scope-level user.")
    # User.email is not unique, so an ambiguous address must not silently
    # bind the channel to an arbitrary row.
    matches = list(User.objects.filter(email__iexact=email, is_active=True)[:2])
    if not matches:
        raise CommandError(f"No active user with email {email}.")
    if len(matches) > 1:
        raise CommandError(f"Several active users share the email {email}.")
    return matches[0]


def _resolve_organization(external_id, scope_level):
    """Resolve --organization, required for an organization-scoped channel."""
    if scope_level != ChannelScopeLevel.ORGANIZATION:
        if external_id:
            raise CommandError(
                "--organization only applies to --scope-level organization."
            )
        return None
    if not external_id:
        raise CommandError("--organization is required for --scope-level organization.")
    try:
        return Organization.objects.get(external_id=external_id)
    except Organization.DoesNotExist as exc:
        raise CommandError(f"No organization with external_id {external_id}.") from exc


def _parse_expires_at(raw):
    """Validate the ISO datetime here so a typo fails now, not at auth time.

    ``Channel.is_expired`` treats an unparseable value as already expired,
    which would silently produce a dead credential.
    """
    try:
        parsed = parse_datetime(raw)
    except ValueError:
        # Well-formed but out of range, e.g. month 13.
        parsed = None
    if parsed is None:
        raise CommandError(f"--expires-at is not an ISO-8601 datetime: {raw}")
    return raw


def _flatten(exc):
    """Render a Django ValidationError as a single CLI line."""
    if hasattr(exc, "error_dict"):
        return "; ".join(
            f"{key}: {' '.join(messages)}" for key, messages in exc.message_dict.items()
        )
    return " ".join(exc.messages)
