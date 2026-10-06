// Local database access. No hosted services.
// - DATABASE_URL set  -> PostgreSQL over TCP (used in Docker).
// - DATABASE_URL unset -> embedded PostgreSQL (PGlite, WASM) persisted to PGLITE_DIR (default ./.data/pglite).
// Migrations and seeds are bundled into the server at build time and applied on first use.

const migrationFiles = import.meta.glob("/db/migrations/*.sql", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;
const seedFiles = import.meta.glob("/db/seed/*.sql", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

export type Db = {
  engine: "postgres" | "pglite";
  query<T = Record<string, unknown>>(text: string, params?: unknown[]): Promise<T[]>;
  exec(text: string): Promise<void>;
};

let dbPromise: Promise<Db> | undefined;

export function getDb(): Promise<Db> {
  if (!dbPromise) {
    dbPromise = open().catch((e) => {
      dbPromise = undefined;
      throw e;
    });
  }
  return dbPromise;
}

async function open(): Promise<Db> {
  const url = process.env["DATABASE_URL"];
  let db: Db;
  if (url) {
    const { default: postgres } = await import("postgres");
    const sql = postgres(url, { max: Number(process.env["DB_POOL_MAX"] ?? 10), onnotice: () => {} });
    // Migrate on ONE reserved connection so the session-level advisory lock, every
    // migration transaction and the unlock share a session.
    const conn = await sql.reserve();
    try {
      await migrate({
        engine: "postgres",
        query: async (text, params = []) =>
          (await conn.unsafe(text, params as never[])) as unknown as never[],
        exec: async (text) => {
          await conn.unsafe(text);
        },
      });
    } finally {
      conn.release();
    }
    db = {
      engine: "postgres",
      query: async (text, params = []) =>
        (await sql.unsafe(text, params as never[])) as unknown as never[],
      exec: async (text) => {
        await sql.unsafe(text);
      },
    };
  } else {
    const { PGlite } = await import("@electric-sql/pglite");
    const { btree_gist } = await import("@electric-sql/pglite/contrib/btree_gist");
    const dir = process.env["PGLITE_DIR"] ?? "./.data/pglite";
    if (!dir.startsWith("memory://")) {
      const { mkdirSync } = await import("node:fs");
      mkdirSync(dir, { recursive: true });
    }
    const pg = await PGlite.create(dir, { extensions: { btree_gist } });
    db = {
      engine: "pglite",
      query: async (text, params = []) => (await pg.query(text, params)).rows as never[],
      exec: async (text) => {
        await pg.exec(text);
      },
    };
    await migrate(db);
  }
  return db;
}

async function migrate(db: Db) {
  await db.exec(`CREATE TABLE IF NOT EXISTS schema_migrations (
    name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())`);
  const lock = db.engine === "postgres";
  if (lock) await db.query("SELECT pg_advisory_lock(727274)");
  try {
    const files = [
      ...Object.entries(migrationFiles).sort(([a], [b]) => a.localeCompare(b)),
      ...(process.env["SKIP_SEED"] === "true"
        ? []
        : Object.entries(seedFiles).sort(([a], [b]) => a.localeCompare(b))),
    ];
    const applied = new Set(
      (await db.query<{ name: string }>("SELECT name FROM schema_migrations")).map((r) => r.name),
    );
    for (const [path, sql] of files) {
      const name = path.replace(/^\/db\//, "");
      if (applied.has(name)) continue;
      try {
        await db.exec(`BEGIN;\n${sql}\n;INSERT INTO schema_migrations(name) VALUES ('${name}');\nCOMMIT;`);
      } catch (e) {
        await db.exec("ROLLBACK").catch(() => {});
        console.error(`[db] migration ${name} failed`, e);
        throw e;
      }
      console.log(`[db] applied ${name}`);
    }
  } finally {
    if (lock) await db.query("SELECT pg_advisory_unlock(727274)");
  }
}
