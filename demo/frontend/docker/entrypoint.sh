#!/bin/sh
# Starts a local PostgreSQL bound to 127.0.0.1 inside the container, then the app.
# The app applies db/migrations and db/seed (bundled at build time) on first request.
set -e

mkdir -p "$PGDATA" /run/postgresql
chown -R postgres:postgres "$PGDATA" /run/postgresql

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "[entrypoint] initialising database cluster"
  # Trust auth is acceptable only because Postgres listens on 127.0.0.1 inside this container.
  gosu postgres initdb -D "$PGDATA" -U tablekeeper --auth=trust >/dev/null
fi

gosu postgres pg_ctl -D "$PGDATA" -o "-c listen_addresses=127.0.0.1 -p 5432" -w start >/dev/null

if ! gosu postgres psql -h 127.0.0.1 -U tablekeeper -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='tablekeeper'" | grep -q 1; then
  gosu postgres createdb -h 127.0.0.1 -U tablekeeper tablekeeper
fi

export DATABASE_URL="${DATABASE_URL:-postgres://tablekeeper@127.0.0.1:5432/tablekeeper}"

shutdown() {
  [ -n "$APP_PID" ] && kill "$APP_PID" 2>/dev/null || true
  gosu postgres pg_ctl -D "$PGDATA" -m fast stop >/dev/null || true
  exit 0
}
trap shutdown TERM INT

echo "[entrypoint] starting app on port ${PORT}"
node /app/.output/server/index.mjs &
APP_PID=$!
wait "$APP_PID"
