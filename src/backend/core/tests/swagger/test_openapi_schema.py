"""
Test suite for generated openapi schema.
"""

import json
from pathlib import Path

from django.test import Client

import pytest

pytestmark = pytest.mark.django_db

COMMITTED_SCHEMA = Path(__file__).resolve().parents[2] / "api" / "openapi.json"


def test_openapi_client_schema_matches_committed_file():
    """The served schema is the committed one: regenerate it with
    ``bin/update_openapi_schema`` after an API change."""
    response = Client().get("/api/v1.0/swagger.json")

    assert response.status_code == 200
    with COMMITTED_SCHEMA.open(encoding="utf-8") as committed:
        assert response.json() == json.load(committed)
