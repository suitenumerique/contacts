"""DAV XML helpers: namespaces, multistatus building, request parsing.

Client XML is parsed with :mod:`defusedxml`: CardDAV bodies arrive from
unauthenticated-until-proven network peers, so the stdlib parser's entity
handling (XXE, billion laughs) is not acceptable here. Responses are built
with the stdlib ``ElementTree``, which only ever serializes data we own.
"""

from xml.etree import ElementTree as ET

from defusedxml.ElementTree import fromstring as safe_fromstring

DAV_NS = "DAV:"
CARDDAV_NS = "urn:ietf:params:xml:ns:carddav"
CS_NS = "http://calendarserver.org/ns/"

NS_MAP = {
    "d": DAV_NS,
    "card": CARDDAV_NS,
    "cs": CS_NS,
}

# Readable prefixes in generated documents instead of ns0, ns1...
for _prefix, _uri in NS_MAP.items():
    ET.register_namespace(_prefix, _uri)


def qn(ns, name):
    """Return the ``{namespace}name`` qualified tag used by ElementTree."""
    return f"{{{ns}}}{name}"


def D(name):  # pylint: disable=invalid-name
    """Qualify *name* in the ``DAV:`` namespace."""
    return qn(DAV_NS, name)


def CARD(name):  # pylint: disable=invalid-name
    """Qualify *name* in the CardDAV namespace."""
    return qn(CARDDAV_NS, name)


def CS(name):  # pylint: disable=invalid-name
    """Qualify *name* in the calendarserver.org extension namespace."""
    return qn(CS_NS, name)


def parse_request(body):
    """Parse a request body into an Element, or None when empty.

    An empty body is legal: ``PROPFIND`` with no body means "allprop".
    Malformed XML raises ``ValueError`` so callers can answer 400.
    """
    if not body or not body.strip():
        return None
    try:
        return safe_fromstring(body)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise ValueError("malformed XML body") from exc


PROPNAME = "propname"


def requested_props(root):
    """Return the list of requested property tags for a PROPFIND body.

    Returns ``None`` for allprop (or an empty body), which callers answer
    with their default property set, and :data:`PROPNAME` for a
    ``<propname/>`` request.
    """
    if root is None:
        return None
    if root.find(D("propname")) is not None:
        return PROPNAME
    if root.find(D("allprop")) is not None:
        return None
    prop = root.find(D("prop"))
    if prop is None:
        return None
    return [child.tag for child in prop]


def error_body(*conditions):
    """A ``DAV:error`` document naming the failed *conditions*."""
    error = ET.Element(D("error"))
    for tag in conditions:
        ET.SubElement(error, tag)
    return to_bytes(error)


def multistatus():
    """Create an empty ``<multistatus>`` root element."""
    return ET.Element(D("multistatus"))


def add_response(parent, href, found=None, not_found=(), status=None):
    """Append one ``<response>`` to *parent*.

    Args:
        href: the resource URL.
        found: iterable of already-built property Elements that exist.
        not_found: iterable of property tags to report as 404.
        status: when given, emit a bare status response (no propstat),
            used by ``sync-collection`` to report removed members.
    """
    response = ET.SubElement(parent, D("response"))
    ET.SubElement(response, D("href")).text = href

    if status is not None:
        ET.SubElement(response, D("status")).text = status
        return response

    found = list(found or [])
    if found:
        propstat = ET.SubElement(response, D("propstat"))
        prop = ET.SubElement(propstat, D("prop"))
        for element in found:
            prop.append(element)
        ET.SubElement(propstat, D("status")).text = "HTTP/1.1 200 OK"

    not_found = list(not_found)
    if not_found:
        propstat = ET.SubElement(response, D("propstat"))
        prop = ET.SubElement(propstat, D("prop"))
        for tag in not_found:
            ET.SubElement(prop, tag)
        ET.SubElement(propstat, D("status")).text = "HTTP/1.1 404 Not Found"

    return response


def to_bytes(root):
    """Serialize a document with the XML declaration clients expect."""
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
