"""Tests for the CardDAV endpoint (auth, discovery, reports, read/write)."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument,too-many-lines

import base64
import uuid
from datetime import timedelta
from xml.etree import ElementTree as ET

from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

import pytest

from core import factories, models
from core.enums import ChannelScope
from core.services.vcf_service import parse_vcf

from carddav import storage, views
from carddav.models import CardName, ContactChange
from carddav.throttling import CardDAVAuthFailureThrottle, CardDAVRequestThrottle
from carddav.xml import CARD, CS, D

pytestmark = pytest.mark.django_db

ROOT = "/carddav/"
PERSONAL = "/carddav/addressbooks/personal/"
DIRECTORY = "/carddav/addressbooks/directory/"
CARD_NS = CARD("x")[1:-2]

VCARD = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\n"
    "N:Lovelace;Ada;;;\r\nEMAIL;TYPE=WORK:ada@example.org\r\nEND:VCARD\r\n"
)
# No FN nor N, and a phone rather than an email that FN would fall back to.
NAMELESS_VCARD = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:\r\nN:;;;;\r\nTEL:+33199000405\r\nEND:VCARD\r\n"
)


@pytest.fixture
def user():
    return factories.UserFactory(email="user@example.org")


def basic(email, password):
    token = base64.b64encode(f"{email}:{password}".encode()).decode()
    return {"HTTP_AUTHORIZATION": f"Basic {token}"}


def make_channel(user, scopes=(ChannelScope.CARDDAV_WRITE.value,), groups=False):
    """A CardDAV channel plus the Basic Auth header it implies.

    The full password is kept on ``channel.password`` for tests that
    present it under another username.
    """
    settings = {"scopes": list(scopes)}
    if groups:
        settings["groups"] = True
    channel = models.Channel(
        name="Phone",
        type="carddav",
        scope_level="user",
        user=user,
        organization=user.organization,
        settings=settings,
    )
    raw_key = channel.generate_api_key()
    channel.save()
    channel.password = f"{channel.pk.hex}{raw_key}"
    return channel, basic(user.email, channel.password)


BAD_PASSWORD = f"{'0' * 32}nope"


@pytest.fixture
def auth(user):
    _channel, headers = make_channel(user)
    return headers


def dav(  # noqa: PLR0913  # pylint: disable=too-many-arguments
    client, method, path, *, headers=None, body=None, depth=None, extra=None
):
    kwargs = dict(headers or {})
    if depth is not None:
        kwargs["HTTP_DEPTH"] = depth
    kwargs.update(extra or {})
    if isinstance(body, str):
        body = body.encode()
    return client.generic(
        method, path, data=body or b"", content_type="application/xml", **kwargs
    )


def hrefs(response):
    root = ET.fromstring(response.content)
    return [el.text for el in root.iter(D("href"))]


def conditions(response):
    """The precondition tags of a ``DAV:error`` body."""
    return [child.tag for child in ET.fromstring(response.content)]


def put(auth, path, body, **extra):
    return dav(Client(), "PUT", path, headers=auth, body=body, extra=extra)


def get(auth, path, **extra):
    return dav(Client(), "GET", path, headers=auth, extra=extra)


def row(contact):
    """Every stored column of *contact*, freshly read."""
    return models.Contact.objects.filter(pk=contact.pk).values().get()


# --- authentication --------------------------------------------------


def test_options_advertises_addressbook():
    response = Client().options(ROOT)
    assert response.status_code == 200
    assert "addressbook" in response["DAV"]


@pytest.mark.parametrize(
    "method,path",
    [
        ("OPTIONS", ROOT),
        ("PROPFIND", ROOT),
        ("PROPFIND", PERSONAL),
        ("PROPFIND", "/.well-known/carddav"),
    ],
)
def test_feature_disabled_answers_404(auth, settings, method, path):
    """With FEATURE_CARDDAV off, even valid credentials hit a 404."""
    settings.FEATURE_CARDDAV = False
    response = dav(Client(), method, path, headers=auth)
    assert response.status_code == 404
    assert "WWW-Authenticate" not in response
    assert "DAV" not in response


def test_unauthenticated_gets_challenge():
    response = dav(Client(), "PROPFIND", ROOT)
    assert response.status_code == 401
    assert response["WWW-Authenticate"].startswith("Basic ")


def test_wrong_key_rejected(user):
    channel, _ = make_channel(user)
    headers = basic(user.email, f"{channel.pk.hex}not-the-key")
    assert dav(Client(), "PROPFIND", ROOT, headers=headers).status_code == 401


def test_email_must_match_channel_user(user):
    """A valid key presented under someone else's address is refused."""
    channel, _ = make_channel(user)
    headers = basic("someone.else@example.org", channel.password)
    response = dav(Client(), "PROPFIND", ROOT, headers=headers)
    assert response.status_code == 403


def test_session_login_is_not_a_carddav_credential(user):
    """A browser session would skip the entitlement and scope checks."""
    client = Client()
    client.force_login(user)
    assert dav(client, "PROPFIND", ROOT).status_code == 401


def test_rest_channel_cannot_be_used_for_carddav(user):
    """A contacts:read REST key is not a CardDAV password."""
    channel = factories.ChannelFactory(
        user=user, settings={"scopes": ["contacts:read"]}
    )
    headers = basic(user.email, f"{channel.pk.hex}{channel.raw_api_key}")
    assert dav(Client(), "PROPFIND", ROOT, headers=headers).status_code == 401


def test_read_only_channel_cannot_write(user):
    _channel, headers = make_channel(user, scopes=[ChannelScope.CARDDAV_READ.value])
    response = put(headers, f"{PERSONAL}{uuid.uuid4()}.vcf", VCARD)
    assert response.status_code == 403


def test_expired_channel_rejected(user):
    channel, headers = make_channel(user)
    models.Channel.objects.filter(pk=channel.pk).update(
        settings={
            "scopes": [ChannelScope.CARDDAV_WRITE.value],
            "expires_at": (timezone.now() - timedelta(hours=1)).isoformat(),
        }
    )
    assert dav(Client(), "PROPFIND", ROOT, headers=headers).status_code == 401


def test_deactivated_user_loses_carddav_access(user):
    """Deactivating an account must revoke its device sync passwords."""
    _channel, headers = make_channel(user)
    assert dav(Client(), "PROPFIND", ROOT, headers=headers).status_code == 207
    models.User.objects.filter(pk=user.pk).update(is_active=False)
    assert dav(Client(), "PROPFIND", ROOT, headers=headers).status_code == 403


def test_carddav_touches_last_used_at(user):
    channel, headers = make_channel(user)
    assert channel.last_used_at is None
    assert dav(Client(), "PROPFIND", ROOT, headers=headers).status_code == 207
    channel.refresh_from_db()
    assert channel.last_used_at is not None


