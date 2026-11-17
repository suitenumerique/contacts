"""CardDAV method dispatcher.

Implements the subset of RFC 6352 / RFC 4918 / RFC 6578 that address book
clients (DAVx5, Apple Contacts, Thunderbird, Evolution) exercise:

    OPTIONS   capability advertisement
    PROPFIND  root, principal, address book home, collections, cards
    REPORT    addressbook-multiget, addressbook-query, sync-collection
    GET/HEAD/PUT/DELETE  on cards

Not implemented: LOCK/UNLOCK (clients fall back to ETag preconditions),
MKCOL (address books are derived from the data model, not user-created),
COPY/MOVE.

Writes are checked with ``Contact.is_writable`` and validated by the REST
``ContactSerializer``, without the email-or-phone rule of the web form.
The name rule is the only rule that refuses a readable card: the parser
(``parse_one_card``) sets aside each value that breaks a value rule.
Group cards (for devices set up with groups) are written as the user's
own groups, with the members their UIDs name.
"""

import logging
import uuid
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree as ET

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse, HttpResponsePermanentRedirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.api.serializers import ContactSerializer
from core.models import Contact, ContactGroup, ContactLabel
from core.services.vcf_service import (
    PENDING_MEMBERS,
    InvalidVCardError,
    parse_one_card,
)

from carddav import groups, storage
from carddav.auth import AuthError, ForbiddenError, authenticate, check_scope
from carddav.models import CardName
from carddav.query import UnsupportedFilterError, compile_filter
from carddav.throttling import (
    CardDAVAuthFailureThrottle,
    CardDAVRequestThrottle,
    retry_after,
)
from carddav.xml import (
    CARD,
    CS,
    PROPNAME,
    D,
    add_response,
    error_body,
    multistatus,
    parse_request,
    requested_props,
    to_bytes,
)

logger = logging.getLogger(__name__)

ALLOWED_METHODS = (
    "OPTIONS",
    "PROPFIND",
    "REPORT",
    "GET",
    "PUT",
    "DELETE",
    "HEAD",
)

XML_CONTENT_TYPE = 'application/xml; charset="utf-8"'
VCARD_CONTENT_TYPE = 'text/vcard; charset="utf-8"'

# Clients fetch in small batches (DAVx5 asks for 10 cards at a time).
MULTIGET_MAX_HREFS = 1000


def _multistatus_response(root):
    return HttpResponse(to_bytes(root), status=207, content_type=XML_CONTENT_TYPE)


def _error(status, *conditions):
    """A response carrying a ``DAV:error`` body with *conditions*."""
    return HttpResponse(
        error_body(*conditions), status=status, content_type=XML_CONTENT_TYPE
    )


def _invalid_card():
    return _error(403, CARD("valid-address-data"))


def _dav_headers(response):
    """Advertise the DAV capabilities clients probe for."""
    response["DAV"] = "1, 3, addressbook"
    response["Allow"] = ", ".join(ALLOWED_METHODS)
    return response


def _challenge():
    response = HttpResponse(status=401)
    response["WWW-Authenticate"] = 'Basic realm="Contacts CardDAV"'
    return response


def _too_many(throttle):
    response = HttpResponse(status=429)
    response["Retry-After"] = retry_after(throttle)
    return response


def _etag_matches(header, etag, *, weak=False):
    """Whether an If-Match / If-None-Match *header* names *etag*.

    If-None-Match uses the weak comparison (RFC 9110 section 13.1.2), so a
    ``W/`` prefix is ignored there.
    """
    for raw in header.split(","):
        candidate = raw.strip()
        if weak:
            candidate = candidate.removeprefix("W/")
        if candidate in ("*", etag):
            return True
    return False


def well_known(request):
    """RFC 6764 bootstrap: redirect ``/.well-known/carddav`` to the service.

    Answers every method, not just GET: clients discover by sending the
    request they actually want (usually PROPFIND) to the well-known path
    and following the redirect. It needs no credentials: clients bootstrap
    before they have anywhere to send them, and the response is a fixed
    path that discloses nothing.
    """
    if not settings.FEATURE_CARDDAV:
        raise Http404()
    return HttpResponsePermanentRedirect("/carddav/")


