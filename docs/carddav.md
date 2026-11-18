# CardDAV

Contacts exposes a read/write **CardDAV** endpoint (RFC 6352) at `/carddav/`,
so phones and desktop address books (DAVx⁵, Apple Contacts, Thunderbird,
Evolution) can sync directly against the app.

It sits **on top** of the contacts domain: the `carddav` Django package
reads contacts with the same scoping as the REST API, checks writes with
`Contact.is_writable` and validates them with the REST `ContactSerializer`.
The only tables it owns are its own sync bookkeeping, which nothing in
`core` reads. Deleting the package would leave `core` untouched.

```
src/backend/carddav/
  auth.py        Basic Auth → core.models.Channel
  throttling.py  rate limits (failed logins, request volume)
  models.py      change journal, purge marks, client card names
  signals.py     journal writes, hung off Contact
  storage.py     address books, ETags, sync tokens over Contact
  groups.py      group cards written by clients
  query.py       addressbook-query filters
  xml.py         multistatus building, safe request parsing
  views.py       the method dispatcher
  urls.py        /carddav/ and /.well-known/carddav
  management/    purge_carddav_changes
```

The vCard payload itself is **not** implemented here: it goes through
`core.services.vcf_service`, the same code path as `.vcf` import/export.
This layer only speaks the DAV envelope around it.

## Feature flag

`FEATURE_CARDDAV` (default `True`) turns the feature off without removing
the package: `/carddav/` and `/.well-known/carddav` answer `404`, the REST
API refuses to create `carddav` channels, and the frontend hides the CardDAV
settings tab. The change journal keeps being written, so sync tokens stay
valid if the flag is turned back on.

## Credentials

CardDAV clients speak HTTP Basic Auth and nothing else, so a credential has
to be presentable as a username/password pair:

```
username = the user's email address
password = <channel id as 32 hex chars><api key>
```

The channel id is fixed-width, so the server splits the password back apart
unambiguously. Using the email as the username is what makes principal
discovery work: the client asks for `current-user-principal` and the server
can answer without any client-side configuration beyond the URL.

Only channel credentials are accepted. A browser session is not, even on
the same host: it would bypass the entitlement check and the channel
scopes, and the web app never calls CardDAV.

Create one from the API. The response carries a ready-to-paste
`carddav_password`, exactly once:

```http
POST /api/v1.0/channels/
{"name": "My phone", "type": "carddav", "scopes": ["carddav:write"], "groups": true}
```