# --- rate limits -----------------------------------------------------


@pytest.fixture
def failure_budget(monkeypatch):
    """Set the failure budget. DRF binds THROTTLE_RATES at import, so the
    table itself is patched rather than the settings."""

    def set_rate(rate):
        monkeypatch.setitem(
            CardDAVAuthFailureThrottle.THROTTLE_RATES, "carddav_auth", rate
        )
        cache.clear()

    return set_rate


def test_repeated_auth_failures_are_throttled(user, failure_budget):
    failure_budget("3/minute")
    headers = basic(user.email, BAD_PASSWORD)
    responses = [dav(Client(), "PROPFIND", ROOT, headers=headers) for _ in range(5)]
    assert [r.status_code for r in responses[:3]] == [401, 401, 401]
    assert responses[3].status_code == 429
    assert 0 < int(responses[3]["Retry-After"]) <= 60


def test_missing_credentials_are_not_failures(user, failure_budget):
    """Clients probe without credentials first; that is not a guess."""
    failure_budget("3/minute")
    for _ in range(10):
        assert dav(Client(), "PROPFIND", ROOT).status_code == 401
    headers = basic(user.email, BAD_PASSWORD)
    assert dav(Client(), "PROPFIND", ROOT, headers=headers).status_code == 401


def test_exhausted_auth_budget_still_admits_valid_credentials(user, failure_budget):
    """A guesser behind a shared address must not lock the others out."""
    failure_budget("2/minute")
    _channel, good = make_channel(user)
    bad = basic(user.email, BAD_PASSWORD)
    for _ in range(2):
        assert dav(Client(), "PROPFIND", ROOT, headers=bad).status_code == 401
    assert dav(Client(), "PROPFIND", ROOT, headers=bad).status_code == 429
    assert dav(Client(), "PROPFIND", ROOT, headers=good).status_code == 207


def test_forwarded_for_does_not_split_the_failure_budget(user, failure_budget):
    """Without a trusted proxy (NUM_PROXIES=0) the header is ignored."""
    failure_budget("2/minute")
    bad = basic(user.email, BAD_PASSWORD)
    codes = [
        dav(
            Client(),
            "PROPFIND",
            ROOT,
            headers=bad,
            extra={"HTTP_X_FORWARDED_FOR": f"203.0.113.{index}"},
        ).status_code
        for index in range(3)
    ]
    assert codes == [401, 401, 429]


def test_authorization_failures_do_not_consume_the_auth_budget(user, failure_budget):
    """A valid key acting for the wrong user is not a guessing attempt."""
    failure_budget("3/minute")
    channel, _ = make_channel(user)
    wrong_user = basic("nobody@example.org", channel.password)
    codes = [
        dav(Client(), "PROPFIND", ROOT, headers=wrong_user).status_code
        for _ in range(6)
    ]
    assert set(codes) == {403}

    # The budget is untouched: a bad key still gets its three tries.
    bad = basic(user.email, BAD_PASSWORD)
    codes = [dav(Client(), "PROPFIND", ROOT, headers=bad).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]


def test_successful_requests_are_throttled_per_credential(user, monkeypatch):
    monkeypatch.setitem(CardDAVRequestThrottle.THROTTLE_RATES, "carddav", "2/minute")
    cache.clear()
    _channel, headers = make_channel(user)
    responses = [dav(Client(), "PROPFIND", ROOT, headers=headers) for _ in range(3)]
    assert [r.status_code for r in responses] == [207, 207, 429]
    assert 0 < int(responses[2]["Retry-After"]) <= 60


# --- discovery -------------------------------------------------------


def test_root_propfind_exposes_principal(auth, user):
    response = dav(Client(), "PROPFIND", ROOT, headers=auth, depth="0")
    assert response.status_code == 207
    assert f"/carddav/principals/{user.email}/" in hrefs(response)


def test_principal_exposes_addressbook_home(auth, user):
    response = dav(
        Client(), "PROPFIND", f"/carddav/principals/{user.email}/", headers=auth
    )
    assert response.status_code == 207
    root = ET.fromstring(response.content)
    assert root.find(f".//{CARD('addressbook-home-set')}") is not None


def test_principal_href_is_quoted():
    """An address with URL delimiters must not break the href."""
    odd = factories.UserFactory(email="a#b?c@example.org")
    _channel, headers = make_channel(odd)
    response = dav(Client(), "PROPFIND", ROOT, headers=headers)
    href = "/carddav/principals/a%23b%3Fc@example.org/"
    assert href in hrefs(response)
    assert dav(Client(), "PROPFIND", href, headers=headers).status_code == 207


def test_someone_elses_principal_is_404(auth):
    path = "/carddav/principals/other@example.org/"
    assert dav(Client(), "PROPFIND", path, headers=auth).status_code == 404


def test_principals_collection_describes_itself(auth):
    response = dav(Client(), "PROPFIND", "/carddav/principals/", headers=auth)
    root = ET.fromstring(response.content)
    assert root.find(f"{D('response')}/{D('href')}").text == "/carddav/principals/"


def test_home_lists_both_address_books(auth):
    response = dav(
        Client(), "PROPFIND", "/carddav/addressbooks/", headers=auth, depth="1"
    )
    assert response.status_code == 207
    found = hrefs(response)
    assert PERSONAL in found
    assert DIRECTORY in found


def test_unknown_address_book_is_404(auth):
    response = dav(Client(), "PROPFIND", "/carddav/addressbooks/nope/", headers=auth)
    assert response.status_code == 404


def test_depth_infinity_is_refused(auth):
    response = dav(Client(), "PROPFIND", PERSONAL, headers=auth, depth="infinity")
    assert response.status_code == 403
    assert conditions(response) == [D("propfind-finite-depth")]


def test_propname_lists_property_names(auth):
    body = '<d:propfind xmlns:d="DAV:"><d:propname/></d:propfind>'
    response = dav(Client(), "PROPFIND", PERSONAL, headers=auth, body=body)
    assert response.status_code == 207
    ctag = ET.fromstring(response.content).find(f".//{CS('getctag')}")
    assert ctag is not None
    assert ctag.text is None


def test_supported_address_data_is_what_is_served(auth):
    body = (
        f'<d:propfind xmlns:d="DAV:" xmlns:card="{CARD_NS}"><d:prop>'
        "<card:supported-address-data/></d:prop></d:propfind>"
    )
    response = dav(Client(), "PROPFIND", PERSONAL, headers=auth, body=body)
    types = ET.fromstring(response.content).iter(CARD("address-data-type"))
    assert [el.get("version") for el in types] == ["3.0"]


# --- reading ---------------------------------------------------------


