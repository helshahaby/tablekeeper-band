import { useSyncExternalStore } from "react";

/*
 * Client for the Tablekeeper Stage 1 backend, reached through the dev proxy
 * (/backend/* -> http://127.0.0.1:8080/*). Shapes follow the backend's app/service.py.
 * Errors are always {"error": {"code", "message"}} with a 4xx/5xx status.
 */

const BASE = "/backend";
const SESSION_KEY = "tablekeeper.backend.session";

export type RestaurantSummary = { id: string; name: string; timezone: string };
export type BackendTable = { id: string; label?: string; capacity: number };
export type RestaurantDetail = RestaurantSummary & {
  slot_minutes: number;
  reservation_duration_minutes: number;
  cancellation_cutoff_minutes: number;
  opening_hours: { weekday: string; opens: string; closes: string }[];
  tables: BackendTable[];
};
export type Slot = { starts_at_local: string; starts_at: string; available_table_ids: string[] };
export type Availability = { restaurant_id: string; date: string; timezone: string; slots: Slot[] };
export type Reservation = {
  reservation_id: string;
  reference: string;
  restaurant_id: string;
  table_id: string;
  party_size: number;
  status: "confirmed" | "cancelled";
  starts_at_local: string;
  starts_at: string;
  ends_at: string;
  created_at: string;
};
export type Session = { token: string; userId: string; displayName: string; email: string };

export class BackendError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

const FRIENDLY: Record<string, string> = {
  table_unavailable:
    "That table is already booked for an overlapping time. No reservation was made.",
  party_exceeds_capacity: "The party is larger than this table seats.",
  outside_opening_hours: "The restaurant is not open for that whole time.",
  not_on_slot_grid: "That start time is not one of the restaurant's slots.",
  invalid_local_time: "That local time does not exist in the restaurant's time zone (DST change).",
  email_taken: "An account with this email already exists.",
  unauthenticated: "Your session is not valid. Please sign in again.",
};

/** Human-readable text for an error, keeping the backend's own message and code visible. */
export function describeError(err: unknown) {
  if (err instanceof BackendError) {
    const friendly = FRIENDLY[err.code];
    return friendly ? `${friendly} (${err.code}: ${err.message})` : `${err.message} (${err.code})`;
  }
  return (err as Error)?.message ?? String(err);
}

/* ---------- session (bearer token) ---------- */

const listeners = new Set<() => void>();
let cachedRaw: string | null = null;
let cachedSession: Session | null = null;

function readSession(): Session | null {
  const raw = localStorage.getItem(SESSION_KEY);
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    try {
      cachedSession = raw ? (JSON.parse(raw) as Session) : null;
    } catch {
      cachedSession = null;
    }
  }
  return cachedSession;
}

function writeSession(s: Session | null) {
  if (s) localStorage.setItem(SESSION_KEY, JSON.stringify(s));
  else localStorage.removeItem(SESSION_KEY);
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => e.key === SESSION_KEY && listener();
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function useSession() {
  return useSyncExternalStore(subscribe, readSession, () => null);
}

export function signOut() {
  writeSession(null);
}

/* ---------- transport ---------- */

async function call<T>(
  method: string,
  path: string,
  opts: { body?: unknown; auth?: boolean; idempotencyKey?: string } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const session = opts.auth ? readSession() : null;
  if (opts.auth && !session) throw new BackendError(401, "unauthenticated", "Not signed in.");
  if (session) headers["Authorization"] = `Bearer ${session.token}`;
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;

  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method,
      headers,
      body: opts.body === undefined ? null : JSON.stringify(opts.body),
    });
  } catch {
    throw new BackendError(0, "network_error", "Could not reach the Tablekeeper backend.");
  }
  const text = await res.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    // Non-JSON means the proxy answered, not the backend (e.g. backend down).
  }
  if (!res.ok) {
    const error = (payload as { error?: { code?: string; message?: string } } | null)?.error;
    // The backend forgets tokens on restart/reset; drop ours so the UI asks to sign in again.
    if (res.status === 401 && session) writeSession(null);
    throw new BackendError(
      res.status,
      error?.code ?? "backend_unavailable",
      error?.message ?? `Backend returned HTTP ${res.status}. Is it running on 127.0.0.1:8080?`,
    );
  }
  return payload as T;
}

/* ---------- endpoints ---------- */

type AuthResponse = { user_id: string; display_name: string; token: string };

async function authenticate(
  path: string,
  body: { email: string; password: string; display_name?: string },
) {
  const r = await call<AuthResponse>("POST", path, { body });
  const session = {
    token: r.token,
    userId: r.user_id,
    displayName: r.display_name,
    email: body.email,
  };
  writeSession(session);
  return session;
}

export const backend = {
  signUp: (email: string, password: string, displayName?: string) =>
    authenticate(
      "/auth/signup",
      displayName ? { email, password, display_name: displayName } : { email, password },
    ),
  logIn: (email: string, password: string) => authenticate("/auth/login", { email, password }),

  listRestaurants: () =>
    call<{ restaurants: RestaurantSummary[] }>("GET", "/restaurants").then((r) => r.restaurants),
  getRestaurant: (id: string) =>
    call<RestaurantDetail>("GET", `/restaurants/${encodeURIComponent(id)}`),
  availability: (restaurantId: string, date: string, partySize: number) =>
    call<Availability>(
      "GET",
      `/availability?${new URLSearchParams({ restaurant_id: restaurantId, date, party_size: String(partySize) })}`,
    ),

  createReservation: (
    input: { restaurant_id: string; table_id: string; starts_at_local: string; party_size: number },
    idempotencyKey: string,
  ) => call<Reservation>("POST", "/reservations", { body: input, auth: true, idempotencyKey }),
  listReservations: () =>
    call<{ reservations: Reservation[] }>("GET", "/reservations", { auth: true }).then(
      (r) => r.reservations,
    ),
  cancelReservation: (reference: string) =>
    call<Reservation>("POST", `/reservations/${encodeURIComponent(reference)}/cancel`, {
      auth: true,
    }),
};

export function tableLabel(r: RestaurantDetail | undefined, tableId: string) {
  const t = r?.tables.find((x) => x.id === tableId);
  return t?.label ?? tableId;
}

/** "2030-01-03T19:00" -> "19:00" (backend local wall time, already in the restaurant's zone). */
export const localTime = (startsAtLocal: string) => startsAtLocal.slice(11, 16);
