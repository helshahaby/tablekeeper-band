# Local demo against the Tablekeeper Stage 1 backend

The customer pages (sign in/up, restaurants, availability, booking, My bookings) call the
Stage 1 backend through the Vite dev proxy: `/backend/*` -> `http://127.0.0.1:8080/*`.
The proxy refuses `/backend/_test/*`, so state can only be reset from this machine with
`setup.mjs`. Staff and Factory pages still use the app's embedded database and are not
linked from the header on this branch.

```sh
# 1. Backend (from band-work/result/stage-1/, see its RUN.md)
docker run --rm -p 8080:8080 -e PORT=8080 tablekeeper-stage1

# 2. Seed demo restaurants/tables/users (wipes backend state; --yes if it has reservations)
node scripts/demo-backend/setup.mjs

# 3. Frontend
npx vite dev --host 127.0.0.1     # http://127.0.0.1:5173/

# Optional: book, overlapping-book (expect 409), re-list, via the proxy
node scripts/demo-backend/check.mjs
```

Demo logins: `ada@demo.test` / `demo-password`, `bob@demo.test` / `demo-password`.
