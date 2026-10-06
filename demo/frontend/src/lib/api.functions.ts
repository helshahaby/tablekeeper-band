import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";
import { getDb } from "./db.server";
import * as auth from "./auth.server";

const iso = (v: unknown) => (v instanceof Date ? v.toISOString() : String(v));

/* ---------- auth ---------- */

export const getMe = createServerFn({ method: "GET" }).handler(async () => auth.currentUser());

const creds = z.object({ email: z.string().trim().email().max(200), password: z.string().min(8).max(200) });

export const signUpFn = createServerFn({ method: "POST" })
  .inputValidator((d) => creds.parse(d))
  .handler(async ({ data }) => {
    await auth.signUp(data.email, data.password);
    return { ok: true };
  });

export const signInFn = createServerFn({ method: "POST" })
  .inputValidator((d) => creds.parse(d))
  .handler(async ({ data }) => {
    await auth.signIn(data.email, data.password);
    return { ok: true };
  });

export const signOutFn = createServerFn({ method: "POST" }).handler(async () => {
  await auth.signOut();
  return { ok: true };
});

/* ---------- restaurants ---------- */

export type Restaurant = {
  id: string;
  slug: string;
  name: string;
  cuisine: string;
  city: string;
  timezone: string;
  opens_at: string;
  closes_at: string;
  slot_minutes: number;
  description: string;
};
export type DiningTable = { id: string; label: string; seats: number };

export const listRestaurants = createServerFn({ method: "GET" }).handler(async () => {
  const db = await getDb();
  return db.query<Restaurant>("SELECT * FROM restaurants ORDER BY name");
});

export const getRestaurant = createServerFn({ method: "GET" })
  .inputValidator((d) => z.object({ slug: z.string().max(100) }).parse(d))
  .handler(async ({ data }) => {
    const db = await getDb();
    const [r] = await db.query<Restaurant>("SELECT * FROM restaurants WHERE slug = $1", [data.slug]);
    if (!r) return null;
    const tables = await db.query<DiningTable>(
      "SELECT id, label, seats FROM dining_tables WHERE restaurant_id = $1 ORDER BY label",
      [r.id],
    );
    return { ...r, tables };
  });

export const getSlots = createServerFn({ method: "GET" })
  .inputValidator((d) =>
    z
      .object({
        restaurantId: z.string().uuid(),
        date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
        party: z.number().int().min(1).max(20),
      })
      .parse(d),
  )
  .handler(async ({ data }) => {
    const db = await getDb();
    const rows = await db.query<{ starts_at: unknown; free_tables: number }>(
      "SELECT starts_at, free_tables FROM available_slots($1, $2::date, $3)",
      [data.restaurantId, data.date, data.party],
    );
    return rows.map((r) => ({ starts_at: iso(r.starts_at), free_tables: Number(r.free_tables) }));
  });

/* ---------- bookings ---------- */

export type BookResult = {
  status: "confirmed" | "replayed" | "conflict" | "rejected" | "error";
  booking_id?: string;
  table_id?: string;
  reason?: string;
};

async function callBook(args: {
  userId: string;
  restaurantId: string;
  startsAt: string;
  party: number;
  guestName: string;
  key: string;
  tableId?: string | null;
}): Promise<BookResult> {
  const db = await getDb();
  try {
    const [row] = await db.query<{ r: BookResult | string }>(
      "SELECT book_table($1, $2, $3::timestamptz, $4, $5, $6, $7) AS r",
      [args.userId, args.restaurantId, args.startsAt, args.party, args.guestName, args.key, args.tableId ?? null],
    );
    const r = row!.r;
    return typeof r === "string" ? (JSON.parse(r) as BookResult) : r;
  } catch (e) {
    console.error("book_table failed", e);
    return { status: "error", reason: "Booking failed, please try again" };
  }
}

export const bookTable = createServerFn({ method: "POST" })
  .inputValidator((d) =>
    z
      .object({
        restaurantId: z.string().uuid(),
        startsAt: z.string().datetime({ offset: true }),
        party: z.number().int().min(1).max(20),
        guestName: z.string().trim().min(1).max(80),
        idempotencyKey: z.string().min(8).max(100),
      })
      .parse(d),
  )
  .handler(async ({ data }) => {
    const u = await auth.requireUser();
    return callBook({
      userId: u.id,
      restaurantId: data.restaurantId,
      startsAt: data.startsAt,
      party: data.party,
      guestName: data.guestName,
      key: data.idempotencyKey,
    });
  });

export const cancelBooking = createServerFn({ method: "POST" })
  .inputValidator((d) => z.object({ bookingId: z.string().uuid() }).parse(d))
  .handler(async ({ data }) => {
    const u = await auth.requireUser();
    const db = await getDb();
    const [row] = await db.query<{ ok: boolean }>("SELECT cancel_booking($1, $2, $3) AS ok", [
      data.bookingId,
      u.id,
      u.isStaff,
    ]);
    return { ok: Boolean(row?.ok) };
  });

