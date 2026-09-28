# Contacts frontend

React app built with [Vite](https://vite.dev/),
[TanStack Router](https://tanstack.com/router/) and
[@gouvfr-lasuite/ui-components](https://github.com/suitenumerique/ui-kit).
Dependencies are managed with pnpm, pinned through corepack
(`packageManager` in `package.json`).

## Getting started

The usual way is the Docker stack from the repository root
(`make bootstrap`, then `make start`): the dev server runs in the
`frontend-dev` service on [http://localhost:8970](http://localhost:8970).

To run it on the host instead (the backend still has to be up, see
`make start-back`):

```bash
corepack enable
pnpm install
VITE_API_ORIGIN=http://localhost:8971 pnpm run dev
```

`VITE_API_ORIGIN` points the app at the backend; unset, it calls
the origin that serves the page (the production setup, behind a reverse
proxy).

Routes are file-based under `src/routes/`. The TanStack Router generator
writes `src/routes.gen.ts` during `pnpm run dev` and `pnpm run build`.

## Useful scripts

- `pnpm run dev`: start the Vite dev server.
- `pnpm run build`: type-check and produce a production build in `dist/`.
- `pnpm run preview`: serve the production build locally.
- `pnpm test`: run the Vitest suite.
- `pnpm run lint`: type-check, then run oxlint.
- `pnpm run format`: format the sources with oxfmt.
- `pnpm run ts:check`: run TypeScript in `--noEmit` mode.
- `pnpm run api:update`: regenerate the Orval client from
  `../backend/core/api/openapi.json` (or `make update-api-front` from the
  repository root).
- `pnpm run analyze`: build with the rollup bundle visualizer.
