"""The external API has its own schema, kept out of the web app's."""

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def test_the_external_schema_lists_the_three_endpoints():
    """The external schema lists the three endpoints."""
    schema = APIClient().get("/external_api/v1.0/swagger.json").json()

    assert set(schema["paths"]) == {
        "/external_api/v1.0/search/",
        "/external_api/v1.0/interactions/",
        "/external_api/v1.0/groups/{group_id}/members/",
    }
    result = schema["components"]["schemas"]["SearchResult"]
    assert result["discriminator"]["propertyName"] == "type"


def test_the_web_app_schema_does_not_list_them():
    """The web app schema does not list them."""
    schema = APIClient().get("/api/v1.0/swagger.json").json()

    assert not [path for path in schema["paths"] if "external_api" in path]