def test_personal_book_lists_own_contacts(auth, user):
    contact = factories.ContactFactory(owner=user, full_name="Ada Lovelace")
    response = dav(Client(), "PROPFIND", PERSONAL, headers=auth, depth="1")
    assert f"{PERSONAL}{contact.id}.vcf" in hrefs(response)


def test_directory_book_holds_org_rows_not_personal(auth, user):
    org_contact = factories.ContactFactory(
        owner=None, organization=user.organization, full_name="Org Desk"
    )
    mine = factories.ContactFactory(owner=user, full_name="Mine")
    found = hrefs(dav(Client(), "PROPFIND", DIRECTORY, headers=auth, depth="1"))
    assert f"{DIRECTORY}{org_contact.id}.vcf" in found
    assert f"{DIRECTORY}{mine.id}.vcf" not in found


def test_other_users_contacts_are_invisible(auth, user):
    stranger = factories.ContactFactory(full_name="Not Mine")
    assert get(auth, f"{PERSONAL}{stranger.id}.vcf").status_code == 404


def test_get_returns_vcard_with_etag(auth, user):
    contact = factories.ContactFactory(owner=user, full_name="Ada Lovelace")
    response = get(auth, f"{PERSONAL}{contact.id}.vcf")
    assert response.status_code == 200
    assert "BEGIN:VCARD" in response.content.decode()
    assert response["ETag"]


def test_get_serves_a_uid(auth, user):
    """RFC 6352 5.1: every card carries a UID, stable across reads."""
    contact = factories.ContactFactory(owner=user, full_name="Ada Lovelace")
    body = get(auth, f"{PERSONAL}{contact.id}.vcf").content.decode()
    assert f"\r\nUID:{contact.pk}\r\n" in body


def test_get_if_none_match_is_304(auth, user):
    contact = factories.ContactFactory(owner=user, full_name="Ada Lovelace")
    url = f"{PERSONAL}{contact.id}.vcf"
    etag = get(auth, url)["ETag"]
    response = get(auth, url, HTTP_IF_NONE_MATCH=etag)
    assert response.status_code == 304
    assert response["ETag"] == etag
    assert get(auth, url, HTTP_IF_NONE_MATCH='"other"').status_code == 200


def test_head_reports_the_get_length(auth, user):
    contact = factories.ContactFactory(owner=user, full_name="Ada Lovelace")
    url = f"{PERSONAL}{contact.id}.vcf"
    length = len(get(auth, url).content)
    response = dav(Client(), "HEAD", url, headers=auth)
    assert response.status_code == 200
    assert response["Content-Length"] == str(length)
    assert response.content == b""


def test_etag_changes_when_contact_changes(auth, user):
    contact = factories.ContactFactory(owner=user, full_name="Ada")
    first = get(auth, f"{PERSONAL}{contact.id}.vcf")["ETag"]
    contact.full_name = "Ada L"
    contact.save()
    second = get(auth, f"{PERSONAL}{contact.id}.vcf")["ETag"]
    assert first != second


def test_card_path_with_extra_segments_is_404(auth, user):
    contact = factories.ContactFactory(owner=user, full_name="Ada")
    assert get(auth, f"{PERSONAL}{contact.id}.vcf/extra").status_code == 404


# --- reports ---------------------------------------------------------


def multiget(auth, book, *card_hrefs):
    body = (
        f'<card:addressbook-multiget xmlns:d="DAV:" xmlns:card="{CARD_NS}">'
        "<d:prop><d:getetag/><card:address-data/></d:prop>"
        + "".join(f"<d:href>{href}</d:href>" for href in card_hrefs)
        + "</card:addressbook-multiget>"
    )
    return dav(Client(), "REPORT", book, headers=auth, body=body)


def test_multiget_returns_requested_cards(auth, user):
    contact = factories.ContactFactory(owner=user, full_name="Ada")
    response = multiget(auth, PERSONAL, f"{PERSONAL}{contact.id}.vcf")
    assert response.status_code == 207
    assert "BEGIN:VCARD" in response.content.decode()


def test_multiget_query_count_does_not_grow_with_hrefs(auth, user):
    contacts = [factories.ContactFactory(owner=user) for _ in range(10)]
    card_hrefs = [f"{PERSONAL}{contact.id}.vcf" for contact in contacts]

    def count(*requested):
        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            assert multiget(auth, PERSONAL, *requested).status_code == 207
        return len(queries)

    count(card_hrefs[0])  # the first request also stamps last_used_at
    assert count(*card_hrefs) == count(card_hrefs[0])


def test_multiget_is_capped(auth, user, monkeypatch):
    monkeypatch.setattr(views, "MULTIGET_MAX_HREFS", 2)
    response = multiget(auth, PERSONAL, *[f"{PERSONAL}{uuid.uuid4()}.vcf"] * 3)
    assert response.status_code == 403
    assert conditions(response) == [D("number-of-matches-within-limits")]


def query(auth, filter_xml, limit=None):
    limit_xml = (
        f"<card:limit><card:nresults>{limit}</card:nresults></card:limit>"
        if limit is not None
        else ""
    )
    body = (
        f'<card:addressbook-query xmlns:d="DAV:" xmlns:card="{CARD_NS}">'
        f"<d:prop><d:getetag/></d:prop>{filter_xml}{limit_xml}"
        "</card:addressbook-query>"
    )
    return dav(Client(), "REPORT", PERSONAL, headers=auth, body=body)


@pytest.fixture
def ada_and_grace(user):
    ada = factories.ContactFactory(
        owner=user,
        full_name="Ada Lovelace",
        emails=[{"type": "work", "value": "ada@example.org"}],
    )
    grace = factories.ContactFactory(
        owner=user,
        full_name="Grace Hopper",
        emails=[],
        phones=[{"type": "cell", "value": "+33 6 39 98 56 78"}],
    )
    return f"{PERSONAL}{ada.id}.vcf", f"{PERSONAL}{grace.id}.vcf"


def test_query_without_filter_returns_collection(auth, ada_and_grace):
    assert sorted(hrefs(query(auth, ""))) == sorted(ada_and_grace)


