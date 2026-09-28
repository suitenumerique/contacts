"""Tests for the ``create_channel`` management command."""

# pylint: disable=missing-function-docstring,redefined-outer-name

import base64
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client

import pytest

from core import factories, models
from core.enums import ChannelScope

pytestmark = pytest.mark.django_db


def run(*args):
    """Run the command and return everything it printed."""
    out = StringIO()
    call_command("create_channel", *args, stdout=out)
    return out.getvalue()


def field(output, label):
    """Read one ``label: value`` line out of the command output."""
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{label}:"):
            return stripped.split(":", 1)[1].strip()
    raise AssertionError(f"{label} not found in:\n{output}")


def test_creates_a_global_rest_channel_whose_key_verifies():
    output = run(
        "--name",
        "Directory service",
        "--scopes",
        "contacts:read,groups:read",
    )

    channel = models.Channel.objects.get()
    assert channel.name == "Directory service"
    assert channel.type == "api_key"
    assert channel.scope_level == "global"
    assert channel.scopes == ["contacts:read", "groups:read"]
    assert channel.user is None
    assert channel.organization is None
    # The printed key is the real secret, and only its hash is stored.
    assert channel.verify_api_key(field(output, "X-API-Key"))
    assert field(output, "X-Channel-Id") == str(channel.pk)


def test_repeated_scope_flags_accumulate():
    run(
        "--name",
        "Directory",
        "--scopes",
        "contacts:read",
        "--scopes",
        "groups:read",
    )

    assert models.Channel.objects.get().scopes == ["contacts:read", "groups:read"]


def test_carddav_password_authenticates_on_behalf_of_a_user():
    """A global CardDAV channel is the credential Messages would use."""
    user = factories.UserFactory(email="agent@example.org")

    output = run(
        "--name",
        "Messages",
        "--type",
        "carddav",
        "--scopes",
        ChannelScope.CARDDAV_WRITE.value,
    )
    password = field(output, "password")

    token = base64.b64encode(f"{user.email}:{password}".encode()).decode()
    response = Client().generic(
        "PROPFIND",
        "/carddav/",
        data=b"",
        content_type="application/xml",
        HTTP_AUTHORIZATION=f"Basic {token}",
        HTTP_DEPTH="0",
    )

    assert response.status_code == 207
    assert user.email in response.content.decode()


def test_carddav_channel_can_sync_groups():
    run(
        "--name",
        "iPhone",
        "--type",
        "carddav",
        "--scopes",
        ChannelScope.CARDDAV_WRITE.value,
        "--groups",
    )
    assert models.Channel.objects.get().settings["groups"] is True


def test_user_scoped_channel_binds_to_the_user_and_its_organization():
    user = factories.UserFactory(email="owner@example.org")

    run(
        "--name",
        "Phone",
        "--type",
        "carddav",
        "--scope-level",
        "user",
        "--user",
        "OWNER@example.org",
        "--scopes",
        ChannelScope.CARDDAV_READ.value,
    )

    channel = models.Channel.objects.get()
    assert channel.user == user
    assert channel.organization == user.organization


def test_organization_scoped_channel_resolves_by_external_id():
    organization = factories.OrganizationFactory(external_id="12345678900011")

    run(
        "--name",
        "Org directory",
        "--scope-level",
        "organization",
        "--organization",
        "12345678900011",
        "--scopes",
        "contacts:read",
    )

    channel = models.Channel.objects.get()
    assert channel.organization == organization
    assert channel.user is None


def test_expires_at_is_stored_where_the_model_reads_it():
    run(
        "--name",
        "Temporary",
        "--scopes",
        "contacts:read",
        "--expires-at",
        "2020-01-01T00:00:00+00:00",
    )

    channel = models.Channel.objects.get()
    assert channel.settings["expires_at"] == "2020-01-01T00:00:00+00:00"
    assert channel.is_expired


@pytest.mark.parametrize(
    "args,message",
    [
        (
            ["--name", "X", "--scopes", "carddav:read"],
            "api_key channels only accept",
        ),
        (
            ["--name", "X", "--type", "carddav", "--scopes", "contacts:read"],
            "carddav channels only accept",
        ),
        (["--name", "X", "--scopes", "contacts:write"], "Unknown scope"),
        (["--name", "X"], "At least one scope"),
        (
            ["--name", "X", "--scope-level", "user", "--scopes", "contacts:read"],
            "--user is required",
        ),
        (
            [
                "--name",
                "X",
                "--scope-level",
                "organization",
                "--scopes",
                "contacts:read",
            ],
            "--organization is required",
        ),
        (
            [
                "--name",
                "X",
                "--scopes",
                "contacts:read",
                "--expires-at",
                "next tuesday",
            ],
            "not an ISO-8601 datetime",
        ),
        (
            [
                "--name",
                "X",
                "--scopes",
                "contacts:read",
                "--expires-at",
                "2026-13-45T00:00:00Z",
            ],
            "not an ISO-8601 datetime",
        ),
        (
            ["--name", "X", "--scopes", "contacts:read", "--groups"],
            "--groups only applies",
        ),
    ],
)
def test_invalid_arguments_are_refused(args, message):
    with pytest.raises(CommandError, match=message):
        run(*args)

    assert not models.Channel.objects.exists()


def test_unknown_user_is_refused():
    with pytest.raises(CommandError, match="No active user"):
        run(
            "--name",
            "X",
            "--scope-level",
            "user",
            "--user",
            "nobody@example.org",
            "--scopes",
            "contacts:read",
        )


def test_ambiguous_user_email_is_refused():
    """User.email is not unique, so a duplicate must not bind arbitrarily."""
    factories.UserFactory(email="shared@example.org")
    factories.UserFactory(email="shared@example.org")

    with pytest.raises(CommandError, match="Several active users"):
        run(
            "--name",
            "X",
            "--scope-level",
            "user",
            "--user",
            "shared@example.org",
            "--scopes",
            "contacts:read",
        )


def test_inactive_user_is_not_a_target():
    factories.UserFactory(email="gone@example.org", is_active=False)

    with pytest.raises(CommandError, match="No active user"):
        run(
            "--name",
            "X",
            "--scope-level",
            "user",
            "--user",
            "gone@example.org",
            "--scopes",
            "contacts:read",
        )


def test_target_flags_are_rejected_on_the_wrong_scope_level():
    with pytest.raises(CommandError, match="--user only applies"):
        run("--name", "X", "--user", "a@example.org", "--scopes", "contacts:read")

    with pytest.raises(CommandError, match="--organization only applies"):
        run("--name", "X", "--organization", "123", "--scopes", "contacts:read")
