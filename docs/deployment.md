# Deployment

## Frontend proxy (Caddy)

The frontend production image (`frontend-production` stage of
`src/frontend/Dockerfile`) runs Caddy with `src/frontend/caddy/Caddyfile`.
It serves the built SPA and proxies the backend paths to Django: `/api/`,
`/carddav/`, `/.well-known/carddav`, the admin URL, `/static/` and
`/__heartbeat__/`. The Scalingo deployment runs the same Caddyfile in
front of gunicorn (`bin/scalingo_postfrontend`, `bin/scalingo_run_web`).

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `PORT` | `8080` | Port Caddy listens on. Keep `8080` in the Docker image: its HEALTHCHECK hardcodes it. |
| `CONTACTS_FRONTEND_ROOT` | `/app` | Directory of the built SPA. |
| `CONTACTS_FRONTEND_BACKEND_SERVER` | `localhost:8000` | `host:port` of the Django backend. |
| `DJANGO_ADMIN_URL` | `admin` | Django admin path, without slashes. Read by Django too, so set it on both. |
| `DJANGO_ADMIN_IP_ALLOWLIST` | `0.0.0.0/0 ::/0` | Space-separated CIDR list of client IPs allowed on the admin URL; others get `403` from Caddy. The default allows everyone. |
| `CONTACTS_FRONTEND_TRUSTED_PROXIES` | _(empty)_ | Space-separated CIDR list of upstream proxies whose `X-Forwarded-For` sets the client IP. Empty: the TCP peer is the client. |

To turn the admin filter off, leave `DJANGO_ADMIN_IP_ALLOWLIST` unset. Do
not set it to an empty value: an empty list matches no client, so every
admin request gets `403`.

Caddy walks `X-Forwarded-For` right to left and takes the first address
that is not a trusted proxy (`trusted_proxies_strict`), so a header sent or
extended by the client never passes the allowlist. It then hands Django that
address as a single `X-Forwarded-For` entry.

### Client address in Django

Django reads the client address (CardDAV login throttling) from
`REMOTE_ADDR`, or from `X-Forwarded-For` when `NUM_PROXIES` says how many
trusted proxies sit in front of it. Behind this Caddy, set
`NUM_PROXIES=1`. The default, `0`, ignores the header, which any client can
write when no proxy sets it.

### Scalingo

`bin/scalingo_run_web` defaults `NUM_PROXIES=1` and
`CONTACTS_FRONTEND_TRUSTED_PROXIES=private_ranges`: the Scalingo routers set
`X-Forwarded-For`, the container port is only reachable through them, and
their IP ranges are not published. Do not use `private_ranges` when
untrusted machines share the private network with Caddy.

### Smoke test

`make test-front-production` builds the image and checks the heartbeat,
the healthcheck, the backend proxying, the SPA fallback, and the admin
allowlist with spoofed and trusted `X-Forwarded-For`. CI runs it.

## Backend image

The backend production image (`backend-production` stage of
`src/backend/Dockerfile`) runs gunicorn on port `8000`. Its `HEALTHCHECK`
requests `/__heartbeat__/` on `localhost:8000`, so the container is
unhealthy when Django does not answer or when the database is unreachable.
The probe sends the container IP as the `Host` header:
`Production.ALLOWED_HOSTS` always holds it.

## Legal pages

Contacts does not host the legal pages. A deployment sets their URLs.

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `FRONTEND_TERMS_OF_SERVICE_URL` | _(unset)_ | Terms of use link in the user menu. |
| `THEME_CUSTOMIZATION_FILE_PATH` | `contacts/configuration/theme/default.json` | JSON file that holds the footer of the home page, with its legal links (`footer.<language>.legalLinks`). |

The legal links of the default file point to `#`. A production deployment
must supply its own copy with the real URLs.
