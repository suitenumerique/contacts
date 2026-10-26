# Channels (service API keys)

Channels let other services (primarily the rest of La Suite) and CardDAV
clients call this app without a user session. The model mirrors
suitenumerique/messages: a `Channel` row holds public `settings` (scopes,
optional `expires_at`) and `encrypted_settings` (SHA-256 hashes of the active
API keys; the raw key is shown exactly once at creation/rotation).

## Authentication

`api_key` channels send two headers on every REST request:

```
X-Channel-Id: <channel uuid>
X-API-Key: <raw secret>
```

`carddav` channels use HTTP Basic Auth instead, see [carddav.md](carddav.md).

## Scope levels

| Level          | Bound to        | `on_behalf_of`                   | Created via |
| -------------- | --------------- | --------------------------------- | ----------- |
| `user`         | one User        | implied (the bound user)          | API (`POST /api/v1.0/channels/`), CLI |
| `organization` | one Organization| required; must belong to the org  | Django admin, CLI |
| `global`       | nothing         | required; any user                | Django admin, CLI |

## Scopes

| Scope           | Channel type | Grants                                          |
| --------------- | ------------ | ----------------------------------------------- |
| `contacts:read` | `api_key`    | Read the acting user's contacts (REST).         |
| `groups:read`   | `api_key`    | Read the acting user's groups (REST).           |
| `carddav:read`  | `carddav`    | Read over [CardDAV](carddav.md).                |
| `carddav:write` | `carddav`    | Read and write over CardDAV.                    |

The REST scopes are read-only: an `api_key` channel can never mutate an
address book. Writes are reachable only over CardDAV, with a credential of
`type="carddav"`. A device sync password cannot be replayed against the
REST API: REST authentication only looks up `api_key` channels and CardDAV
authentication only `carddav` ones. Validation also keeps each type to its
own scopes.

A service that reads address books for many users gets an `organization`
or `global` channel with `contacts:read` + `groups:read`. Suite apps that
suggest people to their user use the [external API](resource_server.md)
instead, with that user's own access token.

## Creating a channel from the CLI

Organization- and global-scoped channels act on behalf of other users, so
they are not creatable over the API. Mint them with:

```bash
python manage.py create_channel --name "Directory service" \
  --scopes contacts:read,groups:read
```

```
--type          api_key (default) or carddav
--scope-level   global (default), organization, user
--organization  Organization external_id, for --scope-level organization
--user          identity email, for --scope-level user
--scopes        repeat the flag or pass a comma-separated list
--expires-at    optional ISO-8601 datetime
--groups        carddav only: also sync the groups (see carddav.md)
```

The secret is printed once: `X-Channel-Id` + `X-API-Key` for REST channels,
a single Basic Auth password for `carddav` ones.

## Django admin

The admin creates channels without a key. Save the channel, then use
"Rotate key" on its change page: it replaces every existing key with a new
one and shows it once, together with the Basic Auth password for a
`carddav` channel.

See [messages-integration.md](messages-integration.md) for the credentials
Messages needs.

## Endpoints

```
GET    /api/v1.0/channels/                          list own channels
POST   /api/v1.0/channels/                          create (returns api_key once)
GET    /api/v1.0/channels/{id}/                     retrieve (no key)
PATCH  /api/v1.0/channels/{id}/                     update name/scopes/is_active
DELETE /api/v1.0/channels/{id}/                     delete
POST   /api/v1.0/channels/{id}/regenerate-api-key/  rotate (old keys die)
```

These endpoints only ever list or create the requesting user's own
`user`-scoped channels. `regenerate-api-key` invalidates all previous keys
immediately.