@pytest.mark.parametrize(
    ("filter_xml", "expected"),
    [
        (
            '<card:filter><card:prop-filter name="FN">'
            "<card:text-match>ada</card:text-match></card:prop-filter></card:filter>",
            ["ada"],
        ),
        (
            '<card:filter><card:prop-filter name="EMAIL"><card:text-match '
            'match-type="equals">ADA@example.org</card:text-match>'
            "</card:prop-filter></card:filter>",
            ["ada"],
        ),
        (
            '<card:filter><card:prop-filter name="TEL"><card:text-match '
            'match-type="starts-with">+33 6</card:text-match>'
            "</card:prop-filter></card:filter>",
            ["grace"],
        ),
        (
            '<card:filter><card:prop-filter name="N"><card:text-match '
            'match-type="starts-with">Hopper;</card:text-match>'
            "</card:prop-filter></card:filter>",
            [],
        ),
        (
            '<card:filter test="anyof">'
            '<card:prop-filter name="FN"><card:text-match>hopper</card:text-match>'
            "</card:prop-filter>"
            '<card:prop-filter name="EMAIL"><card:text-match>ada@</card:text-match>'
            "</card:prop-filter></card:filter>",
            ["ada", "grace"],
        ),
        (
            '<card:filter test="allof">'
            '<card:prop-filter name="FN"><card:text-match>hopper</card:text-match>'
            "</card:prop-filter>"
            '<card:prop-filter name="EMAIL"><card:text-match>ada@</card:text-match>'
            "</card:prop-filter></card:filter>",
            [],
        ),
        (
            '<card:filter><card:prop-filter name="FN"><card:text-match '
            'negate-condition="yes">ada</card:text-match></card:prop-filter>'
            "</card:filter>",
            ["grace"],
        ),
        (
            '<card:filter><card:prop-filter name="EMAIL"><card:is-not-defined/>'
            "</card:prop-filter></card:filter>",
            ["grace"],
        ),
        (
            '<card:filter><card:prop-filter name="FN"><card:text-match '
            'collation="i;octet">ada</card:text-match></card:prop-filter>'
            "</card:filter>",
            [],
        ),
    ],
)
def test_query_evaluates_the_filter(auth, ada_and_grace, filter_xml, expected):
    """RFC 6352 10.5: only matching cards, never a superset."""
    by_name = dict(zip(("ada", "grace"), ada_and_grace, strict=True))
    response = query(auth, filter_xml)
    assert response.status_code == 207
    assert sorted(hrefs(response)) == sorted(by_name[name] for name in expected)


@pytest.mark.parametrize(
    ("filter_xml", "condition"),
    [
        (
            '<card:filter><card:prop-filter name="ADR"/></card:filter>',
            CARD("supported-filter"),
        ),
        (
            '<card:filter><card:prop-filter name="EMAIL">'
            '<card:param-filter name="TYPE"/></card:prop-filter></card:filter>',
            CARD("supported-filter"),
        ),
        (
            '<card:filter><card:prop-filter name="FN"><card:text-match '
            'collation="i;unknown">x</card:text-match></card:prop-filter>'
            "</card:filter>",
            CARD("supported-collation"),
        ),
    ],
)
def test_query_refuses_what_it_cannot_evaluate(auth, filter_xml, condition):
    response = query(auth, filter_xml)
    assert response.status_code == 403
    assert conditions(response) == [condition]


def test_query_limit_truncates_with_507(auth, ada_and_grace):
    response = query(auth, "", limit=1)
    root = ET.fromstring(response.content)
    statuses = [el.text for el in root.iter(D("status"))]
    assert "HTTP/1.1 507 Insufficient Storage" in statuses
    assert len([href for href in hrefs(response) if href.endswith(".vcf")]) == 1


def sync(auth, book, token=None):
    """Run a sync-collection REPORT. Returns ``(response, next token)``."""
    inner = f"<d:sync-token>{token}</d:sync-token>" if token else "<d:sync-token/>"
    body = (
        f'<d:sync-collection xmlns:d="DAV:">{inner}'
        f"<d:prop><d:getetag/></d:prop></d:sync-collection>"
    )
    response = dav(Client(), "REPORT", book, headers=auth, body=body)
    if response.status_code != 207:
        return response, None
    return response, ET.fromstring(response.content).find(D("sync-token")).text


def ctag(auth, book=PERSONAL):
    response = dav(Client(), "PROPFIND", book, headers=auth, depth="0")
    return ET.fromstring(response.content).find(f".//{D('sync-token')}").text


def test_sync_collection_reports_changes_and_removals(auth, user):
    kept = factories.ContactFactory(owner=user, full_name="Kept")
    first, token = sync(auth, PERSONAL)
    assert first.status_code == 207

    added = factories.ContactFactory(owner=user, full_name="Added")
    kept.deleted_at = timezone.now()
    kept.save()

    second, _ = sync(auth, PERSONAL, token)
    found = hrefs(second)
    assert f"{PERSONAL}{added.id}.vcf" in found
    assert f"{PERSONAL}{kept.id}.vcf" in found  # reported as removed (404)


def test_sync_collection_catches_writes_in_the_same_microsecond(
    auth, user, monkeypatch
):
    """A delta must not depend on rows landing on distinct clock ticks."""
    frozen = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: frozen)

    factories.ContactFactory(owner=user, full_name="Before")
    _first, token = sync(auth, PERSONAL)
    added = factories.ContactFactory(owner=user, full_name="Added")

    second, _ = sync(auth, PERSONAL, token)
    assert f"{PERSONAL}{added.id}.vcf" in hrefs(second)


def test_sync_collection_survives_a_clock_going_backwards(auth, user, monkeypatch):
    """A backwards clock adjustment must not swallow a write either."""
    _first, token = sync(auth, PERSONAL)

    past = timezone.now() - timedelta(minutes=5)
    monkeypatch.setattr(timezone, "now", lambda: past)
    added = factories.ContactFactory(owner=user, full_name="Added")
    monkeypatch.undo()

    second, _ = sync(auth, PERSONAL, token)
    assert f"{PERSONAL}{added.id}.vcf" in hrefs(second)


def test_a_write_during_a_sync_is_left_for_the_next_one(auth, user, monkeypatch):
    """The token handed back must not cover a write the delta did not read.

    The write is staged after the high-water mark is taken and before the
    journal is read: a token computed afterwards would jump over it.
    """
    factories.ContactFactory(owner=user, full_name="Before")
    _first, token = sync(auth, PERSONAL)
    edited = factories.ContactFactory(owner=user, full_name="Edited")
    late = {}
    original = storage.changes_since

    def changes_with_a_concurrent_write(*args):
        late["contact"] = factories.ContactFactory(owner=user, full_name="Late")
        return original(*args)

    monkeypatch.setattr(storage, "changes_since", changes_with_a_concurrent_write)
    second, token = sync(auth, PERSONAL, token)
    monkeypatch.undo()
    assert f"{PERSONAL}{edited.id}.vcf" in hrefs(second)

    third, _ = sync(auth, PERSONAL, token)
    assert f"{PERSONAL}{late['contact'].id}.vcf" in hrefs(third)


def test_sync_token_carries_no_clock(auth, user, monkeypatch):
    """An unchanged collection keeps its ctag however much time passes."""
    factories.ContactFactory(owner=user, full_name="Ada")
    before = ctag(auth)
    later = timezone.now() + timedelta(hours=1)
    monkeypatch.setattr(timezone, "now", lambda: later)
    assert ctag(auth) == before
    _response, token = sync(auth, PERSONAL, before)
    assert token == before


