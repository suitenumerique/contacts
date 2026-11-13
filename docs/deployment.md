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

Contacts does not host the legal pages. A deployment sets their URLs, and
the app shows the links in two places.

| Page | Where the link shows | How to set it |
| ---- | -------------------- | ------------- |
| Terms of use | User menu (signed-in users) | `FRONTEND_TERMS_OF_SERVICE_URL` |
| Legal notice, privacy policy, accessibility statement | Footer of the home page (anonymous users) | `footer.<language>.legalLinks` in the theme customization file |

### Theme customization file

The backend reads a JSON file and serves it to the frontend in
`theme_customization` of `GET /api/v1.0/config/`.

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `FRONTEND_TERMS_OF_SERVICE_URL` | _(unset)_ | URL of the terms of use. Unset: the user menu has no link. |
| `THEME_CUSTOMIZATION_FILE_PATH` | `contacts/configuration/theme/default.json` in the backend | Path of the JSON file, readable by the backend container. Empty: no customization, so the footer has no link. |
| `THEME_CUSTOMIZATION_CACHE_TIMEOUT` | `86400` | Seconds the backend keeps the parsed file in its cache. A change to the file shows after this delay. |

The default file is a placeholder: its legal links point to `#`. A
production deployment must supply its own file, for example a copy of
`src/backend/contacts/configuration/theme/default.json` with the real URLs,
mounted in the backend container.

The `footer` key holds one object per language (`en`, `fr`, `nl`) and a
`default` object. The frontend merges `default` with the object of the
current language; a key of the language object replaces the same key of
`default`. Each object takes the props of the UI kit `Footer` component:
`legalLinks`, `externalLinks`, `license` and `logo`.

```json
{
  "footer": {
    "default": {
      "externalLinks": [
        { "label": "Github", "href": "https://github.com/suitenumerique/contacts/" }
      ]
    },
    "fr": {
      "legalLinks": [
        { "label": "Mentions légales", "href": "https://example.org/mentions-legales" },
        { "label": "Conditions générales d'utilisation", "href": "https://example.org/cgu" },
        { "label": "Politique de confidentialité", "href": "https://example.org/confidentialite" },
        { "label": "Accessibilité : non conforme", "href": "https://example.org/accessibilite" }
      ]
    }
  }
}
```

Put `legalLinks` in `default` to show the same links in all languages.
