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
| `POST /external_api/v1.0/interactions/` | "the user just picked these": feeds the ranking |
| `GET  /external_api/v1.0/groups/{id}/members/` | expand a group into people, for apps that invite one by one |

The OpenAPI schema is served at `/external_api/v1.0/swagger.json` (UI at
`/external_api/v1.0/swagger/`) when `USE_SWAGGER` is on. It is separate from
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
| `OIDC_RS_INTROSPECTION_CACHE_TIMEOUT` | `60` | seconds an introspection is reused; `0` turns the cache off |
| `OIDC_RS_BACKEND_CLASS` | `core.authentication.resource_server.ContactsResourceServerBackend` | |
| `EXTERNAL_API` | all enabled | `{"search": {"enabled": true}, "interactions": {...}, "group_members": {...}}` |
| `INTERACTION_HALF_LIFE_DAYS` | `30` | ranking decay, see below |
| `SEARCH_STATEMENT_TIMEOUT_MS` | `300` | database budget of one search request |
| `API_EXTERNAL_SEARCH_THROTTLE_RATE` | `300/minute` | per user |
| `API_EXTERNAL_INTERACTIONS_THROTTLE_RATE` | `60/minute` | per user |
| `API_EXTERNAL_GROUP_MEMBERS_THROTTLE_RATE` | `120/minute` | per user |

### What the authorization server must do

- **Register Contacts as a resource server**, with the credentials above,
  allowed to introspect the consumer apps' tokens.
- **Put Contacts in the token audience.** Keycloak (26.x) answers
  `{"active": false}` to a client introspecting a token whose `aud` doesn't
  name it. In Keycloak, add an *audience* mapper (`oidc-audience-mapper`,
  included client audience `contacts`) to each consumer client, as the dev
  realm does for `drive`.
- Return `sub` (matched to the Contacts user) and the audience claim.

### Who may call

A request is served when all of these hold, in order:

1. A `Bearer` token is present and the authorization server says it is
   active, with one of `OIDC_RS_SCOPES`: else **401**.
2. Its `sub` is a Contacts user: else **401**. (Creating users met for the
   first time this way is being added to django-lasuite.)
3. The app it was issued to is in `OIDC_RS_ALLOWED_AUDIENCES`: else **403**.
4. The user passes the `can_access` entitlement: else **403**.

A session cookie or a channel key (`X-Channel-Id`) is never accepted here.
If the authorization server is unreachable, the answer is **503**: the
caller should retry, not refresh its token.

### Introspection cache

A share modal searches on every keystroke; without a cache each one would
wait for the authorization server first. A successful introspection is kept
in the Django cache (Redis) for `OIDC_RS_INTROSPECTION_CACHE_TIMEOUT`
seconds, under a SHA-256 of the token (never the token itself), and never
past the token's own `exp`. Refused tokens are never cached.

Trade-off: a token revoked at the authorization server keeps working here
until its entry expires (60 s by default).

## Endpoints

Contacts and groups have one shape each, told apart by `type`. Both carry
what a picker needs to draw a row: `type`, `id`, `display_name`, `email`,
`is_org`, `source`.

```ts
type ContactResult = {
  type: "contact";
  id: string;                 // an overlay's id when the user has one
  display_name: string;
  email: string | null;       // first of `emails`
  emails: { type: string; value: string }[];
  company: string | null;
  job_title: string | null;
  photo: string | null;       // https URL; photos stored inline are left out
  is_org: boolean;
  source: string;             // "" or the directory it is synced from
};

type GroupResult = {
  type: "group";
  id: string;
  display_name: string;
  email: string | null;       // addressable groups only (shared mailbox)
  members_count: number;
  is_org: boolean;
  source: string;
};
```

### `GET /search/`

| Param | Default | |
| --- | --- | --- |
| `q` | `""` | empty: the user's most picked contacts and groups |
| `types` | `contact,group` | kinds to return |
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
    {"type": "contact", "id": "79aa…", "display_name": "Félix Marchand",
     "email": "felix.marchand@brigny.example",
     "emails": [{"type": "work", "value": "felix.marchand@brigny.example"}],
     "company": "Mairie de Brigny", "job_title": null, "photo": null,
     "is_org": false, "source": ""},
    {"type": "group", "id": "5d0b…", "display_name": "Marchés publics",
     "email": "marches@brigny.example", "members_count": 6,
     "is_org": true, "source": ""}
  ]
}
```

### `POST /interactions/`

Called by the consumer's backend **after a share succeeded**, so the ranking
reflects real shares, not clicks.

```json
{"items": [{"type": "contact", "id": "79aa…"}, {"type": "group", "id": "02e2…"}]}
```

Answers `204`. 1 to 50 items. Ids the user can't see are ignored, so the
answer never tells whether an id exists. A pick of an overlay counts for the
row it covers; a pick of a group counts for the group, not its members.

### `GET /groups/{id}/members/`

Visible, non-trashed members, alphabetical, paginated (`page`, `page_size`,
100 by default), with the group itself:

```json
{
  "count": 8, "next": null, "previous": null,
  "group": {"type": "group", "id": "02e2…", "display_name": "Conseil municipal",
            "email": null, "members_count": 8, "is_org": false, "source": ""},
  "results": [
    {"type": "contact", "id": "c269…", "display_name": "Amandine Aminoff",
     "email": "amandine.aminoff@brigny.example", "emails": […], …},
    {"type": "contact", "id": "3c7e…", "display_name": "Louis Garnier",
     "email": null, "emails": [], …}
  ]
}
```

Members without an email stay, with `"email": null`, so the app can tell
the user who can't be invited. `404` for a group the user can't see.

## Ranking

`core.models.Interaction` keeps one row per (user, contact) or (user,
group): a lifetime `count`, and a `score` that loses half its weight every
`INTERACTION_HALF_LIFE_DAYS`:

```
on pick:   score = score × 0.5^(days since last pick / half-life) + 1
on read:   rank  = score × 0.5^(days since last pick / half-life)
```

So someone shared with every week this month outranks someone shared with
often a year ago. The decay is applied in SQL when reading: no background
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
- **Rank computed in SQL**, only for the user's 1000 most recent picks, and
  only the rows the response needs are loaded, with only the columns it
  shows.
- `members_count` is computed for the returned groups only, from each
  group's own memberships.
- `SEARCH_STATEMENT_TIMEOUT_MS` cancels a search over its budget (**503**),
  so one bad plan can't pile up on the database.

### Benchmark

```bash
docker compose exec backend-dev python manage.py seed_benchmark
docker compose exec backend-dev python manage.py benchmark_search
docker compose exec backend-dev python manage.py seed_benchmark --clear
```

`seed_benchmark` fills the database to the target size (in `benchmark-*`
organizations only; options scale it down), then `benchmark_search` calls
the search function directly (no HTTP, no auth) for common and rare queries
and reports p50 / p95 per query.

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
the dev Keycloak, with `drive` as the allowed app. The dev realm
(`docker/auth/realm.json`) has a `drive` client with the audience mapper and
direct access grants, so a token can be fetched with curl:

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
