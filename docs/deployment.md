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
| `CONTACTS_FRONTEND_CSP_SCRIPT_SRC` | _(empty)_ | Space-separated list of extra origins that can serve scripts (`script-src`). Empty: only the app's own origin. |
| `CONTACTS_FRONTEND_CSP_CONNECT_SRC` | _(empty)_ | Space-separated list of extra origins the app can call (`connect-src`). Empty: only the app's own origin. |

To turn the admin filter off, leave `DJANGO_ADMIN_IP_ALLOWLIST` unset. Do
not set it to an empty value: an empty list matches no client, so every
admin request gets `403`.

Caddy walks `X-Forwarded-For` right to left and takes the first address
that is not a trusted proxy (`trusted_proxies_strict`), so a header sent or
extended by the client never passes the allowlist. It then hands Django that
address as a single `X-Forwarded-For` entry.

### Content-Security-Policy

Caddy sends one `Content-Security-Policy` header on every response. It
starts from `default-src 'none'` and allows the app's own origin for
scripts, styles, fonts, images and API calls. It also allows inline
styles (the UI kit needs them), `data:` and `blob:` images, and images
from any `https:` URL, because a contact photo can be a remote URL.

Each origin outside the app must be added with a variable:

| Feature | `CONTACTS_FRONTEND_CSP_SCRIPT_SRC` | `CONTACTS_FRONTEND_CSP_CONNECT_SRC` |
| ------- | ---------------------------------- | ----------------------------------- |
| App launcher (`FRONTEND_LAGAUFRE_ENABLED`) | Origin of `FRONTEND_LAGAUFRE_WIDGET_PATH`. Default: `https://static.suite.anct.gouv.fr` | Origin of `FRONTEND_LAGAUFRE_WIDGET_API_URL`. Default: `https://lasuite.numerique.gouv.fr` |
| Feedback widget (`FRONTEND_FEEDBACK_MESSAGES_WIDGET_ENABLED`) | Origin of `FRONTEND_FEEDBACK_MESSAGES_WIDGET_PATH` | Origin of `FRONTEND_FEEDBACK_MESSAGES_WIDGET_API_URL` |
| API on another origin (frontend built with `API_ORIGIN`) | | That origin |

For example, with the app launcher and its default URLs:

```
CONTACTS_FRONTEND_CSP_SCRIPT_SRC=https://static.suite.anct.gouv.fr
CONTACTS_FRONTEND_CSP_CONNECT_SRC=https://lasuite.numerique.gouv.fr
```

Without these values the browser blocks the widget, and the rest of the
app still works. Use origins only (scheme and host): a value that
contains `;` or a quote can change the rest of the policy.

The policy does not allow inline scripts. The Swagger and Redoc pages
(`USE_SWAGGER`, off in the production settings) need them, so they do not
work behind this Caddy.

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
the healthcheck, the backend proxying, the SPA fallback, the
`Content-Security-Policy` header, and the admin allowlist with spoofed and
trusted `X-Forwarded-For`. CI runs it.

## Backend image

The backend production image (`backend-production` stage of
`src/backend/Dockerfile`) runs gunicorn on port `8000`. Its `HEALTHCHECK`
requests `/__heartbeat__/` on `localhost:8000`, so the container is
unhealthy when Django does not answer or when the database is unreachable.
The probe sends the container IP as the `Host` header:
`Production.ALLOWED_HOSTS` always holds it.
