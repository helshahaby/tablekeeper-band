// Local demo setup only: replaces ALL backend state with fixture.json via the backend's
// documented POST /_test/reset. Talks to the backend directly, never through the
// frontend's /backend proxy (which refuses /_test/*), and only to a loopback address or
// the `backend` service on the private Docker Compose network.
//
// Usage: node scripts/demo-backend/setup.mjs [--yes]
//   TABLEKEEPER_URL  backend base URL (default http://127.0.0.1:8080)
import { readFile } from "node:fs/promises";

const base = process.env.TABLEKEEPER_URL ?? "http://127.0.0.1:8080";
const host = new URL(base).hostname;
if (!["127.0.0.1", "localhost", "[::1]", "backend"].includes(host)) {
  console.error(`Refusing to reset non-local backend ${base}.`);
  process.exit(1);
}

const fixture = await readFile(new URL("./fixture.json", import.meta.url), "utf8");

const exported = await fetch(`${base}/_test/export`).then((r) => r.json());
const n = exported.state.reservations.length;
if (n > 0 && !process.argv.includes("--yes")) {
  console.error(`Backend holds ${n} reservation(s); pass --yes to wipe them and reseed.`);
  process.exit(1);
}

const res = await fetch(`${base}/_test/reset`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: fixture,
});
if (res.status !== 204) {
  console.error(`Reset failed: HTTP ${res.status} ${await res.text()}`);
  process.exit(1);
}
const { restaurants } = await fetch(`${base}/restaurants`).then((r) => r.json());
console.log(`Seeded ${base}: ${restaurants.map((r) => r.name).join(", ")}`);
console.log("Demo logins: ada@demo.test / demo-password, bob@demo.test / demo-password");
