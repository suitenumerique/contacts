# External API (resource server)

Other Suite apps (Drive, Docs, Messages...) suggest people and groups in
their share modals from **Contacts**. They call `/external_api/v1.0/` on
behalf of their user, with the **access token that user logged in with**:
Contacts is an OAuth 2.0 *resource server* and checks each token by
introspection at the authorization server (ProConnect, Keycloak in dev).

What a user gets here is what they see in Contacts: their personal book,
their organization's book (synced rows included), with their overlays in
place of the rows they cover. Contacts is the only source of suggestions:
a person shows up if they exist as a contact.

```
 Drive share modal ──▶ Drive backend ──▶ GET /external_api/v1.0/search/?q=mar
   (session cookie)     (stored user        Authorization: Bearer <user token>
                         access token)              │
                                                    ▼
                                   introspection (cached), then the search
```

The browser never calls Contacts: the consumer's backend relays, with the
access token stored in its session (`OIDC_STORE_ACCESS_TOKEN`). No CORS, no
token in the frontend.

| Endpoint | Purpose |
| --- | --- |
| `GET  /external_api/v1.0/search/` | contacts **and** groups in one ranked list |
| `POST /external_api/v1.0/interactions/` | "the user just shared with, wrote to, invited... these": feeds the ranking |
| `GET  /external_api/v1.0/groups/{id}/members/` | expand a group into people, for apps that invite one by one |
| `POST /external_api/v1.0/contacts/` | "Add as contact": save an address in the user's contacts |

The OpenAPI schema is served at `/external_api/v1.0/swagger.json` (UI at
`/external_api/v1.0/swagger/`) when `USE_SWAGGER` or `DEBUG` is on, as in
the `Development` configuration. `USE_SWAGGER` is a settings attribute, not
an environment variable. The schema is separate from
the web app's schema, so the frontend client doesn't list these endpoints.

## Configuration

