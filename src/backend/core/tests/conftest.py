"""Fixtures for tests in the contacts core application"""

from django.core.cache import cache

import pytest


@pytest.fixture(autouse=True)
def clear_cache():
    """Fixture to clear the cache after each test."""
    yield
    cache.clear()
