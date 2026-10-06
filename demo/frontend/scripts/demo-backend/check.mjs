// End-to-end check of the demo flow through the frontend dev proxy (same path the browser uses).
//   1. an available table can be booked
//   2. an overlapping booking for the same table is rejected (409 table_unavailable)
//   3. the original reservation is still listed afterwards
// Also checks that the proxy refuses the backend's /_test/* control endpoints.
//
// Usage: node scripts/demo-backend/check.mjs   (FRONTEND_URL default http://127.0.0.1:5173)
import { randomUUID } from "node:crypto";

const api = `${process.env.FRONTEND_URL ?? "http://127.0.0.1:5173"}/backend`;
const restaurantId = "zum-anker";
const tableId = "t_2";

async function call(method, path, { body, token, key } = {}) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  if (key) headers["Idempotency-Key"] = key;
  const res = await fetch(api + path, { method, headers, body: body && JSON.stringify(body) });
  const text = await res.text();
  return { status: res.status, body: text ? JSON.parse(text) : null };
}

function expect(cond, message, detail) {
  if (!cond) {
    console.error(`FAIL: ${message}`, detail ?? "");
    process.exit(1);
  }
  console.log(`ok   ${message}`);
}

const login = await call("POST", "/auth/login", {
  body: { email: "ada@demo.test", password: "demo-password" },
});
expect(login.status === 200, "log in as ada@demo.test", login.body);
const token = login.body.token;

// A week ahead in the restaurant's zone; pick the first slot where the table and the next slot are free.
const date = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Berlin" }).format(
  Date.now() + 7 * 86400e3,
);
const avail = await call(
  "GET",
  `/availability?restaurant_id=${restaurantId}&date=${date}&party_size=2`,
);
expect(avail.status === 200, `availability for ${date}`, avail.body);
const slots = avail.body.slots;
const i = slots.findIndex(
  (s, j) =>
    s.available_table_ids.includes(tableId) && slots[j + 1]?.available_table_ids.includes(tableId),
);
expect(i >= 0, `found a free slot for table ${tableId}`);
const [first, overlapping] = [slots[i], slots[i + 1]];

const booked = await call("POST", "/reservations", {
  token,
  key: randomUUID(),
  body: {
    restaurant_id: restaurantId,
    table_id: tableId,
    starts_at_local: first.starts_at_local,
    party_size: 2,
  },
});
expect(
  booked.status === 201 && booked.body.status === "confirmed",
  `1. booked ${tableId} at ${first.starts_at_local} (ref ${booked.body?.reference})`,
  booked.body,
);

const bob = await call("POST", "/auth/login", {
  body: { email: "bob@demo.test", password: "demo-password" },
});
const clash = await call("POST", "/reservations", {
  token: bob.body.token,
  key: randomUUID(),
  body: {
    restaurant_id: restaurantId,
    table_id: tableId,
    starts_at_local: overlapping.starts_at_local,
    party_size: 2,
  },
});
expect(
  clash.status === 409 && clash.body.error.code === "table_unavailable",
  `2. overlapping booking ${tableId} at ${overlapping.starts_at_local} rejected: ${clash.status} ${clash.body?.error?.code}`,
  clash.body,
);

const list = await call("GET", "/reservations", { token });
const kept = list.body.reservations.find((r) => r.reference === booked.body.reference);
expect(
  kept?.status === "confirmed",
  `3. reservation ${booked.body.reference} still listed as confirmed`,
  list.body,
);

// GET only: if the proxy ever forwarded these, a GET cannot change state (reset answers 405, export 200).
for (const path of ["/_test/reset", "/_test/export", "/%5Ftest/export", "/_test/import"]) {
  const blocked = await fetch(api + path);
  expect(blocked.status === 404, `proxy refuses ${path} (${blocked.status})`);
}
