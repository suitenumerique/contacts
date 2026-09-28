"""Tests for the healthcheck endpoints."""

from unittest import mock

from django.db.utils import OperationalError
from django.test import Client

import pytest


def test_lbheartbeat_needs_no_database():
    """The load-balancer probe answers without touching the database."""
    with mock.patch("contacts.urls.connection") as connection:
        response = Client().get("/__lbheartbeat__/")
    assert response.status_code == 200
    connection.ensure_connection.assert_not_called()


@pytest.mark.django_db
def test_heartbeat_ok():
    """The healthcheck passes when the database answers."""
    response = Client().get("/__heartbeat__/")
    assert response.status_code == 200
    assert response.content == b"OK"


def test_heartbeat_reports_database_outage():
    """An unreachable database turns the healthcheck into a 500."""
    with mock.patch("contacts.urls.connection") as connection:
        connection.ensure_connection.side_effect = OperationalError("down")
        response = Client().get("/__heartbeat__/")
    assert response.status_code == 500