def test_stale_sync_token_forces_resync(auth, user):
    """A token older than a purged tombstone of its book may miss a removal."""
    gone = factories.ContactFactory(owner=user)
    _first, token = sync(auth, PERSONAL)
    gone_id = gone.pk
    gone.delete()
    ContactChange.objects.filter(contact_id=gone_id).update(
        updated_at=timezone.now() - timedelta(days=400)
    )
    call_command("purge_carddav_changes")

    response, _ = sync(auth, PERSONAL, token)
    assert response.status_code == 403
    assert conditions(response) == [D("valid-sync-token")]

    # A full resync hands out a token the purge no longer invalidates.
    _full, fresh = sync(auth, PERSONAL)
    assert sync(auth, PERSONAL, fresh)[0].status_code == 207


def test_a_colleagues_purge_keeps_my_token_valid(auth, user):
    _first, token = sync(auth, PERSONAL)
    colleague = factories.UserFactory(organization=user.organization)
    theirs = factories.ContactFactory(owner=colleague)
    theirs_id = theirs.pk
    theirs.delete()
    ContactChange.objects.filter(contact_id=theirs_id).update(
        updated_at=timezone.now() - timedelta(days=400)
    )
    call_command("purge_carddav_changes")

    response, next_token = sync(auth, PERSONAL, token)
    assert response.status_code == 207
    assert next_token == token


def test_sync_token_never_moves_backwards_after_a_prune(auth, user):
    """Pruning the last tombstone in a book must not rewind its ctag."""
    gone = factories.ContactFactory(owner=user)
    gone_id = gone.pk
    gone.delete()
    _first, token = sync(auth, PERSONAL)

    ContactChange.objects.filter(contact_id=gone_id).update(
        updated_at=timezone.now() - timedelta(days=400)
    )
    call_command("purge_carddav_changes")
    assert not ContactChange.objects.exists()

    second, next_token = sync(auth, PERSONAL, token)
    assert second.status_code == 207
    assert next_token == token
    assert ctag(auth) == token


def test_sync_token_is_not_bumped_by_a_colleagues_contacts(auth, user):
    """A colleague's own address book is not a reason to re-read this one."""
    _first, token = sync(auth, PERSONAL)
    colleague = factories.UserFactory(organization=user.organization)
    theirs = factories.ContactFactory(owner=colleague, full_name="Theirs")
    theirs.deleted_at = timezone.now()
    theirs.save()
    theirs.delete()

    second, next_token = sync(auth, PERSONAL, token)
    assert hrefs(second) == []
    assert next_token == token


def test_malformed_sync_tokens_are_refused_not_crashed(auth, user):
    """A client-supplied number reaches a bigint column; bound it."""
    factories.ContactFactory(owner=user)
    organization = user.organization_id
    for token in (
        "urn:contacts:sync:",
        f"urn:contacts:sync:{organization}:",
        f"urn:contacts:sync:{organization}:not-a-number",
        f"urn:contacts:sync:{organization}:{2**70}",
        f"urn:contacts:sync:{organization}:-1",
        f"urn:contacts:sync:{organization}:1757900000.123",
    ):
        response, _ = sync(auth, PERSONAL, token)
        assert response.status_code == 403, token
        assert conditions(response) == [D("valid-sync-token")]


def test_a_token_from_another_organization_is_refused(auth, user):
    factories.ContactFactory(owner=user)
    _first, token = sync(auth, PERSONAL)
    foreign = token.replace(str(user.organization_id), str(uuid.uuid4()))
    response, _ = sync(auth, PERSONAL, foreign)
    assert response.status_code == 403


def test_a_token_ahead_of_the_counter_is_refused(auth, user):
    """This server never issued it; answering would skip real changes."""
    factories.ContactFactory(owner=user)
    _first, token = sync(auth, PERSONAL)
    prefix, _, seq = token.rpartition(":")
    response, _ = sync(auth, PERSONAL, f"{prefix}:{int(seq) + 1}")
    assert response.status_code == 403


def test_collection_token_changes_when_parent_changes(auth, user):
    parent, _overlay = _overlay_of(
        user, full_name="Directory Name", company="Brigny Agglo"
    )
    before = ctag(auth)
    parent.company = "ANCT"
    parent.save()
    assert ctag(auth) != before


# --- writing ---------------------------------------------------------


def test_put_creates_contact(auth, user):
    name = f"{uuid.uuid4()}.vcf"
    response = put(auth, f"{PERSONAL}{name}", VCARD)
    assert response.status_code == 201, response.content
    contact = models.Contact.objects.get(owner=user)
    assert contact.display_name == "Ada Lovelace"
    assert contact.emails[0]["value"] == "ada@example.org"
    # Derived columns must be maintained exactly as the REST path does.
    assert "ada" in contact.search_text
    assert get(auth, f"{PERSONAL}{name}").status_code == 200


def test_put_keeps_the_client_href(auth, user):
    """Apple names cards with an upper-case UUID and expects it back."""
    name = f"{str(uuid.uuid4()).upper()}.vcf"
    assert put(auth, f"{PERSONAL}{name}", VCARD).status_code == 201

    listed = hrefs(dav(Client(), "PROPFIND", PERSONAL, headers=auth, depth="1"))
    assert f"{PERSONAL}{name}" in listed
    assert get(auth, f"{PERSONAL}{name}").status_code == 200
    assert get(auth, f"{PERSONAL}{name.lower()}").status_code == 404

    response = multiget(auth, PERSONAL, f"{PERSONAL}{name}")
    assert hrefs(response) == [f"{PERSONAL}{name}"]
    assert "BEGIN:VCARD" in response.content.decode()


def test_put_accepts_a_non_uuid_href(auth, user):
    """DAVx5 names a new card after its UID, which is any string."""
    response = put(auth, f"{PERSONAL}some%20name.vcf", VCARD)
    assert response.status_code == 201
    listed = hrefs(dav(Client(), "PROPFIND", PERSONAL, headers=auth, depth="1"))
    assert f"{PERSONAL}some%20name.vcf" in listed


def test_put_updates_existing(auth, user):
    contact = factories.ContactFactory(owner=user, full_name="Old")
    response = put(auth, f"{PERSONAL}{contact.id}.vcf", VCARD)
    assert response.status_code == 204
    contact.refresh_from_db()
    assert contact.display_name == "Ada Lovelace"


def test_put_on_an_id_owned_elsewhere_creates_a_separate_card(auth, user):
    """A client-chosen name may be the id of a row it cannot see."""
    hidden = factories.ContactFactory(
        owner=None, organization=user.organization, full_name="Org Desk"
    )
    response = put(auth, f"{PERSONAL}{hidden.id}.vcf", VCARD)
    assert response.status_code == 201
    hidden.refresh_from_db()
    assert hidden.display_name == "Org Desk"  # untouched
    created = models.Contact.objects.get(owner=user)
    assert created.pk != hidden.pk
    assert get(auth, f"{PERSONAL}{hidden.id}.vcf").status_code == 200


