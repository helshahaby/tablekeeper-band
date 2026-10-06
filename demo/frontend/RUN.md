# Running Tablekeeper offline

The service runs with **no outbound network**. It needs only:
- the app server (Node 22, built output in `.output/`)
- a PostgreSQL database (bundled in the Docker image), or the embedded PGlite fallback

No hosted backend, CDN, web font or third-party API is called at runtime.

## 1. Docker (recommended)

Build (this step DOES need network to fetch base images and npm packages):

```sh
docker build -t tablekeeper .
```

Run with networking disabled:

```sh
docker run -d --name tablekeeper --network none tablekeeper
sh scripts/offline-smoke.sh tablekeeper
```

`--network none` gives the container no network at all, so the browser cannot reach it from the host.
To use the app in a browser while still blocking outbound traffic, use an internal network:

```sh
docker network create --internal tk-offline
docker run -d --name tablekeeper --network tk-offline tablekeeper
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' tablekeeper
# open http://<that-ip>:3000 from the host (Linux). Outbound internet is blocked by --internal.
```

What the container does on start (`docker/entrypoint.sh`):
1. Initialises a PostgreSQL 16 cluster in `/var/lib/postgresql/data` (first start only), listening on `127.0.0.1` only.
2. Creates the `tablekeeper` database.
3. Starts the app on port 3000. The first request applies `db/migrations/*.sql`, then `db/seed/*.sql`, tracked in `schema_migrations`.

Persist data across restarts with `-v tk-data:/var/lib/postgresql/data`.

## 2. Without Docker

```sh
bun install                 # needs network once
bun run build:node          # NITRO_PRESET=node-server vite build
DATABASE_URL=postgres://user@127.0.0.1:5432/tablekeeper PORT=3000 bun run start:node
```

Leave `DATABASE_URL` unset to use embedded PGlite stored in `./.data/pglite` (or `PGLITE_DIR`).

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | unset | PostgreSQL connection URL. Unset = embedded PGlite. |
| `PGLITE_DIR` | `./.data/pglite` | PGlite data folder. `memory://` for throwaway. |
| `PORT` | `3000` | HTTP port. |
| `STAFF_EMAILS` | unset | Comma-separated emails that get the staff role when they sign up. |
| `SKIP_SEED` | unset | `true` to skip demo restaurants and seat mandates. |
| `DB_POOL_MAX` | `10` | PostgreSQL pool size. |

## Health check

`GET /api/health` opens the database, applies pending migrations and returns
`{"ok":true,"engine":"postgres","restaurants":4,"migrations":2}`.

## Database files

- `db/migrations/0001_schema.sql` — schema, the no-overlap exclusion constraint, booking functions. Plain PostgreSQL >= 14 plus the `btree_gist` extension (ships with standard PostgreSQL).
- `db/seed/0001_seed.sql` — 4 demo restaurants with tables, 4 pending factory stages, 4 generic seat mandates. No handoffs, costs or recoveries are seeded.

## Verification status

| Check | Status |
|---|---|
| Typecheck | Passed |
| Node build (`NITRO_PRESET=node-server`) | Passed outside Docker |
| Built server + local PostgreSQL 17, fresh empty database: migrations, sign-up, booking, staff view, 50-way stress test, 5x retry | Passed |
| Embedded PGlite in development: sign-up, booking, stress test | Passed |
| `docker build` | **Not run** — Docker is unavailable in the environment this was prepared in |
| Container under `--network none` (`scripts/offline-smoke.sh`) | **Not run** |
| Outbound traffic actually blocked during the e2e run | **Not verified** — network namespaces were not permitted in the preparation environment |
| Built server with `DATABASE_URL` unset (PGlite in production build) | **Not verified** |
| Building the image with no network (vendored packages) | **Not supported yet** — `docker build` downloads packages |

## Remaining dependencies

- **Build time:** Docker Hub images (`node:22-bookworm-slim`, `postgres:16-bookworm`), the npm registry, and bun. Not needed at runtime.
- **`@supabase/supabase-js`** stays in `package.json` only because auto-generated files in `src/integrations/supabase/` (left by the hosted editor) import it. No page or server code uses it. It is bundled, not called.
- **`@lovable.dev/vite-tanstack-config`** is the build config wrapper. Build-time only.
- **`drizzle-kit` / `drizzle/`** are leftovers from the hosted editor's migration tooling. Not used by the offline service.
