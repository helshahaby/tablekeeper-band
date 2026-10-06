import { createFileRoute } from "@tanstack/react-router";
import { getDb } from "@/lib/db.server";

// Liveness + readiness: opens the database, applies pending migrations, reports counts.
export const Route = createFileRoute("/api/health")({
  server: {
    handlers: {
      GET: async () => {
        try {
          const db = await getDb();
          const [row] = await db.query<{ restaurants: number; migrations: number }>(
            "SELECT (SELECT count(*)::int FROM restaurants) AS restaurants, (SELECT count(*)::int FROM schema_migrations) AS migrations",
          );
          return Response.json({ ok: true, engine: db.engine, ...row });
        } catch (e) {
          console.error("[health]", e);
          return Response.json({ ok: false }, { status: 503 });
        }
      },
    },
  },
});