export const myBookings = createServerFn({ method: "GET" }).handler(async () => {
  const u = await auth.currentUser();
  if (!u) return [];
  const db = await getDb();
  const rows = await db.query<{
    id: string;
    party_size: number;
    starts_at: unknown;
    status: string;
    restaurant: string;
    timezone: string;
    table_label: string;
  }>(
    `SELECT b.id, b.party_size, lower(b.slot) AS starts_at, b.status,
            r.name AS restaurant, r.timezone, t.label AS table_label
     FROM bookings b JOIN restaurants r ON r.id = b.restaurant_id JOIN dining_tables t ON t.id = b.table_id
     WHERE b.user_id = $1 AND b.guest_name NOT LIKE 'Stress %' AND b.guest_name <> 'Retry'
     ORDER BY b.created_at DESC LIMIT 200`,
    [u.id],
  );
  return rows.map((r) => ({ ...r, starts_at: iso(r.starts_at) }));
});

export const staffFloor = createServerFn({ method: "GET" }).handler(async () => {
  const u = await auth.currentUser();
  if (!u?.isStaff) return null;
  const db = await getDb();
  const rests = await db.query<{ id: string; name: string; timezone: string }>(
    "SELECT id, name, timezone FROM restaurants ORDER BY name",
  );
  const tables = await db.query<DiningTable & { restaurant_id: string }>(
    "SELECT id, label, seats, restaurant_id FROM dining_tables ORDER BY label",
  );
  // Today's bookings, where "today" is evaluated in each restaurant's own time zone.
  const bks = await db.query<{ id: string; table_id: string; starts_at: unknown; guest_name: string; party_size: number }>(
    `SELECT b.id, b.table_id, lower(b.slot) AS starts_at, b.guest_name, b.party_size
     FROM bookings b JOIN restaurants r ON r.id = b.restaurant_id
     WHERE b.status = 'confirmed'
       AND (lower(b.slot) AT TIME ZONE r.timezone)::date = (now() AT TIME ZONE r.timezone)::date
     ORDER BY lower(b.slot)`,
  );
  return {
    rests: rests.map((r) => ({ ...r, tables: tables.filter((t) => t.restaurant_id === r.id) })),
    bks: bks.map((b) => ({ ...b, starts_at: iso(b.starts_at) })),
  };
});

/* ---------- factory dashboard ---------- */

export const getFactory = createServerFn({ method: "GET" }).handler(async () => {
  const db = await getDb();
  const [stages, seats, handoffs, costs, recoveries, runs] = await Promise.all([
    db.query("SELECT id, number, title, status, summary FROM factory_stages ORDER BY number"),
    db.query("SELECT id, name, role, model, mandate FROM seats ORDER BY sort"),
    db.query("SELECT id, stage_number, from_seat, to_seat, kind, summary, result FROM handoffs ORDER BY created_at"),
    db.query("SELECT id, stage_number, seat, tokens::float8 AS tokens, minutes::float8 AS minutes, usd::float8 AS usd FROM cost_entries ORDER BY stage_number"),
    db.query("SELECT id, stage_number, caught_by, problem, fix FROM recovery_events ORDER BY created_at"),
    db.query<{ created_at: unknown }>(
      "SELECT id, attempts, confirmed, conflicts, replayed, errors, duration_ms, passed, engine, created_at FROM stress_runs ORDER BY created_at DESC LIMIT 20",
    ),
  ]);
  return {
    stages,
    seats,
    handoffs,
    costs,
    recoveries,
    runs: runs.map((r) => ({ ...r, created_at: iso(r.created_at) })),
  } as unknown as {
    stages: { id: string; number: number; title: string; status: string; summary: string }[];
    seats: { id: string; name: string; role: string; model: string; mandate: string }[];
    handoffs: { id: string; stage_number: number; from_seat: string; to_seat: string; kind: string; summary: string; result: string }[];
    costs: { id: string; stage_number: number; seat: string; tokens: number; minutes: number; usd: number }[];
    recoveries: { id: string; stage_number: number; caught_by: string; problem: string; fix: string }[];
    runs: { id: string; attempts: number; confirmed: number; conflicts: number; replayed: number; errors: number; duration_ms: number; passed: boolean; engine: string; created_at: string }[];
  };
});