@method_decorator(csrf_exempt, name="dispatch")
class CardDAVView(View):  # pylint: disable=too-many-public-methods
    """Single entry point for every CardDAV path under the mount point."""

    http_method_names = [m.lower() for m in ALLOWED_METHODS]

    # Resolved per-request in dispatch().
    path = ""
    base = "/"
    user = None
    channel = None

    def dispatch(self, request, *args, **kwargs):
        if not settings.FEATURE_CARDDAV:
            raise Http404()

        raw_path = kwargs.get("path") or ""
        self.path = raw_path.strip("/")
        # The mount point, with trailing slash, that every href is built on.
        # Derived from the *raw* captured segment: stripping slashes first
        # would misalign the lengths and truncate the prefix.
        self.base = request.path[: len(request.path) - len(raw_path)]
        if not self.base.endswith("/"):
            self.base += "/"

        if request.method == "OPTIONS":
            return _dav_headers(HttpResponse(status=200))

        if rejection := self._authenticate(request):
            return rejection

        try:
            return super().dispatch(request, *args, **kwargs)
        except ForbiddenError as exc:
            return HttpResponse(str(exc), status=403, content_type="text/plain")
        except ValueError as exc:
            return HttpResponse(str(exc), status=400, content_type="text/plain")

    def _authenticate(self, request):
        """Resolve the acting user, or return the response that refuses it.

        Sets ``self.user``/``self.channel`` on success and returns None.
        """
        if not request.META.get("HTTP_AUTHORIZATION"):
            # Clients try without credentials first: not a failed attempt.
            return _challenge()
        try:
            self.user, self.channel = authenticate(request)
        except ForbiddenError as exc:
            # A valid credential that may not act as this user is not a
            # guessing attempt, so it does not count as a failure.
            return HttpResponse(str(exc), status=403, content_type="text/plain")
        except AuthError:
            failures = CardDAVAuthFailureThrottle()
            if failures.exhausted(request, self):
                return _too_many(failures)
            failures.record_failure(request, self)
            return _challenge()

        request.user = self.user
        throttle = CardDAVRequestThrottle(self.channel)
        if not throttle.allow_request(request, self):
            return _too_many(throttle)
        return None

    # --- path parsing -------------------------------------------------

    def _segments(self):
        return [segment for segment in self.path.split("/") if segment]

    def _book(self):
        """The address book the path is or lies in, or None."""
        segments = self._segments()
        if len(segments) >= 2 and segments[0] == "addressbooks":
            if segments[1] in storage.ADDRESS_BOOKS:
                return segments[1]
        return None

    def _card_target(self):
        """``(book, name)`` when the path addresses a card, else None."""
        segments = self._segments()
        book = self._book()
        if book is None or len(segments) != 3:
            return None
        return book, segments[2]

    def _with_groups(self):
        """Whether this device's credential asks for group cards.

        Fixed when the credential is created: a book that gained or lost
        its groups midway would not be reported by sync-collection.
        """
        return bool((self.channel.settings or {}).get("groups"))

    def _groups(self, book):
        """The group cards of *book* for this device, or None."""
        return storage.book_groups(self.user, book) if self._with_groups() else None

    def _find(self, book, name):
        """The card called *name* in *book*, or None."""
        return storage.find_card(self.user, book, name, groups=self._groups(book))

    # --- properties ---------------------------------------------------

    def _principal_id(self):
        return self.user.email or str(self.user.pk)

    def _principal_href(self):
        return f"{self.base}principals/{quote(self._principal_id(), safe='@')}/"

    def _prop_elements(self, tags, *, kind, book=None, contact=None):
        """Build (found, not_found) property elements for one resource."""
        found, missing = [], []
        for tag in tags:
            builder = PROP_BUILDERS.get(tag)
            element = (
                None if builder is None else builder(self, tag, kind, book, contact)
            )
            if element is None:
                missing.append(tag)
            else:
                found.append(element)
        return found, missing

    # -- individual property builders ---------------------------------
    #
    # Every builder takes the same (tag, kind, book, contact) signature so
    # PROP_BUILDERS can call them uniformly; most ignore some of it.
    # pylint: disable=unused-argument

    def prop_principal_href(self, tag, kind, book, contact):
        """The acting user's principal URL."""
        element = ET.Element(tag)
        ET.SubElement(element, D("href")).text = self._principal_href()
        return element

    def prop_home_set(self, tag, kind, book, contact):
        """Where this user's address books live."""
        element = ET.Element(tag)
        ET.SubElement(element, D("href")).text = f"{self.base}addressbooks/"
        return element

    def prop_resourcetype(self, tag, kind, book, contact):
        """Collection/principal/addressbook markers."""
        element = ET.Element(tag)
        if kind == "card":
            return element  # cards are non-collections: empty resourcetype
        ET.SubElement(element, D("collection"))
        if kind == "principal":
            ET.SubElement(element, D("principal"))
        if kind == "book":
            ET.SubElement(element, CARD("addressbook"))
        return element

    def prop_displayname(self, tag, kind, book, contact):
        """Human-readable name for the resource."""
        element = ET.Element(tag)
        if kind == "book":
            element.text = storage.DISPLAY_NAMES[book]
        elif kind == "principal":
            element.text = self.user.full_name or self._principal_id()
        elif kind == "card" and contact is not None:
            element.text = (
                contact.name if storage.is_group(contact) else contact.display_name
            )
        else:
            element.text = "Contacts"
        return element

    def prop_getetag(self, tag, kind, book, contact):
        """Strong validator for one card."""
        if contact is None:
            return None
        element = ET.Element(tag)
        element.text = storage.etag_for(contact)
        return element

    def prop_getcontenttype(self, tag, kind, book, contact):
        """Cards are always vCard."""
        if kind != "card":
            return None
        element = ET.Element(tag)
        element.text = "text/vcard; charset=utf-8"
        return element

    def prop_address_data(self, tag, kind, book, contact):
        """The vCard body, inlined into a REPORT."""
        if contact is None:
            return None
        element = ET.Element(tag)
        element.text = storage.render_card(self.user, book, contact)
        return element

    def prop_supported_address_data(self, tag, kind, book, contact):
        """vCard versions this book serves and accepts."""
        if kind != "book":
            return None
        element = ET.Element(tag)
        child = ET.SubElement(element, CARD("address-data-type"))
        child.set("content-type", "text/vcard")
        child.set("version", "3.0")
        return element

    def prop_max_resource_size(self, tag, kind, book, contact):
        """Largest card PUT accepts, in octets."""
        if kind != "book":
            return None
        element = ET.Element(tag)
        element.text = str(storage.max_resource_size())
        return element

    def prop_token(self, tag, kind, book, contact):
        """ctag / sync-token for the collection."""
        if kind != "book":
            return None
        element = ET.Element(tag)
        element.text = storage.make_sync_token(
            self.user, storage.book_sync_seq(self.user, book)
        )
        return element

    def prop_supported_report_set(self, tag, kind, book, contact):
        """Reports this collection implements."""
        if kind != "book":
            return None
        element = ET.Element(tag)
        for report in ("addressbook-multiget", "addressbook-query"):
            node = ET.SubElement(element, D("supported-report"))
            ET.SubElement(ET.SubElement(node, D("report")), CARD(report))
        node = ET.SubElement(element, D("supported-report"))
        ET.SubElement(ET.SubElement(node, D("report")), D("sync-collection"))
        return element

    def prop_privilege_set(self, tag, kind, book, contact):
        """What the acting user may do here."""
        if kind not in ("book", "card"):
            return None
        element = ET.Element(tag)
        privileges = ["read"]
        if book == storage.PERSONAL:
            privileges += ["write", "write-content", "bind", "unbind"]
        for name in privileges:
            node = ET.SubElement(element, D("privilege"))
            ET.SubElement(node, D(name))
        return element

    # pylint: enable=unused-argument

    DEFAULT_PROPS = {
        "root": (D("resourcetype"), D("current-user-principal"), D("displayname")),
        "principal": (
            D("resourcetype"),
            D("displayname"),
            D("principal-URL"),
            D("current-user-principal"),
            CARD("addressbook-home-set"),
        ),
        "home": (D("resourcetype"), D("displayname")),
        "book": (
            D("resourcetype"),
            D("displayname"),
            CS("getctag"),
            D("sync-token"),
            D("supported-report-set"),
            CARD("supported-address-data"),
        ),
        "card": (D("resourcetype"), D("getetag"), D("getcontenttype")),
    }

    def _describe(self, response, href, kind, tags, *, book=None, contact=None):  # noqa: PLR0913  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Append one resource's propstat to *response*."""
        if tags == PROPNAME:
            # address-data is only ever requested explicitly (RFC 6352 10.4).
            names = [tag for tag in PROP_BUILDERS if tag != CARD("address-data")]
            found, _missing = self._prop_elements(
                names, kind=kind, book=book, contact=contact
            )
            add_response(response, href, found=[ET.Element(el.tag) for el in found])
            return
        wanted = tags if tags is not None else self.DEFAULT_PROPS[kind]
        found, missing = self._prop_elements(
            wanted, kind=kind, book=book, contact=contact
        )
        add_response(response, href, found=found, not_found=missing)

    def _card_tags(self, root_el):
        tags = requested_props(root_el)
        return self.DEFAULT_PROPS["card"] if tags in (None, PROPNAME) else tags

    # --- HTTP methods -------------------------------------------------

    def http_method_not_allowed(self, request, *args, **kwargs):
        return _dav_headers(HttpResponse(status=405))

    def propfind(self, request, *args, **kwargs):
        """Describe a resource and, at Depth: 1, its children."""
        check_scope(self.channel, write=False)
        depth = request.headers.get("Depth", "0").strip().lower()
        if depth == "infinity":
            return _error(403, D("propfind-finite-depth"))
        if depth not in ("0", "1"):
            raise ValueError("Depth must be 0, 1 or infinity")
        tags = requested_props(parse_request(request.body))
        segments = self._segments()
        response = multistatus()

        if not segments or segments[0] == "principals":
            found = self._describe_principals(response, segments, tags)
        elif segments == ["addressbooks"]:
            found = self._describe_home(response, depth, tags)
        else:
            found = self._describe_book(response, segments, depth, tags)
        if not found:
            return HttpResponse(status=404)
        return _multistatus_response(response)

    def _describe_principals(self, response, segments, tags):
        """The root, the principals collection or the user's principal."""
        if len(segments) < 2:
            href = f"{self.base}principals/" if segments else self.base
            self._describe(response, href, "root", tags)
            return True
        if len(segments) == 2 and segments[1].lower() == self._principal_id().lower():
            self._describe(response, self._principal_href(), "principal", tags)
            return True
        return False

    def _describe_home(self, response, depth, tags):
        self._describe(response, f"{self.base}addressbooks/", "home", tags)
        if depth == "1":
            for book in storage.ADDRESS_BOOKS:
                href = storage.book_href(self.base, book)
                self._describe(response, href, "book", tags, book=book)
        return True

    def _describe_book(self, response, segments, depth, tags):
        """A collection (with its cards at Depth: 1) or one card in it."""
        book = self._book()
        if book is None or len(segments) > 3:
            return False
        if len(segments) == 3:
            contact = self._find(book, segments[2])
            if contact is None:
                return False
            href = storage.card_href(self.base, book, segments[2])
            self._describe(response, href, "card", tags, book=book, contact=contact)
            return True

        href = storage.book_href(self.base, book)
        self._describe(response, href, "book", tags, book=book)
        if depth == "1":
            for contact in storage.book_cards(self.user, book, self._with_groups()):
                self._describe(
                    response,
                    storage.card_href(self.base, book, storage.card_name(contact)),
                    "card",
                    tags,
                    book=book,
                    contact=contact,
                )
        return True

    def report(self, request, *args, **kwargs):
        """Dispatch the three reports address book clients use."""
        check_scope(self.channel, write=False)
        root_el = parse_request(request.body)
        if root_el is None:
            raise ValueError("REPORT requires a body")

        book = self._book()
        if book is None or len(self._segments()) != 2:
            return HttpResponse(status=404)

        name = root_el.tag
        if name == CARD("addressbook-multiget"):
            return self._multiget(root_el, book)
        if name == CARD("addressbook-query"):
            return self._query(root_el, book)
        if name == D("sync-collection"):
            return self._sync_collection(root_el, book)
        return _error(403, D("supported-report"))

    def _multiget(self, root_el, book):
        """Return the named hrefs, echoed as the client spelled them."""
        tags = self._card_tags(root_el)
        hrefs = [(el.text or "").strip() for el in root_el.findall(D("href"))]
        if len(hrefs) > MULTIGET_MAX_HREFS:
            return _error(403, D("number-of-matches-within-limits"))
        names = {
            href: unquote(urlsplit(href).path.rstrip("/").rpartition("/")[2])
            for href in hrefs
        }
        found = storage.find_cards(
            self.user, book, list(set(names.values())), groups=self._groups(book)
        )

        response = multistatus()
        for href in hrefs:
            contact = found.get(names[href])
            if contact is None:
                add_response(response, href, status="HTTP/1.1 404 Not Found")
            else:
                self._describe(response, href, "card", tags, book=book, contact=contact)
        return _multistatus_response(response)

    def _query(self, root_el, book):
        """Return the cards matching the ``<filter>`` (RFC 6352 8.6)."""
        tags = self._card_tags(root_el)
        try:
            matches = compile_filter(root_el.find(CARD("filter")))
        except UnsupportedFilterError as exc:
            return _error(403, exc.condition)
        limit = None
        if (
            nresults := root_el.find(f"{CARD('limit')}/{CARD('nresults')}")
        ) is not None:
            try:
                limit = int((nresults.text or "").strip())
            except ValueError as exc:
                raise ValueError("nresults must be an integer") from exc

        response = multistatus()
        count = 0
        for contact in storage.book_cards(self.user, book, self._with_groups()):
            if not matches(contact):
                continue
            if limit is not None and count >= limit:
                truncated = add_response(
                    response,
                    storage.book_href(self.base, book),
                    status="HTTP/1.1 507 Insufficient Storage",
                )
                ET.SubElement(
                    ET.SubElement(truncated, D("error")),
                    D("number-of-matches-within-limits"),
                )
                break
            count += 1
            self._describe(
                response,
                storage.card_href(self.base, book, storage.card_name(contact)),
                "card",
                tags,
                book=book,
                contact=contact,
            )
        return _multistatus_response(response)

    def _sync_collection(self, root_el, book):
        """RFC 6578 delta since the client's token."""
        token_el = root_el.find(D("sync-token"))
        token = (token_el.text or "").strip() if token_el is not None else ""
        tags = self._card_tags(root_el)

        since = None
        if token:
            since = storage.parse_sync_token(self.user, token)
            if since is None:
                return _error(403, D("valid-sync-token"))

        # The high-water mark is taken before reading anything, and the
        # delta is capped at it: a write landing in between is left for
        # the next sync instead of being skipped by a token read after it.
        until = storage.book_sync_seq(self.user, book)
        if since is None:
            contacts = storage.book_cards(self.user, book, self._with_groups())
            removed = []
        else:
            contacts, removed = storage.changes_since(
                self.user, book, since, until, self._with_groups()
            )
            # Checked after reading the journal: a purge that ran before
            # that read has already raised the mark.
            if since < storage.purged_seq(self.user, book):
                return _error(403, D("valid-sync-token"))

        response = multistatus()
        for contact in contacts:
            self._describe(
                response,
                storage.card_href(self.base, book, storage.card_name(contact)),
                "card",
                tags,
                book=book,
                contact=contact,
            )
        for name in storage.removed_names(self.user, book, removed):
            add_response(
                response,
                storage.card_href(self.base, book, name),
                status="HTTP/1.1 404 Not Found",
            )
        ET.SubElement(response, D("sync-token")).text = storage.make_sync_token(
            self.user, max(until, since or 0)
        )
        return _multistatus_response(response)

    def _preconditions(self, request, existing):
        """Enforce If-Match / If-None-Match, or None when satisfied."""
        etag = storage.etag_for(existing) if existing is not None else None
        if_match = request.headers.get("If-Match")
        if if_match is not None and (
            existing is None or not _etag_matches(if_match, etag)
        ):
            return HttpResponse(status=412)
        if_none_match = request.headers.get("If-None-Match")
        if (
            if_none_match is not None
            and existing is not None
            and _etag_matches(if_none_match, etag, weak=True)
        ):
            if request.method in ("GET", "HEAD"):
                response = HttpResponse(status=304)
                response["ETag"] = etag
                return response
            return HttpResponse(status=412)
        return None

    def get(self, request, *args, **kwargs):
        """Return one card's vCard body."""
        check_scope(self.channel, write=False)
        target = self._card_target()
        contact = self._find(*target) if target else None
        if contact is None:
            return HttpResponse(status=404)
        if precondition := self._preconditions(request, contact):
            return precondition
        response = HttpResponse(
            storage.render_card(self.user, target[0], contact),
            content_type=VCARD_CONTENT_TYPE,
        )
        response["ETag"] = storage.etag_for(contact)
        return response

    def head(self, request, *args, **kwargs):  # pylint: disable=method-hidden
        """Same as GET without a body (Django would otherwise alias it)."""
        response = self.get(request, *args, **kwargs)
        response["Content-Length"] = str(len(response.content))
        response.content = b""
        return response

    @transaction.atomic
    def put(self, request, *args, **kwargs):  # noqa: PLR0911  # pylint: disable=too-many-return-statements
        """Create, replace or restore a card.

        A PUT onto a card of the user's own that sits in the corbeille
        restores it with the new content: from the client's side the href
        is free, and reusing the row keeps its history and labels.
        """
        check_scope(self.channel, write=True)
        target = self._card_target()
        if target is None:
            return HttpResponse(status=404)
        book, name = target
        if book != storage.PERSONAL:
            return HttpResponse(status=403)
        if len(name) > storage.MAX_NAME_LENGTH:
            return HttpResponse("card name too long", status=403)

        body = _read_body(request)
        if isinstance(body, HttpResponse):
            return body

        existing = storage.find_card(
            self.user, book, name, storage.personal_rows(self.user), self._groups(book)
        )
        live = (
            existing
            if existing is not None and getattr(existing, "deleted_at", None) is None
            else None
        )
        if precondition := self._preconditions(request, live):
            return precondition
        if existing is not None and not existing.is_writable(self.user):
            return HttpResponse(status=403)

        parsed = self._parse_body(body, existing)
        if isinstance(parsed, HttpResponse):
            return parsed
        card = self._write(request, name, existing, *parsed)
        if isinstance(card, HttpResponse):
            return card

        response = HttpResponse(status=204 if live is not None else 201)
        card.refresh_from_db()
        vars(card).pop("card_seq", None)  # read before the write
        # RFC 6352 6.3.2.3: a strong ETag only when the stored card is
        # exactly what was sent, otherwise the client must GET it back.
        if storage.render_card(self.user, book, card).encode("utf-8") == body:
            response["ETag"] = storage.etag_for(card)
        return response

    def _write(self, request, name, existing, is_group, fields):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Create or update the card; the card, or the response refusing it."""
        if is_group:
            create, update = self._create_group, self._update_group
        else:
            create, update = self._create, self._update
        if existing is None:
            return create(request, name, fields)
        return update(request, existing, fields)

    def _parse_body(self, body, existing):
        """``(is_group, fields)`` of a PUT body, or the response refusing it."""
        try:
            is_group, fields = parse_one_card(body)
        except InvalidVCardError as exc:
            logger.info("CardDAV PUT refused an unreadable card: %s", exc)
            return _invalid_card()
        if is_group and not self._with_groups():
            logger.info("CardDAV PUT refused a group card: groups are off")
            return _invalid_card()
        if existing is not None and storage.is_group(existing) != is_group:
            # A card does not turn from a contact into a group or back.
            return _invalid_card()
        return is_group, fields

    def _validate(self, request, data, instance=None):
        """The serializer's validated data, or None when the card is refused."""
        serializer = ContactSerializer(
            instance=instance,
            data=data,
            partial=instance is not None,
            context={
                "request": request,
                "acting_user": self.user,
                # A client can hold a card without an email and a phone.
                "email_or_phone_required": False,
            },
        )
        if serializer.is_valid():
            return dict(serializer.validated_data)
        logger.info("CardDAV PUT refused an invalid card: %s", serializer.errors)
        return None

    def _name_card(self, name, card):
        """Record the client's name for *card*, or the 409 of a race."""
        try:
            with transaction.atomic():
                CardName.objects.update_or_create(
                    owner=self.user, name=name, defaults={"contact_id": card.pk}
                )
        except IntegrityError:
            # A concurrent PUT created the same name first.
            transaction.set_rollback(True)
            return HttpResponse(status=409)
        return None

    def _create(self, request, name, fields):
        values = self._validate(request, _api_fields(fields))
        if values is None:
            return _invalid_card()
        values.pop("is_org_contact", None)  # a phone writes personal cards
        contact = Contact(
            id=uuid.uuid4(),
            owner=self.user,
            organization=self.user.organization,
            created_by=self.user,
            updated_by=self.user,
            vcard_extra=fields["vcard_extra"],
            **values,
        )
        contact.save()
        if conflict := self._name_card(name, contact):
            return conflict
        if fields["label_names"]:
            contact.labels.set(ContactLabel.for_names(self.user, fields["label_names"]))
        groups.claim_pending_members(self.user, contact)
        return contact

    def _update(self, request, existing, fields):
        """Apply only what the client changed against the card it was served.

        Comparing with the parsed rendering of the row, not with the row,
        makes an unchanged PUT a no-op, keeps the overlay provenance of
        fields the client did not touch, and leaves columns vCard does not
        carry alone.
        """
        _is_group, served = parse_one_card(
            storage.render_card(self.user, storage.PERSONAL, existing)
        )
        changed = {key: value for key, value in fields.items() if served[key] != value}
        if existing.overlay_of_id:
            # An overlay renders its parent's vcard_extra; its own would
            # never be shown.
            changed.pop("vcard_extra", None)

        values = self._validate(request, _api_fields(changed), instance=existing)
        if values is None:
            return _invalid_card()
        if values and existing.overlay_of_id:
            values = existing.diff_overlay(values)
        if "vcard_extra" in changed:
            values["vcard_extra"] = changed["vcard_extra"]
        if existing.deleted_at is not None:
            values["deleted_at"] = None
        if values:
            for field, value in values.items():
                setattr(existing, field, value)
            existing.updated_by = self.user
            existing.save()
        if "label_names" in changed:
            existing.labels.set(
                ContactLabel.for_names(self.user, fields["label_names"])
            )
        groups.claim_pending_members(self.user, existing)
        return existing

    # --- group cards --------------------------------------------------

    def _create_group(self, request, name, fields):  # pylint: disable=unused-argument
        if not fields["name"].strip():
            return _invalid_card()
        group = ContactGroup(
            owner=self.user,
            organization=self.user.organization,
            # A phone would retry a refused name forever: number it.
            name=ContactGroup.free_name(self.user, fields["name"]),
            vcard_extra=fields["vcard_extra"],
        )
        group.save()
        if conflict := self._name_card(name, group):
            return conflict
        groups.set_members(self.user, group, fields["member_refs"], [])
        return group

    def _update_group(self, request, existing, fields):  # pylint: disable=unused-argument
        """Apply what the client changed against the group card it was served.

        Members that are not cards of the personal book were not served,
        so a client cannot drop them.
        """
        _is_group, served = parse_one_card(
            storage.render_card(self.user, storage.PERSONAL, existing)
        )
        dirty = []
        if fields["name"] != served["name"]:
            if not fields["name"].strip():
                return _invalid_card()
            existing.name = ContactGroup.free_name(
                self.user, fields["name"], exclude=existing
            )
            dirty.append("name")
        if fields["vcard_extra"] != served["vcard_extra"]:
            pending = existing.vcard_extra.get(PENDING_MEMBERS)
            existing.vcard_extra = dict(fields["vcard_extra"])
            groups.set_pending(existing, pending or [])
            dirty.append("vcard_extra")
        if dirty:
            existing.save(update_fields=[*dirty, "updated_at"])
        groups.set_members(
            self.user,
            existing,
            [ref for ref in fields["member_refs"] if ref not in served["member_refs"]],
            [ref for ref in served["member_refs"] if ref not in fields["member_refs"]],
        )
        return existing

    def delete(self, request, *args, **kwargs):
        """Move a card to the trash (soft delete), matching the REST API.

        Deleting an overlay only drops the local edits (the parent shows
        again), so like the REST API it is removed outright: a trashed
        overlay would keep hiding its parent from every listing. A group
        has no trash, as over REST.

        The directory book is read-only, even for the administrators who
        may edit its shared groups in the web app.
        """
        check_scope(self.channel, write=True)
        target = self._card_target()
        contact = self._find(*target) if target else None
        if contact is None:
            return HttpResponse(status=404)
        if target[0] != storage.PERSONAL or not contact.is_writable(self.user):
            return HttpResponse(status=403)
        if precondition := self._preconditions(request, contact):
            return precondition

        if storage.is_group(contact) or contact.overlay_of_id is not None:
            contact.delete()
        else:
            contact.deleted_at = timezone.now()
            contact.save(update_fields=["deleted_at", "updated_at"])
        return HttpResponse(status=204)


