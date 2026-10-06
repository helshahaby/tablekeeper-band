# Tablekeeper — single self-contained image: PostgreSQL 16 + Node 22 app server.
# Runtime needs NO outbound network. The BUILD stage downloads npm packages and base images
# (see RUN.md "Offline build" for the vendored alternative, which is not yet verified).

# ---- build ----
FROM node:22-bookworm-slim AS build
WORKDIR /app
RUN npm install -g bun@1.3
COPY package.json bun.lock bunfig.toml ./
RUN bun install --frozen-lockfile
COPY . .
# Target a plain Node server instead of the default edge/worker output.
RUN NITRO_PRESET=node-server npx vite build

# ---- node binary ----
FROM node:22-bookworm-slim AS node

# ---- runtime ----
FROM postgres:16-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=build /app/.output /app/.output
COPY docker/entrypoint.sh /usr/local/bin/tablekeeper-entrypoint
RUN chmod +x /usr/local/bin/tablekeeper-entrypoint
ENV PORT=3000 \
    NODE_ENV=production \
    PGDATA=/var/lib/postgresql/data
EXPOSE 3000
VOLUME ["/var/lib/postgresql/data"]
ENTRYPOINT ["tablekeeper-entrypoint"]
