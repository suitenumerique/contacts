# AGENTS.md

Guidance for coding agents working in this repository.

## Project Overview

La Suite Contacts is a contacts application, nothing more. Suite-wide
people/group suggestion (autocomplete) lives in the separate **directory**
service, which federates this app as one of its sources; this repo is just
the contacts app itself:
- **Backend**: Django 5 REST API with PostgreSQL (+ Redis, dramatiq worker)
- **Frontend**: React 19 + Vite + TanStack Router/Query + @gouvfr-lasuite/ui-components (pnpm via corepack, oxlint/oxfmt)
- **Authentication**: Keycloak OIDC; organizations come from a configurable claim (`siret` in dev/prod)

Core concepts: personal + org-wide contacts, groups and labels; read-only
rows provisioned from an external source, with per-user **overlays** (fork) so
local edits don't break the link; trash (corbeille); channel API keys giving
the directory service read-only access; async vCard/CSV import.

Autocomplete, directory backends and interaction ("+1") ranking are out of
scope here: they are the directory service's job.

## Common Commands

### Development Setup
```bash
make bootstrap          # Initial setup: builds containers, runs migrations, starts services
make start              # Start all services
make stop               # Stop all containers
make down               # Stop and remove containers and networks (DB is lost)
make demo               # Reset the DB and seed demo data (log in as user1/user1)
```

### Backend Development
```bash
make test-back -- path/to/test.py::test_name   # Run specific test
make test-back-parallel                        # Run all tests in parallel
make lint-back                                 # ruff format + check + pylint
make migrate / make makemigrations             # Django migrations
make shell-back-django                         # Django shell
make shell-db                                  # PostgreSQL shell
bash bin/update_openapi_schema                 # Regenerate core/api/openapi.json
```

### Frontend Development
```bash
make install-front      # (Re)install deps, regenerate pnpm-lock.yaml
make lint-front         # oxfmt + tsc + oxlint
make typecheck-front    # tsc --noEmit
make test-front         # vitest
```

## Architecture

### Backend Structure (`src/backend/`)
- `contacts/` - Django project configuration (settings, urls, wsgi)
- `core/` - Main application code:
  - `models.py` - Organization, User, Contact, ContactGroup, ContactLabel, Channel
  - `api/` - DRF viewsets/serializers (+ `viewsets_channels.py`, `viewsets_task.py`)
  - `authentication/` - OIDC backend + channel API-key authentication
  - `services/vcf_service.py`, `services/csv_service.py` - vCard / CSV parse+serialize
  - `tests/` - pytest test files
- `carddav/` - CardDAV endpoint over `core.models.Contact`; its own models
  (`SyncCounter`, `ContactChange`, ...) only hold sync bookkeeping (see docs/carddav.md)

### Frontend Structure (`src/frontend/`)
- `src/features/contacts/` - the contacts feature: `pages/` (one per route),
  `components/`, `hooks/` (adapters over the generated client), `modals.tsx`
  (shared modal host), `navigation.ts` (URL search state)
- `src/features/api/gen/` - orval client generated from `core/api/openapi.json`
  (`make update-api-front`, never edit by hand)
- `src/features/{auth,api,config,layouts,ui,i18n,...}` - app shell shared with other Suite apps
- `src/routes/` - TanStack Router file routes: `/contacts/{-$contactId}`,
  `/groups/{-$groupId}`, `/trash/{-$contactId}` under the `_app` layout
  (generated `routes.gen.ts`, gitignored)
- UI state lives in the URL (open item, `q`, `label`, `favorites`, `sort`);
  search params are plain strings (see `features/app/searchParams.ts`)

### Development Services

| Service        | URL / Port                                     |
|----------------|------------------------------------------------|
| **Frontend**   | [http://localhost:8970](http://localhost:8970) |
| **Backend API**| [http://localhost:8971](http://localhost:8971) |
| **Redis**      | 8974                                           |
| **Keycloak**   | [http://localhost:8975](http://localhost:8975) |
| **PostgreSQL** | 8976                                           |

## Key Conventions

- Tests run in the hermetic `backend-test` compose service (only
  `backend.defaults`, never `backend.local`); use `bin/pytest`.
- Channel API keys are hashed at rest; raw keys are returned exactly once.
- Ruff line length 88; pylint must stay at 10.0; frontend must pass
  `pnpm run format && pnpm run lint` (`lint` includes `ts:check`).