def test_put_into_directory_is_forbidden(auth, user):
    response = put(auth, f"{DIRECTORY}{uuid.uuid4()}.vcf", VCARD)
    assert response.status_code == 403


def test_cannot_write_org_contact(auth, user):
    org_contact = factories.ContactFactory(
        owner=None, organization=user.organization, full_name="Org Desk"
    )
    response = put(auth, f"{DIRECTORY}{org_contact.id}.vcf", VCARD)
    assert response.status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            VCARD.replace("FN:Ada", f"TITLE:{'x' * 300}\r\nFN:Ada"), id="long"
        ),
        pytest.param(VCARD.replace("END:VCARD\r\n", ""), id="unterminated"),
        pytest.param(VCARD + VCARD, id="two-cards"),
        pytest.param(NAMELESS_VCARD, id="no-names"),
        pytest.param(
            VCARD.replace("EMAIL;TYPE=WORK:ada@example.org\r\n", ""), id="no-contact"
        ),
        pytest.param("not a vcard at all", id="garbage"),
    ],
)
def test_put_refuses_an_invalid_card(auth, user, body):
    """Refused like the REST API would, with the RFC 6352 precondition."""
    response = put(auth, f"{PERSONAL}{uuid.uuid4()}.vcf", body)
    assert response.status_code == 403
    assert conditions(response) == [CARD("valid-address-data")]
    assert not models.Contact.objects.filter(owner=user).exists()


def test_put_cannot_blank_the_required_names(auth, user):
    contact = factories.ContactFactory(
        owner=user, full_name="Ada Lovelace", given_name="Ada", family_name="Lovelace"
    )
    response = put(auth, f"{PERSONAL}{contact.id}.vcf", NAMELESS_VCARD)
    assert response.status_code == 403
    contact.refresh_from_db()
    assert contact.given_name == "Ada"


def test_put_accepts_a_card_named_by_fn_only(auth, user):
    body = VCARD.replace("N:Lovelace;Ada;;;\r\n", "").replace(
        "FN:Ada Lovelace", "FN:Madonna"
    )
    response = put(auth, f"{PERSONAL}{uuid.uuid4()}.vcf", body)
    assert response.status_code == 201, response.content
    contact = models.Contact.objects.get(owner=user)
    assert (contact.full_name, contact.given_name) == ("Madonna", "")


def test_put_over_the_size_limit_is_refused(auth, user, settings):
    settings.CONTACTS_VCF_MAX_FILE_SIZE = 64
    body = (
        f'<d:propfind xmlns:d="DAV:" xmlns:card="{CARD_NS}"><d:prop>'
        "<card:max-resource-size/></d:prop></d:propfind>"
    )
    advertised = dav(Client(), "PROPFIND", PERSONAL, headers=auth, body=body)
    assert "<card:max-resource-size>64<" in advertised.content.decode()

    response = put(auth, f"{PERSONAL}{uuid.uuid4()}.vcf", VCARD)
    assert response.status_code == 403
    assert conditions(response) == [CARD("max-resource-size")]


def test_if_match_mismatch_is_412(auth, user):
    contact = factories.ContactFactory(owner=user, full_name="Ada")
    response = put(auth, f"{PERSONAL}{contact.id}.vcf", VCARD, HTTP_IF_MATCH='"stale"')
    assert response.status_code == 412


def test_if_match_accepts_a_list(auth, user):
    contact = factories.ContactFactory(owner=user, full_name="Ada")
    url = f"{PERSONAL}{contact.id}.vcf"
    etag = get(auth, url)["ETag"]
    response = put(auth, url, VCARD, HTTP_IF_MATCH=f'"stale", {etag}')
    assert response.status_code == 204


def test_if_none_match_star_blocks_overwrite(auth, user):
    contact = factories.ContactFactory(owner=user, full_name="Ada")
    response = put(auth, f"{PERSONAL}{contact.id}.vcf", VCARD, HTTP_IF_NONE_MATCH="*")
    assert response.status_code == 412


def test_put_etag_only_when_the_stored_card_is_the_body(auth, user):
    """RFC 6352 6.3.2.3: otherwise the client has to fetch what was stored."""
    url = f"{PERSONAL}{uuid.uuid4()}.vcf"
    created = put(auth, url, VCARD)
    assert created.status_code == 201
    assert not created.has_header("ETag")  # the server added a UID

    served = get(auth, url)
    again = put(auth, url, served.content)
    assert again.status_code == 204
    assert again["ETag"] == served["ETag"]


@pytest.mark.parametrize(
    ("line", "notes", "extra"),
    [
        pytest.param(
            "NOTE;ENCODING=QUOTED-PRINTABLE:a=00b", "ab", {}, id="column-encoded"
        ),
        pytest.param(
            "X-FOO;ENCODING=QUOTED-PRINTABLE:a=00b",
            "",
            {"X-FOO": ["X-FOO:ab"]},
            id="extra-encoded",
        ),
        pytest.param("NOTE:a\0b", "ab", {}, id="column-literal"),
        pytest.param(
            "X-FOO;X-BAR=a\0b:c\0d",
            "",
            {"X-FOO": ["X-FOO;X-BAR=ab:cd"]},
            id="extra-literal",
        ),
    ],
)
def test_put_stores_a_card_without_its_nul_characters(auth, user, line, notes, extra):
    """PostgreSQL cannot store a NUL character in a text or JSON column."""
    body = VCARD.replace("END:VCARD", f"{line}\r\nEND:VCARD")

    response = put(auth, f"{PERSONAL}{uuid.uuid4()}.vcf", body)

    assert response.status_code == 201, response.content
    contact = models.Contact.objects.get(owner=user)
    assert (contact.notes, contact.vcard_extra) == (notes, extra)


def test_put_with_a_nul_character_gets_no_etag(auth, user):
    """The stored card is not the body, thus the client must read it back."""
    url = f"{PERSONAL}{uuid.uuid4()}.vcf"
    put(auth, url, VCARD)
    served = get(auth, url).content

    response = put(auth, url, served.replace(b"FN:Ada", b"FN:A\0da"))

    assert response.status_code == 204
    assert not response.has_header("ETag")
    assert get(auth, url).content == served


