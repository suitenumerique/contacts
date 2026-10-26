# Organizations

Every user belongs to exactly one `Organization`, created automatically on
first login. Organizations are the scoping unit for everything in contacts:
org-wide directory rows, organization-bound channels, and what the
[external API](resource_server.md) suggests to other Suite apps.

## Resolving the organization identifier

The org `external_id` comes from exactly one source, picked by
`OIDC_USERINFO_ORGANIZATION_CLAIM`:

- **Setting non-empty: that OIDC claim.** For example `siret`, with an
  identity provider that carries the legal SIRET number (such as
  ProConnect). There is no fallback to the email domain, which could attach
  a user to the wrong org.
- **Setting empty (the default): the email domain**, lowercased. Users of
  `@ministry.example` share one organization.

The identifier is resolved again on **every login**, not only the first:

- On first login, a missing identifier (claim absent, or no email) refuses
  the login: the user cannot be created without an organization.
- On later logins, a missing identifier is logged as an error and the user
  keeps their current organization.
- When the identifier changed, the user moves to the matching organization
  (created if needed). Their personal contacts, groups and user-scoped
  channels move with them; overlays of the old organization's rows, and
  group memberships or favorites pointing at them, are dropped.

The organization's display name comes from the entitlements backend
(`organization_name`, see [entitlements.md](entitlements.md)) and is
refreshed on each login that provides one. A login without one (the local
backend never sends it) leaves the name as it is; an organization that never
got a name displays as its `external_id`.

## Development setup

The dev Keycloak realm maps a `siret` user attribute into the `siret` claim
(`docker/auth/realm.json`), and `compose.yaml` sets
`OIDC_USERINFO_ORGANIZATION_CLAIM=siret` for `backend-dev`/`worker-dev` only.
The hermetic `backend-test` service keeps the email-domain source, and the
`Test` settings class pins the setting to `""` so the test suite is
environment-independent.

The realm holds two organizations: `user1`, `user2` and `user3` share siret
`12345678900011`, while `user1.2` and `user2.2` belong to `98765432100012`
(passwords are the user names). Both are fictional: they fail the SIREN
checksum.

`make demo` seeds an organization with `external_id=12345678900011` matching
the first set, so logging in as `user1` lands on demo data.
