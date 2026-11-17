# Entitlements

A pluggable backend that answers "what is this user allowed to do?".
Contacts asks it two questions:

| Key | Meaning |
| --- | ------- |
| `can_access` | the user may use the app at all |
| `can_admin` | the user administers their organization |

The answer also carries `organization_name`, which is what names the
`Organization` row on login (see [organizations.md](organizations.md)).

## Backends

```
core/entitlements/
  __init__.py   get_user_entitlements() + EntitlementsUnavailableError
  factory.py    functools.cache singleton over ENTITLEMENTS_BACKEND
  backends/
    base.py          the interface
    local.py         grants access, not admin (default, dev and test)
    deploycenter.py  calls the DeployCenter API, cached, stale fallback
```

| Setting | Default | Meaning |
| ------- | ------- | ------- |
| `ENTITLEMENTS_BACKEND` | `core.entitlements.backends.local.LocalEntitlementsBackend` | dotted path to the backend class |
| `ENTITLEMENTS_BACKEND_PARAMETERS` | `{}` | kwargs passed to it, as a Python dict literal |
| `ENTITLEMENTS_CACHE_TIMEOUT` | `300` | seconds a response stays fresh |

The default backend grants `can_access` to everyone and `can_admin` to no
one: a deployment that does not configure anything lets every user in,
and has no administrator.

DeployCenter parameters:

```
ENTITLEMENTS_BACKEND=core.entitlements.backends.deploycenter.DeployCenterEntitlementsBackend
ENTITLEMENTS_BACKEND_PARAMETERS='{"base_url":"https://deploycenter.example.com/api/v1.0/entitlements/","service_id":"contacts","api_key":"secret","timeout":10,"oidc_claims":["siret"]}'
```

The DeployCenter backend caches each answer for
`ENTITLEMENTS_CACHE_TIMEOUT` and keeps a second copy for `stale_timeout`
seconds (a backend parameter, one day by default). When the API fails, that
last known answer is used; past it, the call fails.

## Where it is consulted

1. **On login** (`core/authentication/backends.py`): the OIDC backend
   fetches entitlements once, which both warms the cache and supplies the
   organization name. A failure here is swallowed: login still succeeds
   and the organization name is left as it is.
2. **`GET /users/me/`** (`UserMeSerializer`): exposes `can_access` and
   `can_admin` to the SPA.
3. **The frontend** (`src/routes/index.tsx`): sends a user whose
   `can_access` is false to `/no-access`. This is presentation only; the
   API enforces the same rule on its own (see below).

## Where it is enforced

`IsEntitledToAccess` (`core/api/permissions.py`) gates every endpoint that
touches address-book data: contacts, groups, labels, channels and
`GET /tasks/{id}/` (import progress). A user whose `can_access` is false
gets `403`, not just a redirected SPA.

CardDAV is gated at authentication (`carddav/auth.py`). HTTP Basic Auth with
a channel password is the only way into CardDAV (sessions are refused), and
the check runs on every request that reads or writes data, so a sync
password minted before the entitlement was withdrawn stops working too.

Both paths are **fail-closed**: when the entitlements service is
unreachable and nothing usable is cached, access is denied rather than
assumed. With the default local backend this is invisible, since it grants
access to everyone and never fails.

Organization and global `api_key` channels are a separate axis. A request
carrying `contacts:read` passes on its scope, so a service keeps reading
regardless of any individual's entitlement. The [external API](resource_server.md)
acts as the token's user, who must pass `can_access` like in the web app. A
`user`-scoped channel is that user's own credential, so it is checked like
the user (`core/authentication/channel.py`), fail-closed too.

`can_admin` gates the organization's address book: only an administrator
creates, edits, trashes, restores and deletes its contacts and groups,
imports a file into it, or copies one of their own contacts into it (see
[architecture.md](architecture.md)); everyone else in the organization
reads it. The decisions are `Contact.is_writable` and
`ContactGroup.is_writable`, fail-closed like the rest: an unreachable
service makes the organization's rows read-only. With the default local
backend nobody is an administrator, so they can only come from the Django
admin, a source or the demo seed.
