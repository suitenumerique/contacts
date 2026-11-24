# Deployment

## Frontend proxy (Caddy)

The frontend production image (`frontend-production` stage of
`src/frontend/Dockerfile`) runs Caddy with `src/frontend/caddy/Caddyfile`.
It serves the built SPA and proxies the backend paths to Django: `/api/`,
`/carddav/`, `/.well-known/carddav`, the admin URL, `/static/` and
`/__heartbeat__/`. The Scalingo deployment runs the same Caddyfile in
front of gunicorn (`bin/scalingo_postfrontend`, `bin/scalingo_run_web`).
Caddy adds `X-Robots-Tag: noindex` to every response, because the whole
app is behind login.

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

Caddy sends a `Content-Security-Policy` header that allows scripts and
requests from the app's own origin only. For each widget that is on (app
launcher, feedback widget), add the origin of its script to
`CONTACTS_FRONTEND_CSP_SCRIPT_SRC` and the origin of its API to
`CONTACTS_FRONTEND_CSP_CONNECT_SRC`. If an origin is missing, the browser
blocks the widget. A frontend built with `API_ORIGIN` needs that origin in
`CONTACTS_FRONTEND_CSP_CONNECT_SRC`.

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

The backend production image (`backend-distroless` stage of
`src/backend/Dockerfile`) runs gunicorn on port `8000`. Its `HEALTHCHECK`
requests `/__heartbeat__/` on `localhost:8000`, so the container is
unhealthy when Django does not answer or when the database is unreachable.
The probe sends the container IP as the `Host` header:
`Production.ALLOWED_HOSTS` always holds it.

### Worker healthcheck

A worker container runs the same image with `python worker.py`. It has no
gunicorn, so the `HEALTHCHECK` of the image fails there. Replace it with
`python worker.py --ping`:

```yaml
healthcheck:
  test: ["CMD", "python", "worker.py", "--ping"]
  interval: 1m
  timeout: 10s
```

The command sends a ping to the worker of its own container and waits up to
5 seconds for the answer. It exits with 0 when the worker answers and with 1
when it does not. The answer goes through Redis, so the probe also fails
when Redis is unreachable.

A probe can fail on a healthy worker: a ping that goes to a worker process
in the middle of an import fails when the import needs more than 5 seconds
to end. Docker marks the container unhealthy only after `retries` failed
probes in a row (3 by default). With the default retry count, one isolated
failed probe does not mark the container unhealthy.

### Distroless image

The image is built on `gcr.io/distroless/cc-debian13:nonroot`: no shell, no
package manager. It holds a standalone Python installed by uv, the virtual
environment, the application and the collected static files.

- It runs as the image's `nonroot` user (uid 65532).
- Commands are given in exec form, for example
  `python manage.py migrate`. A command that needs `sh` does not run.

The release workflow publishes this stage. It is the last stage of the
Dockerfile, so a build without `--target` also produces it.

`make test-back-distroless` builds the image and checks the user, the
native modules, the CA store, the static files and the Django system
checks. It needs no database. CI runs it on amd64 and arm64.

### Image with a shell

The `backend-production` stage builds the same application on alpine, with
a shell and the `entrypoint` script. It runs as uid 10000 and has the same
`HEALTHCHECK`. The release workflow does not publish it.

```bash
docker build --target backend-production -t contacts-backend-production src/backend
```

## Legal pages

Contacts does not host the legal pages. A deployment sets their URLs.

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `FRONTEND_TERMS_OF_SERVICE_URL` | _(unset)_ | Terms of use link in the user menu. |
| `THEME_CUSTOMIZATION_FILE_PATH` | `contacts/configuration/theme/default.json` | JSON file that holds the footer of the home page, with its legal links (`footer.<language>.legalLinks`). |

The legal links of the default file point to `#`. A production deployment
must supply its own copy with the real URLs.
