import { randomBytes, scrypt as scryptCb, timingSafeEqual, createHash } from "node:crypto";
import { promisify } from "node:util";
import { getCookie, setCookie, deleteCookie } from "@tanstack/react-start/server";
import { getDb } from "./db.server";

const scrypt = promisify(scryptCb) as (pw: string, salt: Buffer, len: number) => Promise<Buffer>;
const COOKIE = "tk_session";
const SESSION_DAYS = 14;

export type SessionUser = { id: string; email: string; isStaff: boolean };

export async function hashPassword(pw: string) {
  const salt = randomBytes(16);
  const key = await scrypt(pw, salt, 64);
  return `scrypt$${salt.toString("hex")}$${key.toString("hex")}`;
}

async function verifyPassword(pw: string, stored: string) {
  const [alg, saltHex, keyHex] = stored.split("$");
  if (alg !== "scrypt" || !saltHex || !keyHex) return false;
  const key = await scrypt(pw, Buffer.from(saltHex, "hex"), 64);
  const expected = Buffer.from(keyHex, "hex");
  return key.length === expected.length && timingSafeEqual(key, expected);
}

const sha = (s: string) => createHash("sha256").update(s).digest("hex");

async function startSession(userId: string) {
  const db = await getDb();
  const token = randomBytes(32).toString("base64url");
  await db.query("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES ($1, $2, now() + $3::interval)", [
    sha(token),
    userId,
    `${SESSION_DAYS} days`,
  ]);
  setCookie(COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env["COOKIE_SECURE"] === "true",
    path: "/",
    maxAge: SESSION_DAYS * 86400,
  });
}

function staffEmails() {
  return (process.env["STAFF_EMAILS"] ?? "")
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

export async function signUp(email: string, password: string) {
  const db = await getDb();
  const e = email.trim().toLowerCase();
  const exists = await db.query("SELECT 1 FROM users WHERE email = $1", [e]);
  if (exists.length) throw new Error("An account with that email already exists");
  const [u] = await db.query<{ id: string }>(
    "INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING id",
    [e, await hashPassword(password)],
  );
  if (staffEmails().includes(e)) {
    await db.query("INSERT INTO user_roles (user_id, role) VALUES ($1,'staff'),($1,'admin') ON CONFLICT DO NOTHING", [
      u!.id,
    ]);
  }
  await startSession(u!.id);
}

export async function signIn(email: string, password: string) {
  const db = await getDb();
  const [u] = await db.query<{ id: string; password_hash: string }>(
    "SELECT id, password_hash FROM users WHERE email = $1",
    [email.trim().toLowerCase()],
  );
  if (!u || !(await verifyPassword(password, u.password_hash))) throw new Error("Wrong email or password");
  await startSession(u.id);
}

export async function signOut() {
  const token = getCookie(COOKIE);
  if (token) {
    const db = await getDb();
    await db.query("DELETE FROM sessions WHERE token_hash = $1", [sha(token)]);
  }
  deleteCookie(COOKIE, { path: "/" });
}

export async function currentUser(): Promise<SessionUser | null> {
  const token = getCookie(COOKIE);
  if (!token) return null;
  const db = await getDb();
  const [row] = await db.query<{ id: string; email: string; is_staff: boolean }>(
    `SELECT u.id, u.email,
       EXISTS (SELECT 1 FROM user_roles r WHERE r.user_id = u.id AND r.role IN ('staff','admin')) AS is_staff
     FROM sessions s JOIN users u ON u.id = s.user_id
     WHERE s.token_hash = $1 AND s.expires_at > now()`,
    [sha(token)],
  );
  return row ? { id: row.id, email: row.email, isStaff: row.is_staff } : null;
}

export async function requireUser(): Promise<SessionUser> {
  const u = await currentUser();
  if (!u) throw new Error("Please sign in");
  return u;
}
