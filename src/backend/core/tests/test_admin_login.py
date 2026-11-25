"""Tests for the limit on failed admin logins."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from django.contrib import admin
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

import pytest

from core import factories
from core.admin_site import (
    AdminLoginAddressThrottle,
    AdminLoginUsernameThrottle,
    ContactsAdminSite,
)

pytestmark = pytest.mark.django_db

ADMIN_EMAIL = "admin@example.com"
PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    """Admin pages link static files; tests run without a collected manifest."""
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }


@pytest.fixture
def staff():
    user = factories.UserFactory(
        admin_email=ADMIN_EMAIL, is_staff=True, is_superuser=True
    )
    user.set_password(PASSWORD)
    user.save()
    return user


@pytest.fixture
def budgets(monkeypatch):
    """Set the failure budgets. DRF binds THROTTLE_RATES at import, so the
    table itself is patched rather than the settings."""

    def set_rates(address, username):
        rates = AdminLoginAddressThrottle.THROTTLE_RATES
        monkeypatch.setitem(rates, AdminLoginAddressThrottle.scope, address)
        monkeypatch.setitem(rates, AdminLoginUsernameThrottle.scope, username)
        cache.clear()

    return set_rates


def login(username, password, **extra):
    data = {
        "username": username,
        "password": password,
        # The admin form sends this field. Without it, a successful login
        # redirects to LOGIN_REDIRECT_URL, which can be unset.
        "next": reverse("admin:index"),
    }
    return Client().post(reverse("admin:login"), data, **extra)


def test_the_admin_site_has_the_login_limit():
    assert isinstance(admin.site, ContactsAdminSite)


@pytest.mark.usefixtures("staff")
def test_login_works(budgets):
    budgets("3/minute", "3/minute")
    response = login(ADMIN_EMAIL, PASSWORD)
    assert response.status_code == 302


def test_failed_logins_are_limited_per_address(budgets):
    budgets("3/minute", "100/minute")
    codes = [login(f"user{i}@example.com", "bad").status_code for i in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_failed_logins_are_limited_per_username(budgets):
    """Each attempt comes from another address."""
    budgets("100/minute", "2/minute")
    codes = [
        login(ADMIN_EMAIL, "bad", REMOTE_ADDR=f"203.0.113.{i}").status_code
        for i in range(3)
    ]
    assert codes == [200, 200, 429]
    assert login("other@example.com", "bad").status_code == 200


def test_username_budget_ignores_case_spaces_and_unicode_forms(budgets):
    budgets("100/minute", "3/minute")
    # The last username starts with a fullwidth "a" (U+FF41).
    names = [
        ADMIN_EMAIL,
        ADMIN_EMAIL.upper(),
        f" {ADMIN_EMAIL} ",
        "ａ" + ADMIN_EMAIL[1:],
    ]
    codes = [login(name, "bad").status_code for name in names]
    assert codes == [200, 200, 200, 429]


@pytest.mark.usefixtures("staff")
def test_spent_budget_refuses_the_correct_password(budgets):
    budgets("2/minute", "100/minute")
    for _ in range(2):
        assert login(ADMIN_EMAIL, "bad").status_code == 200

    response = login(ADMIN_EMAIL, PASSWORD)

    assert response.status_code == 429
    assert 0 < int(response["Retry-After"]) <= 60
    assert "_auth_user_id" not in response.client.session


def test_refused_attempts_are_not_counted(budgets):
    budgets("1/minute", "100/minute")
    assert login(ADMIN_EMAIL, "bad").status_code == 200
    for _ in range(5):
        assert login(ADMIN_EMAIL, "bad").status_code == 429
    history = cache.get(
        AdminLoginAddressThrottle.cache_format
        % {"scope": AdminLoginAddressThrottle.scope, "ident": "127.0.0.1"}
    )
    assert len(history) == 1


@pytest.mark.usefixtures("staff")
def test_successful_logins_are_not_counted(budgets):
    budgets("2/minute", "2/minute")
    for _ in range(5):
        assert login(ADMIN_EMAIL, PASSWORD).status_code == 302


def test_login_page_is_not_limited(budgets):
    budgets("1/minute", "1/minute")
    assert login(ADMIN_EMAIL, "bad").status_code == 200
    assert login(ADMIN_EMAIL, "bad").status_code == 429
    assert Client().get(reverse("admin:login")).status_code == 200


def test_forwarded_for_does_not_split_the_address_budget(budgets):
    """Without a trusted proxy (NUM_PROXIES=0) the header is ignored."""
    budgets("2/minute", "100/minute")
    codes = [
        login(
            f"user{i}@example.com", "bad", HTTP_X_FORWARDED_FOR=f"203.0.113.{i}"
        ).status_code
        for i in range(3)
    ]
    assert codes == [200, 200, 429]


def test_non_staff_login_counts_as_a_failure(budgets):
    budgets("1/minute", "100/minute")
    user = factories.UserFactory(admin_email="plain@example.com")
    user.set_password(PASSWORD)
    user.save()
    assert login("plain@example.com", PASSWORD).status_code == 200
    assert login("plain@example.com", PASSWORD).status_code == 429
