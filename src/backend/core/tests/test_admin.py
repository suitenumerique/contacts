"""Smoke tests for the Django admin."""

# pylint: disable=missing-function-docstring,redefined-outer-name

import importlib

from django.urls import clear_url_caches, reverse

import pytest

from core import factories, models

from contacts import urls as contacts_urls

pytestmark = pytest.mark.django_db


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
def admin_client(client):
    staff = factories.UserFactory(
        admin_email="admin@example.com", is_staff=True, is_superuser=True
    )
    client.force_login(staff)
    return client


@pytest.mark.parametrize(
    "model",
    [
        models.Organization,
        models.User,
        models.Contact,
        models.ContactGroup,
        models.ContactLabel,
        models.Channel,
    ],
)
def test_changelist_renders(admin_client, model):
    factories.ContactFactory()
    factories.ChannelFactory()
    opts = model._meta  # pylint: disable=protected-access
    url = reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
    assert admin_client.get(url).status_code == 200


def test_user_change_page_does_not_edit_the_organization(admin_client):
    user = factories.UserFactory()
    url = reverse("admin:core_user_change", args=[user.pk])
    response = admin_client.get(url)
    assert response.status_code == 200
    assert 'name="organization"' not in response.content.decode()


def test_contact_changelist_query_count_does_not_grow(
    admin_client, django_assert_max_num_queries
):
    url = reverse("admin:core_contact_changelist")

    def add_overlay():
        parent = factories.ContactFactory(
            owner=None, organization=factories.OrganizationFactory()
        )
        models.Contact.objects.create(
            owner=factories.UserFactory(organization=parent.organization),
            organization=parent.organization,
            overlay_of=parent,
        )

    add_overlay()
    with django_assert_max_num_queries(50) as baseline:
        admin_client.get(url)
    for _ in range(5):
        add_overlay()
    with django_assert_max_num_queries(len(baseline.captured_queries)):
        admin_client.get(url)


def test_channel_change_page_renders(admin_client):
    channel = factories.ChannelFactory()
    url = reverse("admin:core_channel_change", args=[channel.pk])

    response = admin_client.get(url)

    assert response.status_code == 200
    assert reverse("admin:core_channel_rotate_token", args=[channel.pk]) in (
        response.content.decode()
    )


def test_contact_change_page_renders(admin_client):
    contact = factories.ContactFactory()
    url = reverse("admin:core_contact_change", args=[contact.pk])
    assert admin_client.get(url).status_code == 200


def test_rotate_token_shows_the_new_key_once(admin_client):
    channel = factories.ChannelFactory()
    old_key = channel.raw_api_key
    url = reverse("admin:core_channel_rotate_token", args=[channel.pk])

    response = admin_client.post(url)

    assert response.status_code == 200
    new_key = response.context["api_key"]
    assert new_key in response.content.decode()
    assert response.context["carddav_password"] is None
    channel.refresh_from_db()
    assert channel.verify_api_key(new_key)
    assert not channel.verify_api_key(old_key)


def test_rotate_token_shows_the_carddav_password(admin_client):
    channel = factories.ChannelFactory(
        type="carddav", settings={"scopes": ["carddav:read"]}
    )
    url = reverse("admin:core_channel_rotate_token", args=[channel.pk])

    response = admin_client.post(url)

    password = response.context["carddav_password"]
    assert password == f"{channel.pk.hex}{response.context['api_key']}"
    assert password in response.content.decode()


@pytest.fixture
def reload_urls():
    """Rebuild the URLconf after a settings change, and again on teardown."""

    def reload():
        clear_url_caches()
        importlib.reload(contacts_urls)

    yield reload
    reload()


def test_admin_is_served_at_the_configured_path(admin_client, settings, reload_urls):
    settings.ADMIN_URL = "gestion"
    reload_urls()

    assert admin_client.get("/gestion/").status_code == 200
    assert admin_client.get("/admin/").status_code == 404


def test_rotate_token_refuses_get(admin_client):
    channel = factories.ChannelFactory()
    url = reverse("admin:core_channel_rotate_token", args=[channel.pk])
    old_hashes = channel.encrypted_settings["api_key_hashes"]

    assert admin_client.get(url).status_code == 405
    channel.refresh_from_db()
    assert channel.encrypted_settings["api_key_hashes"] == old_hashes