const entry = z.discriminatedUnion("table", [
  z.object({
    table: z.literal("handoffs"),
    stage_number: z.number().int().min(1).max(4),
    from_seat: z.string().min(1).max(60),
    to_seat: z.string().min(1).max(60),
    kind: z.enum(["plan", "change", "evidence", "check"]),
    summary: z.string().min(1).max(500),
    result: z.enum(["pass", "fail", "info"]),
  }),
  z.object({
    table: z.literal("cost_entries"),
    stage_number: z.number().int().min(1).max(4),
    seat: z.string().min(1).max(60),
    tokens: z.number().min(0),
    minutes: z.number().min(0),
    usd: z.number().min(0),
  }),
  z.object({
    table: z.literal("recovery_events"),
    stage_number: z.number().int().min(1).max(4),
    caught_by: z.string().min(1).max(60),
    problem: z.string().min(1).max(500),
    fix: z.string().min(1).max(500),
  }),
]);

export const addFactoryEntry = createServerFn({ method: "POST" })
  .inputValidator((d) => entry.parse(d))
  .handler(async ({ data }) => {
    await auth.requireUser();
    const db = await getDb();
    const { table, ...row } = data;
    const cols = Object.keys(row);
    await db.query(
      `INSERT INTO ${table} (${cols.join(",")}) VALUES (${cols.map((_, i) => `$${i + 1}`).join(",")})`,
      Object.values(row),
    );
    return { ok: true };
  });

/* ---------- concurrency stress test ---------- */

export type StressReport = {
  engine: string;
  attempts: number;
  confirmed: number;
  conflicts: number;
  replayed: number;
  errors: number;
  durationMs: number;
  passed: boolean;
  contested: { restaurant: string; startsAt: string; timezone: string; table: string };
  retry: { calls: number; distinctBookings: number; confirmed: number; replayed: number };
};

export const runStressTest = createServerFn({ method: "POST" })
  .inputValidator((d) => z.object({ attempts: z.number().int().min(2).max(50) }).parse(d))
  .handler(async ({ data }): Promise<StressReport> => {
    const u = await auth.requireUser();
    const db = await getDb();
    const rests = await db.query<Restaurant>("SELECT * FROM restaurants");
    const r = rests[Math.floor(Math.random() * rests.length)];
    if (!r) throw new Error("No restaurants");
    const [table] = await db.query<DiningTable>(
      "SELECT id, label, seats FROM dining_tables WHERE restaurant_id = $1 ORDER BY seats, label LIMIT 1",
      [r.id],
    );
    if (!table) throw new Error("No tables");
    const date = new Date(Date.now() + (60 + Math.floor(Math.random() * 300)) * 86400000).toISOString().slice(0, 10);
    const slots = await db.query<{ starts_at: unknown }>(
      "SELECT starts_at FROM available_slots($1, $2::date, 1) WHERE free_tables > 0",
      [r.id, date],
    );
    if (slots.length < 2) throw new Error("No free slots for the test");
    const contestedAt = iso(slots[0]!.starts_at);
    const retryAt = iso(slots[slots.length - 1]!.starts_at);

    const runId = crypto.randomUUID();
    const base = { userId: u.id, restaurantId: r.id, party: 1, tableId: table.id };
    const t0 = Date.now();
    const results = await Promise.all(
      Array.from({ length: data.attempts }, (_, i) =>
        callBook({ ...base, startsAt: contestedAt, guestName: `Stress ${i + 1}`, key: `${runId}-${i}` }),
      ),
    );
    const retries = await Promise.all(
      Array.from({ length: 5 }, () =>
        callBook({ ...base, startsAt: retryAt, guestName: "Retry", key: `${runId}-retry` }),
      ),
    );
    const durationMs = Date.now() - t0;

    const count = (arr: BookResult[], s: string) => arr.filter((x) => x.status === s).length;
    const confirmed = count(results, "confirmed");
    const conflicts = count(results, "conflict");
    const replayed = count(results, "replayed");
    const errors = count(results, "error") + count(results, "rejected");
    const retryIds = new Set(retries.map((x) => x.booking_id).filter(Boolean));
    const passed = confirmed === 1 && conflicts === data.attempts - 1 && retryIds.size === 1 && count(retries, "confirmed") === 1;

    const ids = new Set([...results, ...retries].map((x) => x.booking_id).filter((x): x is string => Boolean(x)));
    for (const id of ids) await db.query("SELECT cancel_booking($1, $2, false)", [id, u.id]);

    await db.query(
      "INSERT INTO stress_runs (attempts, confirmed, conflicts, replayed, errors, duration_ms, passed, engine) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
      [data.attempts, confirmed, conflicts, replayed, errors, durationMs, passed, db.engine],
    );

    return {
      engine: db.engine,
      attempts: data.attempts,
      confirmed,
      conflicts,
      replayed,
      errors,
      durationMs,
      passed,
      contested: { restaurant: r.name, startsAt: contestedAt, timezone: r.timezone, table: table.label },
      retry: { calls: 5, distinctBookings: retryIds.size, confirmed: count(retries, "confirmed"), replayed: count(retries, "replayed") },
    };
  });