def test_get_then_unchanged_put_is_a_no_op(auth, user):
    contact = factories.ContactFactory(
        owner=user,
        full_name="",
        given_name="Ada",
        family_name="Lovelace",
        civility="mrs",
        department="Maths",
        emails=[{"type": "", "value": "Ada@Example.org"}],
        addresses=[{"type": "home", "value": "1 rue X\r\n75001 Paris"}],
        urls=[{"type": "blog", "value": "https://ada.example.org/a,b"}],
        photo="data:image/png;base64,iVBORw0KGgo=",
        notes="line one\nline two; with, punctuation",
    )
    before = row(contact)
    seq = ContactChange.objects.get(contact_id=contact.pk).seq
    url = f"{PERSONAL}{contact.id}.vcf"
    served = get(auth, url)

    response = put(auth, url, served.content, HTTP_IF_MATCH=served["ETag"])
    assert response.status_code == 204
    assert row(contact) == before
    assert ContactChange.objects.get(contact_id=contact.pk).seq == seq


def test_unchanged_put_on_an_overlay_keeps_its_overrides(auth, user):
    _parent, overlay = _overlay_of(
        user,
        full_name="Directory Name",
        emails=[{"type": "work", "value": "inherited@example.org"}],
        photo="https://example.org/p.png",
        urls=[{"type": "work", "value": "https://example.org"}],
    )
    before = row(overlay)
    url = f"{PERSONAL}{overlay.id}.vcf"
    response = put(auth, url, get(auth, url).content)
    assert response.status_code == 204
    assert row(overlay) == before
    assert before["overridden_fields"] == ["full_name"]


def test_put_changes_only_what_the_client_changed(auth, user):
    """What vCard does not carry (labels) is left alone by a client edit."""
    contact = factories.ContactFactory(
        owner=user,
        given_name="Ada",
        family_name="Lovelace",
        photo="data:image/png;base64,iVBORw0KGgo=",
    )
    label = models.ContactLabel.objects.create(owner=user, name="Kept")
    contact.labels.add(label)
    url = f"{PERSONAL}{contact.id}.vcf"
    body = get(auth, url).content.decode()
    body = body.replace("END:VCARD", "NOTE:called back\r\nEND:VCARD")
    assert put(auth, url, body).status_code == 204

    contact.refresh_from_db()
    assert contact.notes == "called back"
    assert list(contact.labels.all()) == [label]
    assert contact.photo == "data:image/png;base64,iVBORw0KGgo="


def test_web_photo_is_served_inline(auth, user):
    contact = factories.ContactFactory(
        owner=user, photo="data:image/jpeg;base64,/9j/4AAQ"
    )
    body = get(auth, f"{PERSONAL}{contact.id}.vcf").content.decode()
    assert "\r\nPHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQ\r\n" in body


RICH_VCARD = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:phone-uid-1\r\nFN:Dr Ada Lovelace\r\n"
    "N:Lovelace;Ada;Augusta;Dr;PhD\r\n"
    "item1.EMAIL;TYPE=INTERNET,WORK,pref:ada@example.org\r\n"
    "item1.X-ABLabel:Office\r\n"
    "ADR;TYPE=HOME:;;12 rue X;Paris;;75001;France\r\n"
    "URL;TYPE=HOME:https://ada.example.org\r\n"
    "CATEGORIES:friends,work\r\nKEY;ENCODING=b;TYPE=X509:AAECAw==\r\n"
    "ORG:Brigny Agglo;Direction;Service\r\nEND:VCARD\r\n"
)


def test_a_rich_card_survives_the_round_trip(auth, user):
    url = f"{PERSONAL}phone-uid-1.vcf"
    assert put(auth, url, RICH_VCARD).status_code == 201
    served = get(auth, url).content.decode()

    sent_lines = set(RICH_VCARD.split("\r\n"))
    assert sent_lines - {"item1.X-ABLabel:Office"} <= set(served.split("\r\n"))
    assert "item1.X-ABLABEL:Office" in served
    assert parse_vcf(served)[0] == parse_vcf(RICH_VCARD)[0]

    # Sending it back unchanged changes nothing, however many times.
    contact = models.Contact.objects.get(owner=user)
    before = row(contact)
    for _ in range(3):
        assert put(auth, url, served).status_code == 204
    assert row(contact) == before


def test_put_restores_a_trashed_card(auth, user):
    """A client re-creating a card it deleted gets the same row back."""
    url = f"{PERSONAL}Card-1.vcf"
    assert put(auth, url, VCARD).status_code == 201
    contact = models.Contact.objects.get(owner=user)
    assert dav(Client(), "DELETE", url, headers=auth).status_code == 204

    response = put(auth, url, VCARD.replace("Ada Lovelace", "Ada King"))
    assert response.status_code == 201
    contact.refresh_from_db()
    assert contact.deleted_at is None
    assert contact.full_name == "Ada King"
    assert models.Contact.objects.filter(owner=user).count() == 1


def test_a_name_freed_by_a_hard_delete_can_be_reused(auth, user):
    url = f"{PERSONAL}Card-1.vcf"
    assert put(auth, url, VCARD).status_code == 201
    models.Contact.objects.filter(owner=user).delete()
    assert put(auth, url, VCARD).status_code == 201
    contact = models.Contact.objects.get(owner=user)
    assert CardName.objects.get(owner=user, name="Card-1.vcf").contact_id == contact.pk


def test_sync_reports_a_removal_under_the_client_href(auth, user):
    url = f"{PERSONAL}Client-Name.vcf"
    assert put(auth, url, VCARD).status_code == 201
    _first, token = sync(auth, PERSONAL)
    assert dav(Client(), "DELETE", url, headers=auth).status_code == 204
    second, _ = sync(auth, PERSONAL, token)
    assert hrefs(second) == [url]


def test_delete_trashes_rather_than_erases(auth, user):
    contact = factories.ContactFactory(owner=user, full_name="Ada")
    response = dav(Client(), "DELETE", f"{PERSONAL}{contact.id}.vcf", headers=auth)
    assert response.status_code == 204
    contact.refresh_from_db()
    assert contact.deleted_at is not None


def test_delete_org_contact_forbidden(auth, user):
    org_contact = factories.ContactFactory(
        owner=None, organization=user.organization, full_name="Org Desk"
    )
    response = dav(Client(), "DELETE", f"{DIRECTORY}{org_contact.id}.vcf", headers=auth)
    assert response.status_code == 403


def test_malformed_xml_is_400(auth):
    response = dav(Client(), "PROPFIND", PERSONAL, headers=auth, body=b"<not xml")
    assert response.status_code == 400


def test_xxe_entity_is_rejected(auth):
    """defusedxml must refuse external entity declarations."""
    body = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<d:propfind xmlns:d="DAV:"><d:prop>'
        b"<d:displayname>&x;</d:displayname></d:prop></d:propfind>"
    )
    response = dav(Client(), "PROPFIND", PERSONAL, headers=auth, body=body)
    assert response.status_code == 400


# --- overlays --------------------------------------------------------