| Env var | Default | |
| --- | --- | --- |
| `OIDC_RESOURCE_SERVER_ENABLED` | `False` | mounts `/external_api/` |
| `OIDC_OP_URL` | | issuer of the authorization server |
| `OIDC_OP_INTROSPECTION_ENDPOINT` | | RFC 7662 introspection URL |
| `OIDC_RS_CLIENT_ID` / `OIDC_RS_CLIENT_SECRET` | | Contacts' own credentials at the authorization server, used to introspect |
| `OIDC_RS_ALLOWED_AUDIENCES` | `[]` | apps allowed to call, e.g. `drive,docs` |
| `OIDC_RS_AUDIENCE_CLAIM` | `client_id` | introspection claim naming the app a token was issued to |
| `OIDC_RS_SCOPES` | `openid` | a token must carry at least one of them |
| `OIDC_RS_CREATE_USER` | `False` | create the user of an unknown `sub`, see [Who may call](#who-may-call) |
| `OIDC_RS_INTROSPECTION_CACHE_TIMEOUT` | `60` | seconds an introspection is reused; `0` turns the cache off |
| `OIDC_RS_BACKEND_CLASS` | `core.authentication.resource_server.ContactsResourceServerBackend` | |
| `JSCONTACT_VENDOR_PREFIX` | `numerique.gouv.fr` | domain prefix of the cards' vendor properties (`numerique.gouv.fr:isOrg`); part of the contract, see [Cards](#cards-jscontact) |
| `EXTERNAL_API` | all enabled | endpoints served, and the interaction kinds each app may report (see below) |
| `INTERACTION_KINDS` | see [Interaction kinds](#interaction-kinds) | weight of each kind |
| `INTERACTION_MASS_THRESHOLD` | `10` | past this many people, an interaction adds its kind's weight times `threshold / n` per person |
| `INTERACTION_HALF_LIFE_DAYS` | `30` | ranking decay, see below |
| `SEARCH_STATEMENT_TIMEOUT_MS` | `300` | time limit of each SQL statement of a search, in milliseconds |
| `API_EXTERNAL_SEARCH_THROTTLE_RATE` | `300/minute` | per user |
| `API_EXTERNAL_INTERACTIONS_THROTTLE_RATE` | `60/minute` | per user |
| `API_EXTERNAL_INTERACTIONS_CHANNEL_THROTTLE_RATE` | `1200/minute` | per service channel |
| `API_EXTERNAL_GROUP_MEMBERS_THROTTLE_RATE` | `120/minute` | per user |
| `API_EXTERNAL_CONTACTS_THROTTLE_RATE` | `30/minute` | per user |

`EXTERNAL_API` replaces the whole default when set, so give every key. The
value is a Python dict literal (`True`, not `true`):

```python
{
  "search": {"enabled": True},
  "interactions": {
    "enabled": True,
    "kinds": {
      "drive": ["share"],
      "docs": ["share", "mention"],
      "messages": ["email_sent", "email_received"],
      "calendars": ["event_invite"]
    },
    "channel_kinds": ["email_sent", "email_received", "event_invite"]
  },
  "group_members": {"enabled": True},
  "contacts": {"enabled": True}
}
```

### What the authorization server must do

- **Register Contacts as a resource server**, with the credentials above,
  allowed to introspect the consumer apps' tokens.
- **Put Contacts in the token audience.** Keycloak (26.x) answers
  `{"active": false}` to a client introspecting a token whose `aud` doesn't
  name it. In Keycloak, add an *audience* mapper (`oidc-audience-mapper`,
  included client audience `contacts`) to each consumer client, as the dev
  realm does for `drive`.
- Return `sub` (matched to the Contacts user) and the audience claim.
- With `OIDC_RS_CREATE_USER`: return `email` and the
  [organization claim](organizations.md) at userinfo for a consumer app's
  token.

### Who may call

A request is served when all of these hold, in order:

1. A `Bearer` token is present and the authorization server says it is
   active, with one of `OIDC_RS_SCOPES`: else **401**.
2. Its `sub` is an active Contacts user: else **401**. With
   `OIDC_RS_CREATE_USER`, an unknown `sub` from an allowed app gets a new
   user, created from the userinfo as at a first login.
3. The app it was issued to is in `OIDC_RS_ALLOWED_AUDIENCES`: else **403**.
4. The user passes the `can_access` entitlement: else **403**.

A session cookie is never accepted here, and a channel key (`X-Channel-Id`)
only on `POST /interactions/` (see
[Events without a user](#events-without-a-user-service-channels)). If the
authorization server is unreachable, the answer is **503**: the caller
should retry, not refresh its token.

### Introspection cache

A share modal searches on every keystroke; without a cache each one would
wait for the authorization server first. A successful introspection is kept
in the Django cache (Redis) for `OIDC_RS_INTROSPECTION_CACHE_TIMEOUT`
seconds, under a SHA-256 of the token (never the token itself), and never
past the token's own `exp`. Contacts does not cache a token that the
introspection rejects. The subsequent checks (the user, the app, the
entitlement) run on each request, with or without the cache.

Trade-off: a token revoked at the authorization server keeps working here
until its entry expires (60 s by default).

## Endpoints

### Cards (JSContact)

Contacts and groups are **JSContact cards** ([RFC 9553](https://www.rfc-editor.org/rfc/rfc9553)),
the IETF's JSON format for contact data, so apps read them with the
standard's vocabulary. A card carries only what a picker needs; properties
with nothing to say are left out.

```json
{
  "@type": "Card",
  "version": "1.0",
  "uid": "urn:uuid:79aa8714-94af-4ea5-8fc3-55673153af3e",
  "kind": "individual",
  "name": {
    "full": "Félix Marchand",
    "components": [{"kind": "given", "value": "Félix"}, {"kind": "surname", "value": "Marchand"}]
  },
  "emails": {
    "e1": {"address": "felix.marchand@brigny.example", "contexts": {"work": true}, "pref": 1},
    "e2": {"address": "felix@home.example", "contexts": {"private": true}}
  },
  "organizations": {"o1": {"name": "Mairie de Brigny", "units": [{"name": "Conseil"}]}},
  "titles": {"t1": {"kind": "title", "name": "Conseiller municipal"}},
  "media": {"m1": {"kind": "photo", "uri": "https://cdn.example/felix.png"}},
  "numerique.gouv.fr:isOrg": false,
  "numerique.gouv.fr:source": ""
}
```

| Contacts | Card |
| --- | --- |
| id | `uid`: `urn:uuid:<id>`; an overlay has its own |
| contact / group | `kind`: `individual` / `group` |
| displayed name | `name.full` |
| prefix, given, middle, family name, suffix | `name.components`: `title`, `given`, `given2`, `surname`, `credential` |
| nickname | `nicknames.k1.name` |
| grammatical gender | `speakToAs.grammaticalGender` |
| emails | `emails` by id (`e1`…): type `work` → `contexts.work`, `home` or `private` → `contexts.private`, `other` or none → neither, any other text → `label`; the first one has `pref: 1` |
| company, department | `organizations.o1.name`, `.units[0].name` |
| job title | `titles.t1` |
| photo (https only; inline photos are left out) | `media.m1.uri` |
| in the organization's book | `numerique.gouv.fr:isOrg` |
| synced from | `numerique.gouv.fr:source` (`""` when not synced) |
| group members | `numerique.gouv.fr:membersCount`; `members` isn't listed (`GET /groups/{id}/members/` is) |

- **Vendor properties** (RFC 9553, 1.8) carry what JSContact has no
  property for. Their prefix is `JSCONTACT_VENDOR_PREFIX`: changing it
  renames them for every caller.
- **Ids in maps** (`e1`, `o1`…) are positions, stable for a given card, not
  across edits.
- `@type` is only on the card: RFC 9553 makes it optional on nested
  objects.
- **Skip cards whose `kind` you don't know**: new kinds may come without a
  new API version.

### `GET /search/`

| Param | Default | |
| --- | --- | --- |
| `q` | `""` | empty: the user's most picked contacts and groups |
| `kind` | `individual,group` | card kinds to return |
| `limit` | `10` | 1 to 50 |

Each word of `q` must match, accents and case ignored. A word matches the
**start of a word** at any length, and the **inside of a word** from 3
characters. Addresses are also split into words, so "dubois" finds
`claire.dubois@…`. A query made of digits looks up phone numbers.

Contacts without an email are left out (apps invite by email), and so is
the user's own card. Order:

1. what the user picked, by decayed rank (see [Ranking](#ranking));
2. the user's favorites;
3. a word starts with the query;
4. the query inside a word;

then alphabetical within each. The order is the contract: no score is
returned.

```json
{
  "results": [
    {"@type": "Card", "version": "1.0", "uid": "urn:uuid:79aa…",
     "kind": "individual", "name": {"full": "Félix Marchand"},
     "emails": {"e1": {"address": "felix.marchand@brigny.example",
                       "contexts": {"work": true}, "pref": 1}},
     "numerique.gouv.fr:isOrg": false, "numerique.gouv.fr:source": ""},
    {"@type": "Card", "version": "1.0", "uid": "urn:uuid:5d0b…",
     "kind": "group", "name": {"full": "Marchés publics"},
     "emails": {"e1": {"address": "marches@brigny.example", "pref": 1}},
     "numerique.gouv.fr:isOrg": true, "numerique.gouv.fr:source": "",
     "numerique.gouv.fr:membersCount": 6}
  ]
}
```

### `POST /interactions/`

One call is **one event** (a share, a message, an invitation) and the people
or groups in it. Called by the consumer's backend **after the event
happened**, so the ranking reflects real shares and messages, not clicks.

```json
{
  "kind": "email_sent",
  "items": [
    {"uid": "urn:uuid:79aa…"},
    {"uid": "urn:uuid:02e2…"},
    {"email": "bob.martin@example.org"}
  ]
}
```

- **`kind`**: what happened, `share` when left out (see
  [Interaction kinds](#interaction-kinds)). An app may only report the kinds
  `EXTERNAL_API` lists for it, else **403**: Drive can't report emails.
- **Items by uid**: for an app whose user picked from `GET /search/`; send
  the card's `uid` back as is. Contacts and groups alike: their ids are
  random UUIDs, so a uid matches at most one of them.
- **Items by address** (`email`): for an app that only knows addresses
  (Messages, Calendars). Contacts finds the row behind it: a contact the user
  sees with that address, their own cards before the organization's, then a
  group with that address (a shared mailbox). An address matching nothing
  is ignored (**nothing is created**), and so is the user's own.

Answers `204`. 1 to 50 items with a user token, 500 with a channel; each
item is exactly one `uid` or one `email`. Uids the user can't see are
ignored, so the answer never tells whether a row exists. A person named
twice (by uid and by address) counts once; an interaction with
an overlay counts for the row it covers; with a group, for the group, not
its members.

#### Interaction kinds

| Kind | Weight | Reported by |
| --- | ---: | --- |
| `share` | 1 | Drive, Docs, after a share |
| `email_sent` | 1 | Messages, for each To / Cc recipient |
| `event_invite` | 1 | Calendars |
| `mention` | 0.5 | Docs (@mention) |
| `email_received` | 0.2 | Messages, server side (a channel) |
| `added_as_contact` | 2 | Contacts itself, see `POST /contacts/` |

Writing to someone says more than receiving mail from them. In an event
with more than `INTERACTION_MASS_THRESHOLD` people (10), each person gets
the kind's weight times `threshold / n`. A message to 200 people says little
about any of them. Addresses matching nothing still count as people in the
event.

`INTERACTION_KINDS` sets the weights. Like `EXTERNAL_API`, it is a Python
dict literal, and it replaces the whole default. Thus the value must list
each kind. A kind that is not in the value answers **400**. A weight of `0`
adds nothing to the score. But Contacts still records the interaction, and
the card stays in the recent picks of the user.

#### Events without a user (service channels)

Some events happen on a server with no user session: Messages receives mail
when nobody is logged in, so it has no access token. It reports them with a
service channel holding the `interactions:write` scope, naming the user
with `on_behalf_of` (in the body), like the other channel endpoints:

```bash
python manage.py create_channel --name "Messages interactions" \
  --scope-level global --scopes interactions:write
```

```http
POST /external_api/v1.0/interactions/
X-Channel-Id: <channel id>
X-API-Key: <key>

{"kind": "email_received", "on_behalf_of": "alice@brigny.example",
 "items": [{"email": "bob.martin@example.org"}]}
```

A channel may report `EXTERNAL_API["interactions"]["channel_kinds"]`, up to
500 items per call, and is rate limited per channel. An organization
channel only acts for its organization's users. With a user token,
`on_behalf_of` is ignored.

### `POST /contacts/`

"Add as contact" buttons (a sender in Messages, a share recipient in Drive):

```json
{"email": "bob.martin@example.org", "name": "Bob Martin"}
```

- **`201`** with the card of a new personal contact of the user. `name` is
  optional: without it the contact shows its address.
- **`200`** with the card of the contact the user already sees under that address,
  their own or the organization's: calling twice is harmless.
- **`400`** for an invalid address or the user's own.

A new contact gets a one-time boost in the ranking (`added_as_contact`):
saving someone says they matter.

### `GET /groups/{id}/members/`

Visible, non-trashed members as cards, alphabetical, paginated (`page`,
`page_size`, 100 by default), with the group's card:

```json
{
  "count": 8, "next": null, "previous": null,
  "group": {"@type": "Card", "version": "1.0", "uid": "urn:uuid:02e2…",
            "kind": "group", "name": {"full": "Conseil municipal"},
            "numerique.gouv.fr:isOrg": false, "numerique.gouv.fr:source": "",
            "numerique.gouv.fr:membersCount": 8},
  "results": [
    {"@type": "Card", "version": "1.0", "uid": "urn:uuid:c269…",
     "kind": "individual", "name": {"full": "Amandine Aminoff"},
     "emails": {"e1": {"address": "amandine.aminoff@brigny.example", "pref": 1}}, …},
    {"@type": "Card", "version": "1.0", "uid": "urn:uuid:3c7e…",
     "kind": "individual", "name": {"full": "Louis Garnier"}, …}
  ]
}
```

Members without an email stay, as cards without `emails`, so the app can
tell the user who can't be invited. `404` for a group the user can't see.

## Ranking

`core.models.Interaction` keeps one row per (user, contact) or (user,
group): a lifetime `count`, and a `score` that loses half its weight every
`INTERACTION_HALF_LIFE_DAYS`, a *frecency* (frequency + recency) score with
exponential decay:

```
on interaction:  score = score × 0.5^(days since last / half-life) + weight
on read:         rank  = score × 0.5^(days since last / half-life)
```

`weight` is the kind's (see [Interaction kinds](#interaction-kinds)). So
someone written to every week this month outranks someone shared with often
a year ago. The decay is applied in SQL when reading: no background
job. A pick of an overlay is stored on the row it covers, like group
memberships, so the history survives dropping the overlay.

## Performance

Target: **p95 under 50 ms for the search queries** (the database work of
one call), on 1M contacts in total, 200k in one organization, 10k in one
personal book, 5k groups and 10M interactions. Authentication is outside
the target: the first call with a new token waits for the authorization
server, which Contacts doesn't control.

How the search holds it:

- **Word starts, not substrings, for short queries.** `search_text` starts
  every word with a space, so "a word starts with *ma*" is
  `LIKE '% ma%'`, which the trigram index serves from one character on;
  "*ma* anywhere" can't use it below three characters and would read the
  whole index.
- **Small steps that stop early.** Sorting every match by a score would read
  all of them (typing "m" matches a third of a large organization). The
  order above lets the search run as a few queries, each limited to the rows
  still missing, and stop as soon as the response is full. Personal and
  organization books are queried apart, so each walks the
  `(organization, owner, sort_key)` index in order.
- **Rank computed in SQL**, only for the user's 1000 most recent picks of
  each kind (contacts, groups), and only the rows the response needs are
  loaded, with only the columns it shows.
- The member count (`membersCount`) is computed for the returned groups only, from each
  group's own memberships.
- `SEARCH_STATEMENT_TIMEOUT_MS` limits each SQL statement of a search. If
  a statement goes over the limit, the search stops with **503**, so one
  bad plan can't pile up on the database.

### Benchmark

```bash
docker compose exec backend-dev python manage.py seed_benchmark
docker compose exec backend-dev python manage.py benchmark_search
docker compose exec backend-dev python manage.py seed_benchmark --clear
```

`seed_benchmark` fills the database to the target size (in `benchmark-*`
organizations only; options scale it down), then `benchmark_search` calls
the search function directly (no HTTP, no auth) for common and rare queries
and reports p50 / p95 per query. It exits with an error when the overall
p95 reaches `--target-ms` (50 by default).

The `Search benchmark` workflow (`.github/workflows/benchmark.yml`) does the
same at full size every Monday on `main`, about 30 minutes, mostly seeding.
Run it on demand from the Actions tab (**Run workflow**), or with
`gh workflow run benchmark.yml`. The results table is on the run's summary
page. Its Postgres runs with default settings on shared machines, so compare
its numbers with earlier runs rather than with production.

Measured on a laptop (dev stack, `DEBUG` on), 1,000,035 contacts (210,000
in the user's organization), 10.5M interactions, 20 runs per query, limit 10:

| Query | Results | SQL queries | p50 ms | p95 ms |
| --- | ---: | ---: | ---: | ---: |
| empty (top picks) | 10 | 3 | 28.3 | 31.0 |
| 1 letter (`m`) | 10 | 3 | 22.2 | 27.0 |
| 2 letters (`ma`) | 10 | 3 | 21.0 | 24.0 |
| common prefix (`mar`) | 10 | 3 | 18.4 | 20.2 |
| common name (`martin`) | 10 | 3 | 18.2 | 19.5 |
| two words (`jean dup`) | 10 | 8 | 32.6 | 34.1 |
| inside a word (`ichel`) | 10 | 3 | 18.6 | 20.9 |
| rare token | 0 | 11 | 39.1 | 41.0 |
| no match | 0 | 11 | 19.8 | 22.7 |
| phone digits (`0612`) | 10 | 8 | 32.1 | 34.5 |
| group word (`service`) | 10 | 9 | 22.5 | 23.7 |
| **all** | | | **22.3** | **38.4** |

Two traps the benchmark caught, kept out since: counting group members
against every contact the user sees (100 ms on a large organization; now
counted from the group's own memberships), and querying both books for
favorites the user doesn't have (now skipped).

## Local development

`env.d/development/backend.defaults` turns the resource server on against
the dev Keycloak, with `drive` as the allowed app and `OIDC_RS_CREATE_USER`
on. The dev realm (`docker/auth/realm.json`) has a `drive` client with the
audience mapper and direct access grants, so a token can be fetched with
curl:

```bash
curl -s http://localhost:8975/realms/contacts/protocol/openid-connect/token \
  -d grant_type=password -d client_id=drive \
  -d client_secret=ThisIsAnExampleKeyForDevPurposeOnly \
  -d username=user1 -d password=user1 -d scope=openid | jq -r .access_token
```

```bash
curl -s "http://localhost:8971/external_api/v1.0/search/?q=mar" \
  -H "Authorization: Bearer $TOKEN" | jq
```

Keycloak imports the realm file only when the realm doesn't exist yet: on
an existing environment, create the `drive` client (with its audience
mapper) in the admin console, or start Keycloak from a fresh database.
