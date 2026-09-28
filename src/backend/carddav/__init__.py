"""CardDAV (RFC 6352) endpoint on top of the contacts domain.

This package lives outside ``core``: it reads contacts with the same
scoping as the REST API (organization, overlays, soft-delete), checks
writes with ``Contact.is_writable`` and validates them with the REST
``ContactSerializer``. The tables it owns are its own sync bookkeeping,
which nothing in ``core`` reads. Removing it would leave the contacts
domain untouched.

Layout:

- :mod:`carddav.auth`: HTTP Basic Auth backed by ``core.models.Channel``
- :mod:`carddav.throttling`: rate limits (failed logins, request volume)
- :mod:`carddav.models`: change journal, purge marks, client card names
- :mod:`carddav.signals`: journal writes, hung off ``Contact``
- :mod:`carddav.storage`: address books, ETags, sync tokens over ``Contact``
- :mod:`carddav.groups`: group cards written by clients
- :mod:`carddav.query`: addressbook-query filters
- :mod:`carddav.xml`: multistatus building and request parsing
- :mod:`carddav.views`: the method dispatcher (OPTIONS/PROPFIND/REPORT/...)
- :mod:`carddav.urls`: ``/carddav/`` and ``/.well-known/carddav``
- ``management``: the ``purge_carddav_changes`` command

The vCard payload itself goes through ``core.services.vcf_service``, the
same code as ``.vcf`` import/export. This layer only speaks the DAV
envelope around it.
"""
