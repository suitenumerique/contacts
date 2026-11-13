# Contacts

A modern, open-source contacts application for La Suite. It manages personal
and organization-wide address books, and serves them to the standalone
directory service, one of the sources behind the people/group suggestions
you get across every La Suite app (Docs, Messages, Calendars, ...).

## Why use Contacts ❓

### Manage your address book
- 👤 Create, edit and organize contacts, groups and labels (libellés)
- ⭐ Favorites, search, vCard (.vcf) and CSV import/export
- 🗑️ Trash (corbeille) with restore and a configurable retention period
- 🏢 Organization-wide directory entries next to personal contacts

### Feed the whole Suite
- 🔎 Read-only, per-user access to contacts and groups for the Suite's
  directory service, through scoped [channel API keys](docs/channels.md)
- 📇 [CardDAV](docs/carddav.md) read/write sync, so phones and desktop
  address books talk to Contacts directly
- 🔗 Read-only rows provisioned from an external source, with personal
  **overlays** so a user can edit locally without losing the link
- 🏛️ Organizations resolved from a configurable OIDC claim (e.g. `siret`)

### Self-host
- 🚀 Django + PostgreSQL + React, easy to install, scalable and secure

## Getting started 🔧

### Prerequisite

A recent Docker with [Docker Compose](https://docs.docker.com/compose/install):

```bash
$ docker -v
  Docker version 27.x
```

### Project bootstrap

```bash
$ make bootstrap
```

Then seed demo data and open the app:

```bash
$ make demo
```

In your development workflow, the main commands you should use are:

```
# Stop all containers
$ make stop

# Start all containers, without full bootstrap
$ make start

# View all available commands
$ make help
```

### Development Services

| Service     | URL / Port                                     | Credentials       |
| ----------- | ---------------------------------------------- | ----------------- |
| Frontend    | [http://localhost:8970](http://localhost:8970) | `user1` / `user1` |
| Backend API | [http://localhost:8971](http://localhost:8971) |                   |
| Redis       | 8974                                           |                   |
| Keycloak    | [http://localhost:8975](http://localhost:8975) | `admin` / `admin` |
| PostgreSQL  | 8976                                           |                   |

### OpenAPI client

The frontend API client is generated with [Orval](https://orval.dev/) from
the OpenAPI schema that [drf-spectacular](https://drf-spectacular.readthedocs.io/)
produces from the backend, committed in `src/backend/core/api/openapi.json`.

To regenerate the schema, then the frontend client:

```bash
$ bash bin/update_openapi_schema
$ make update-api-front
```

## Documentation 📚

- [Architecture](docs/architecture.md)
- [CardDAV](docs/carddav.md)
- [Channels / service API keys](docs/channels.md)
- [Deployment (frontend proxy, admin allowlist, legal pages)](docs/deployment.md)
- [Entitlements](docs/entitlements.md)
- [Messages integration](docs/messages-integration.md)
- [Organizations](docs/organizations.md)

## Contributing 🙌

This project is intended to be community-driven, see the
[contributing guidelines](CONTRIBUTING.md) (including how releases are cut).

## License 📝

This work is released under the MIT License (see [LICENSE](LICENSE)).