def _overlay_of(user, **parent_kwargs):
    parent = factories.ContactFactory(
        owner=None,
        organization=user.organization,
        source="directory",
        external_id="ext-1",
        **parent_kwargs,
    )
    overlay = models.Contact.objects.create(
        owner=user,
        organization=user.organization,
        overlay_of=parent,
        full_name="Renamed Locally",
        overridden_fields=["full_name"],
    )
    return parent, overlay


def test_overlay_card_inherits_parent_fields(auth, user):
    """An overlay must export the merged card, not its own empty columns."""
    _parent, overlay = _overlay_of(
        user,
        full_name="Directory Name",
        emails=[{"type": "work", "value": "inherited@example.org"}],
        company="Brigny Agglo",
    )
    body = get(auth, f"{PERSONAL}{overlay.id}.vcf").content.decode()
    assert "Renamed Locally" in body  # the override wins
    assert "inherited@example.org" in body  # inherited from the parent
    assert "Brigny Agglo" in body


def test_overlay_etag_changes_when_parent_changes(auth, user):
    parent, overlay = _overlay_of(
        user, full_name="Directory Name", company="Brigny Agglo"
    )
    url = f"{PERSONAL}{overlay.id}.vcf"
    before = get(auth, url)["ETag"]
    parent.company = "ANCT"
    parent.save()
    assert get(auth, url)["ETag"] != before


def test_put_on_overlay_records_overrides_and_keeps_inheritance(auth, user):
    """A PUT on an overlay follows the REST diff rule, field by field."""
    parent, overlay = _overlay_of(
        user,
        full_name="Directory Name",
        given_name="Ada",
        family_name="Lovelace",
        emails=[{"type": "work", "value": "inherited@example.org"}],
        company="Brigny Agglo",
    )
    card = (
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Renamed Again\r\nN:Lovelace;Ada;;;\r\n"
        "EMAIL;TYPE=WORK:local@example.org\r\nORG:Brigny Agglo\r\nEND:VCARD\r\n"
    )
    url = f"{PERSONAL}{overlay.id}.vcf"
    response = put(auth, url, card)
    assert response.status_code == 204, response.content

    overlay.refresh_from_db()
    assert "emails" in overlay.overridden_fields  # differs from the parent
    assert "company" not in overlay.overridden_fields  # unchanged: inherited
    assert overlay.company == ""
    body = get(auth, url).content.decode()
    assert "local@example.org" in body
    assert "Renamed Again" in body

    parent.company = "ANCT"
    parent.save()
    assert "ANCT" in get(auth, url).content.decode()  # still tracking the source


def test_delete_overlay_reverts_to_source(auth, user):
    """Deleting an overlay drops the local copy; the parent shows again."""
    parent, overlay = _overlay_of(user, full_name="Directory Name")
    found = hrefs(dav(Client(), "PROPFIND", DIRECTORY, headers=auth, depth="1"))
    assert f"{DIRECTORY}{parent.id}.vcf" not in found  # hidden by the overlay

    response = dav(Client(), "DELETE", f"{PERSONAL}{overlay.id}.vcf", headers=auth)
    assert response.status_code == 204
    assert not models.Contact.objects.filter(pk=overlay.pk).exists()
    found = hrefs(dav(Client(), "PROPFIND", DIRECTORY, headers=auth, depth="1"))
    assert f"{DIRECTORY}{parent.id}.vcf" in found


def test_sync_collection_removals_only_cover_visible_rows(auth, user):
    """Another user's trashed rows must not surface, not even as hrefs."""
    _first, token = sync(auth, DIRECTORY)

    colleague = factories.UserFactory(organization=user.organization)
    theirs = factories.ContactFactory(
        owner=colleague, source="directory", external_id="uid=theirs"
    )
    theirs.deleted_at = timezone.now()
    theirs.save()

    second, _ = sync(auth, DIRECTORY, token)
    assert second.status_code == 207
    assert f"{DIRECTORY}{theirs.id}.vcf" not in hrefs(second)


def test_sync_collection_reports_a_parent_edit_on_its_overlay(auth, user):
    """An overlay's card is rendered from its parent, so a parent edit
    changes a card in the personal book even though nothing there was
    written."""
    parent, overlay = _overlay_of(
        user, full_name="Directory Name", company="Brigny Agglo"
    )
    _first, token = sync(auth, PERSONAL)

    parent.company = "ANCT"
    parent.save()

    second, _ = sync(auth, PERSONAL, token)
    assert f"{PERSONAL}{overlay.id}.vcf" in hrefs(second)


def test_sync_collection_reports_both_sides_of_a_new_overlay(auth, user):
    """Overlaying a directory card hides it and adds a personal one."""
    parent = factories.ContactFactory(
        owner=None,
        organization=user.organization,
        source="directory",
        external_id="ext-9",
        full_name="Directory Name",
    )
    _first, personal_token = sync(auth, PERSONAL)
    _first, directory_token = sync(auth, DIRECTORY)

    overlay = models.Contact.objects.create(
        owner=user,
        organization=user.organization,
        overlay_of=parent,
        full_name="Renamed Locally",
        overridden_fields=["full_name"],
    )

    personal, _ = sync(auth, PERSONAL, personal_token)
    assert f"{PERSONAL}{overlay.id}.vcf" in hrefs(personal)
    directory, _ = sync(auth, DIRECTORY, directory_token)
    assert f"{DIRECTORY}{parent.id}.vcf" in hrefs(directory)  # now hidden


def test_sync_collection_reports_an_erased_overlay_as_removed(auth, user):
    """Dropping an overlay erases a row outright; the corbeille never sees
    it, so only the journal's tombstone can report the href as gone."""
    parent, overlay = _overlay_of(user, full_name="Directory Name")
    _first, personal_token = sync(auth, PERSONAL)
    _first, directory_token = sync(auth, DIRECTORY)

    response = dav(Client(), "DELETE", f"{PERSONAL}{overlay.id}.vcf", headers=auth)
    assert response.status_code == 204

    personal, _ = sync(auth, PERSONAL, personal_token)
    assert f"{PERSONAL}{overlay.id}.vcf" in hrefs(personal)  # reported as removed
    directory, _ = sync(auth, DIRECTORY, directory_token)
    assert f"{DIRECTORY}{parent.id}.vcf" in hrefs(directory)  # the source is back


# --- RFC 6764 discovery ----------------------------------------------


@pytest.mark.parametrize("method", ["GET", "PROPFIND"])
def test_well_known_redirects_to_the_service(method):
    """Clients bootstrap from a bare host name, often with PROPFIND."""
    response = dav(Client(), method, "/.well-known/carddav")

    assert response.status_code == 301
    assert response["Location"] == ROOT


def test_well_known_needs_no_credentials():
    """Discovery happens before the client has anywhere to send a key."""
    assert "WWW-Authenticate" not in dav(Client(), "PROPFIND", "/.well-known/carddav")