def _read_body(request):
    """The body of a PUT, or the 403 for one over ``max-resource-size``."""
    limit = storage.max_resource_size()
    try:
        too_big = int(request.META.get("CONTENT_LENGTH") or 0) > limit
        body = b"" if too_big else request.body
    except ValueError, RequestDataTooBig:
        too_big = True
    if too_big or len(body) > limit:
        return _error(403, CARD("max-resource-size"))
    return body


def _api_fields(fields):
    """The parsed card as REST serializer input.

    Without vcard_extra, which the API does not expose, and label_names,
    applied as labels once the card is saved.
    """
    return {
        key: value
        for key, value in fields.items()
        if key not in ("vcard_extra", "label_names")
    }


# Tag -> builder. Defined after the class so the methods exist.
PROP_BUILDERS = {
    D("current-user-principal"): CardDAVView.prop_principal_href,
    D("principal-URL"): CardDAVView.prop_principal_href,
    CARD("addressbook-home-set"): CardDAVView.prop_home_set,
    D("resourcetype"): CardDAVView.prop_resourcetype,
    D("displayname"): CardDAVView.prop_displayname,
    D("getetag"): CardDAVView.prop_getetag,
    D("getcontenttype"): CardDAVView.prop_getcontenttype,
    CARD("address-data"): CardDAVView.prop_address_data,
    CARD("supported-address-data"): CardDAVView.prop_supported_address_data,
    CARD("max-resource-size"): CardDAVView.prop_max_resource_size,
    CS("getctag"): CardDAVView.prop_token,
    D("sync-token"): CardDAVView.prop_token,
    D("supported-report-set"): CardDAVView.prop_supported_report_set,
    D("current-user-privilege-set"): CardDAVView.prop_privilege_set,
}