`groups` (default `false`, fixed at creation) also serves the user's groups
as group cards, see [Groups](#groups). DAVx⁵'s default contact group method
("groups are separate vCards") reads them; leave it off for a DAVx⁵ account
set to "groups are per-contact categories", which would save each group
card as a contact.

```json
{"id": "…", "type": "carddav", "api_key": "…", "carddav_password": "…"}
```

Operators can mint one (including the organization- and global-scoped
channels the API refuses) with `manage.py create_channel --type carddav`;
see [channels.md](channels.md).

Point the client at `https://<host>/carddav/` with that email and password.

### Discovery

`/.well-known/carddav` redirects (301) to `/carddav/`, per RFC 6764, so a
user only has to type the host name. It answers any method (clients
discover with the request they actually want, usually `PROPFIND`) and
needs no credentials, since a client bootstraps before it has anywhere to
send them.

The principal is `/carddav/principals/<email>/`, percent-encoded.

### Rate limits

Failed authentications are counted per client address
(`API_CARDDAV_AUTH_THROTTLE_RATE`, default `10/minute`). Past the budget,
further failures from that address are answered `429` with `Retry-After`
instead of `401`. Not counted:

- requests without an `Authorization` header: clients send one first to
  get the challenge;
- authorization failures (a valid key that may not act as the named user):
  they need a working credential, so they are not a guessing vector.

Valid credentials always get through, so a guesser behind a shared address
(NAT, corporate proxy) cannot lock the other users out.

Authenticated request volume is capped per credential
(`API_CARDDAV_THROTTLE_RATE`, default `5000/hour`), answered `429` with
`Retry-After`. DAVx⁵ downloads 10 cards per request, so an initial sync
costs about one request per 10 cards across both books: the default covers
about 40,000 cards. Raise it for larger directories: DAVx⁵ treats a `429`
as a hard error and shows it to the user.

#### Client address behind a proxy

The failure budget is per client address. Behind the frontend proxy, set
`NUM_PROXIES` and `CONTACTS_FRONTEND_TRUSTED_PROXIES` as described in
[deployment.md](deployment.md#client-address-in-django); without them every
client shares the router's address, and so one failure budget.

### Scopes

| Scope           | Grants                                              |
| --------------- | --------------------------------------------------- |
| `carddav:read`  | PROPFIND, REPORT, GET, HEAD                          |
| `carddav:write` | the above plus PUT and DELETE                        |

`OPTIONS` needs no credential.

`carddav:*` scopes are only valid on `type="carddav"` channels, and the REST
scopes (`contacts:read`, `groups:read`) are only valid on `type="api_key"`
channels. The two sets are disjoint by validation: a sync password handed to
a phone must not double as a service key that can enumerate address books
over REST, and vice versa.

Keys are stored as SHA-256 hashes like every other channel key, so a
database leak does not yield usable CardDAV passwords.

## Address books

Two collections per user, both under
`/carddav/addressbooks/<book>/`:

| Book        | Contents                                              | Writable |
| ----------- | ----------------------------------------------------- | -------- |
| `personal`  | the user's own contacts (`scope` personal, no `source`) | yes     |
| `directory` | the organization's (`scope` organization) and `source`-provisioned rows | no |

Writability in `personal` is decided by `Contact.is_writable`, the same
predicate the REST API uses. `directory` is read-only for everyone, even
the administrators who edit the organization's rows in the web app.
CardDAV adds a protocol, never a permission.

### Card names

A card created by a client keeps the name the client chose for it (Apple
uses an upper-case UUID, DAVx⁵ the card's UID, anything goes), stored in
`carddav_card_name`. Every other card is `<uuid>.vcf` (the contact's or the
group's id), in the lower-case form this server emits. Names are compared
as exact strings.

### Labels

A contact's labels are its `CATEGORIES`, both ways. A category a client
sends that matches no label (case-insensitively) creates one; one it drops
takes the label off the contact, and the label itself stays. A row without
an owner has no labels, so its own stored `CATEGORIES` lines are served
as they are.

Until a device sends a `CATEGORIES` property in an accepted `PUT` of a
contact card, a `PUT` without `CATEGORIES` keeps the labels of the contact.
Thus a client that discards categories does not remove the labels when it
changes another field. After that first property (`sends_categories` in
the settings of the device), a `PUT` without `CATEGORIES` removes the
labels.

As a result, the removal of the last label of a contact on a device can
have no effect at first. The web app can still remove the label. An empty
`CATEGORIES` property always removes the labels.

### Groups

For a credential created with `groups`, each book also holds its groups as
group cards, in the form Apple's clients use on vCard 3:

```
BEGIN:VCARD
VERSION:3.0
FN:Team
N:Team;;;;
X-ADDRESSBOOKSERVER-KIND:group
X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:<member UID>
UID:<group UID>
END:VCARD
```

`personal` holds the user's own groups (writable), `directory` the
organization's (read-only). A group card only lists the members that are
cards of the same book: an org contact in a personal group is not listed
(unless the user overlaid it), and a `PUT` of the group card cannot remove
it, since the client never saw it. The vCard 4 `KIND:group` and `MEMBER` are
read too.

A `PUT` of a group card creates or updates a personal group; a name taken
by another of the user's groups gets a number (`Team (2)`) rather than a
refusal the phone would retry forever. A member UID that names no card yet
is kept on the group (`ContactGroup.vcard_extra`) and served back as sent,
since iOS may send a group before the card of its new member; the member
joins when that card arrives. `DELETE` erases the group (groups have no
corbeille). A card cannot change from a contact into a group or back.

### Writes

A `PUT` is validated like a REST write: a name required (`FN`, or an `N`
component other than the prefix; a card without either falls back to its
first email as `FN`,
as vCard requires one), an email or a phone required, the same field
lengths. A card with only `FN` keeps it whole, it is never split into
first and last name. A card that fails, or
that is not exactly one readable vCard, is refused with `403` and the
`CARDDAV:valid-address-data` precondition. A body larger than
`max-resource-size` (the smaller of `CONTACTS_VCF_MAX_FILE_SIZE` and
`DATA_UPLOAD_MAX_MEMORY_SIZE`) gets `403` with `CARDDAV:max-resource-size`.

One length is different: a text of more than 255 characters in `FN`, `N`,
`NICKNAME`, `ORG` or `TITLE` does not refuse the card, because a client
cannot correct a refused card. The contact holds the first 255 characters.
The server keeps the full line and serves it back until the text changes.
A vCard export holds the full line, and a vCard import applies the same
rule.

A `PUT` over an existing card applies only what the client changed against
the card it was served: an unchanged `PUT` is a no-op, and what vCard does
not carry (favorites, group memberships) is left alone. The
response carries an `ETag` only when the stored card is byte-for-byte the
body sent (RFC 6352 6.3.2.3); otherwise the client fetches it back.

A `PUT` onto a card of the user's own that is in the corbeille restores it
with the new content (`201`). The restore keeps the row, thus the data
that vCard does not carry (favorite, group memberships) stays. The labels
follow the `CATEGORIES` of the body, as for an update.

`DELETE` moves the contact to the corbeille (soft delete), matching the REST
API: it does not erase it. The one exception is an overlay (a local copy of
a read-only contact): deleting it drops the local edits outright and the
source card shows again, exactly as over REST.

Overlays appear in the `personal` book (they are the user's rows) and the
read-only contact they cover is hidden. A `PUT` on an overlay follows the
REST rule for the fields the client changed: equal to the source value,
they stay inherited; different, they become local overrides. The overlay
shows its parent's unmodeled vCard properties and cannot change them. Thus
it cannot keep the full line of a long text: a `PUT` that gives it a new
text of more than 255 characters is refused, and a `PUT` that cuts such a
text of its parent to exactly its first 255 characters has no effect.

### vCard mapping

Cards are served as vCard 3.0 and accepted as 3.0 or 4.0. Every card
carries a `UID`: the client's when it sent one, else the contact id.
What the columns cannot hold is kept in `Contact.vcard_extra` as raw
content lines, so a card read, edited on a phone and written back keeps
its property groups (`item1.`), parameters, ADR components, lists of
values in one `N` component, `KEY`, `X-` properties, and later instances
of one-column
properties (a second `ORG`, `NOTE`...). See `core/services/vcf_service.py`
for the mapping.

A card is served without the characters that XML 1.0 does not allow, such
as a vertical tab. The stored data keeps these characters.

## Supported methods

`OPTIONS`, `PROPFIND`, `REPORT`, `GET`, `HEAD`, `PUT`, `DELETE`.

Reports: `addressbook-multiget` (up to 1000 hrefs, answered with a fixed
number of queries),
`addressbook-query`, `sync-collection`.

Preconditions: `If-Match` and `If-None-Match` take `*` or a list of ETags;
a mismatch is `412`, and a matching `If-None-Match` on `GET`/`HEAD` is
`304`. `PROPFIND` takes `Depth: 0` or `1`; `infinity` is refused with the
`DAV:propfind-finite-depth` precondition. `<propname/>` is supported.

Not implemented: `LOCK`/`UNLOCK` (clients fall back to ETag
preconditions), `MKCOL` (address books derive from the data model, users
do not create them), `COPY`/`MOVE`.

`addressbook-query` evaluates `prop-filter` on `FN`, `EMAIL`, `TEL` and `N`
(matched as `family;given;;;`) with `test="anyof"`/`"allof"`,
`is-not-defined`, and `text-match` with every match type,
`negate-condition` and the `i;unicode-casemap` (default), `i;ascii-casemap`
and `i;octet` collations. Anything else (other properties,
`param-filter`, other collations) is refused with `403` and the
`CARDDAV:supported-filter` or `CARDDAV:supported-collation` precondition,
never answered with a superset. `<limit><nresults>` truncates the answer
and flags it with a `507` response for the collection.

## Sync tokens

```
urn:contacts:sync:<organization id>:<seq>
```

`seq` is a number from the **change journal**; the ctag (`CS:getctag`) is
the same value. It carries no clock: it only changes when the address book
does. A token naming another organization, or a number the organization
has not reached, is refused.

### The change journal

Every write that changes a card records a numbered entry in
`carddav_contact_change`, and the number comes from a per-organization
counter in `carddav_sync_counter` bumped inside the writing transaction.
That covers writes to other rows that show on a card: a label put on,
taken off, renamed or deleted (the contact's `CATEGORIES`), and a group's
members (its group card). The entry's number is also the card's version:
the `ETag` is derived from it.

Deriving a delta from `Contact.updated_at` instead cannot be made exact.
Two writes inside the same microsecond are indistinguishable from none, a
backwards clock adjustment hides everything written before it, and the
stamp is set in Python before the row commits, so rows can become visible
out of timestamp order. All three drop a card from the delta silently, and
the client never asks again.

The counter is what closes the last of those: its row stays locked until
the writing transaction commits, so a concurrent writer waits there instead
of taking a higher number while a lower one is still in flight. There is
one counter per organization rather than per collection, so writes in
unrelated organizations never queue behind each other.

A `sync-collection` takes the book's high-water mark before reading the
journal and reports only the changes up to it, so a write landing during
the request is left for the next sync rather than skipped.

The journal holds one row per contact, carrying its latest number: it is a
"what is dirty since N" index, not an audit log. Which address book a change
belongs to, and whether the asking user may see it, is decided at read time
against the books themselves; anything the journal names that the book no
longer holds is reported as removed, which covers trashed, newly overlaid
and erased cards without separate bookkeeping.

Overlays move with their parent. An overlay renders fields read live from
the contact it covers, so editing that contact journals the overlay too,
and creating or dropping one journals the parent, whose visibility just
changed. Groups move with their members: a write to a contact journals the
groups holding it, whose cards name it only while it shows in their book.

> Writes that bypass model signals (`QuerySet.update()`, `bulk_update()`,
> `bulk_create()`) do not reach the journal, and a card changed that way
> will not appear in any delta. A row without a journal entry gets an ETag
> from its timestamps until its next journaled write.

### Resync

Deletions are observable **because of the corbeille**: a trashed row still
exists with `deleted_at` set, so `sync-collection` can report it as removed.
A card erased outright (dropping an overlay, or emptying the corbeille)
never passes through it, so the journal keeps a tombstone instead.

`purge_carddav_changes` drops tombstones older than `TRASHBIN_CUTOFF_DAYS`
and records, per address book, the highest number it dropped
(`carddav_purge_mark`). A token below the mark of its book may be missing
a removal, so the server answers `403` with the RFC 6578 `valid-sync-token`
precondition and the client resyncs from scratch. Marks count towards the
book's number, so a purge never moves a ctag backwards, and a purge in a
colleague's book does not invalidate this one's tokens. Schedule
`purge_trashed_contacts` and `purge_carddav_changes` daily.

## Known limits

- **Groups only for credentials that ask for them**, and only as Apple's
  group cards (vCard 3). DAVx⁵ needs its default "groups are separate
  vCards" method.
- **No `getcontentlength`**: clients that display card sizes show nothing.
- **`PROPFIND Depth:1` and `addressbook-query` are unpaginated**: a very
  large address book is rendered in a single response.
- **One address book per kind.** Users cannot create additional books:
  there is no model for "a book".
- **No `CARDDAV:no-uid-conflict` check**: two cards of one book may carry
  the same UID if a client sends them.
