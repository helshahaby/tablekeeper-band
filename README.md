# Tablekeeper — Band factory submission

- **Track:** `tablekeeper` (restaurant reservations)
- **Team:** Hossam Elshahaby (human owner; dispatched the task, did not steer the run)
- **Stage reached:** Stage 1 (`stage-1/`). No later stage was attempted in this run.
- **Demo video:** https://www.loom.com/share/8318b9a6de17450bbfb7fdda287a9a8b

## How to read this repository

| Path | What it is | Written by |
|---|---|---|
| `mandates/` | Generic standing mandates, one per seat, named after the seat identity | planner-nc33 |
| `FACTORY.md` | Factory design: seats, handoffs, review loop, measured time and cost | planner-nc33 |
| `stage-1/` | The Stage 1 service: source, `Dockerfile`, `RUN.md`, black-box tests | developer-nc33 |
| `review/stage-1/` | The reviewer's independent spec-derived probe suite and clean-build review script | reviewer-nc34 |
| `room.json` | Full Band room download (added by the human after the run; see FACTORY.md) | Band |
| `demo/frontend/` | Web frontend for the demo, a **later Lovable integration**, not part of the BAND build | Lovable, after the run |
| `compose.yaml` | Docker Compose packaging of `stage-1/` plus `demo/frontend/` for the demo | Added after the run |

Every commit up to `2784470` is authored by the seat that wrote it
(`git log --format='%h %an %s'`). Later commits (the room transcript, the frontend and the
Compose packaging) were added by the human after the run.

## Build and run Stage 1

From `stage-1/`:

```sh
docker build -t tablekeeper-stage1 .
docker run --rm -p 8080:8080 -e PORT=8080 tablekeeper-stage1
curl http://127.0.0.1:8080/health     # {"status": "ok"}
```

The service is Python 3.12 standard library plus the vendored `tzdata` package. All
state lives in memory, and the container needs no network at run time.

## Verification results (official harness, shipped checks only)

| Run | Revision | Mode | Stage 1 | Stage 2 (overshoot probe) | Claimed |
|---|---|---|---|---|---|
| `checks/s1-01` | `42d743f` | host | **fail** 117/120 | fail | 1 |
| `checks/s1-02` | `aa32fd7` | host | pass 120/120 | fail | 1 |
| `checks/s1-final` | `aa32fd7` | isolated | pass 120/120 | fail | 1 |
| `checks/s1-final2` | `5d70f0e` | isolated | pass 120/120 | fail (expected) | **1** |

The reviewer's own probe suite, derived from the spec text, gave 456 passed and 0 failed at
`5d70f0e`. The shipped checks are only part of the graded suite. See FACTORY.md for what
the band checked beyond them and for the known limitations.

## Demo: backend and frontend with Docker Compose

The frontend in `demo/frontend/` was built later in Lovable and connected to the accepted
Stage 1 service. It is separate from the original BAND build. The BAND agents did not write
it, and it is not covered by the verification results above. `stage-1/` is packaged exactly
as accepted.

Run these from the repository root (tested with Docker 29.1 and Compose 2.40):

```sh
# Build both images and start them. Returns when both health checks pass.
docker compose up -d --build --wait

# Load the demo restaurants and users. This is never run automatically.
docker compose run --rm demo-setup
# If reservations already exist, it refuses. To wipe them and reseed:
#   docker compose run --rm demo-setup --yes

# Open the frontend
xdg-open http://127.0.0.1:3000/     # or open the URL in a browser

# Optional: scripted check through the frontend proxy (book, overlapping booking as a
# second user rejected with 409, original still listed, /_test/* refused)
docker compose run --rm demo-check

# Stop both services
docker compose down
```

Demo logins: `ada@demo.test` / `demo-password` and `bob@demo.test` / `demo-password`. You
can also sign up a new account in the frontend.

How it fits together:

- **backend** is built from `stage-1/` and listens on port 8080 inside the Compose network.
  It is not published to the host. Its health check calls `GET /health`.
- **frontend** is published on `127.0.0.1:3000`. It forwards `/backend/*` to
  `http://backend:8080/*` and strips the `/backend` prefix
  (`demo/frontend/src/lib/backend-proxy.server.ts`). Its health check calls `GET /api/health`.
- The proxy returns 404 for any path with a `_test` segment, including URL-encoded forms
  like `%5Ftest`. The public frontend therefore cannot reach `/_test/reset`,
  `/_test/export` or `/_test/import`. Only the `demo-setup` command can reset the backend,
  and it calls the backend directly over the private Compose network.
- **Reservations are stored in memory.** Restarting or stopping the backend container
  (including `docker compose down`) loses all users, restaurants and reservations. After
  that, run `docker compose run --rm demo-setup` again. Restarting only the frontend keeps
  backend data.
- The frontend image also contains its own PostgreSQL, used only by the staff and factory
  pages. Those pages are left over from the Lovable build and are not linked from the
  header. Its data is kept in the `tablekeeper_frontend-db` volume, which
  `docker compose down -v` removes.

Tested on 2026-10-06 with the commands above: both health checks passed. In a headless
Chromium browser, a new user signed up, signed out and logged back in, and booked a table.
A second user (`bob@demo.test`) had the same table open at an overlapping time. Their
booking was rejected with `409 table_unavailable`. The first reservation was still listed
as confirmed on My bookings after a browser refresh. `demo-check` passed.
`/backend/_test/*` returned 404 for GET and POST, including encoded and `..` variants. A
backend restart left the backend empty, as expected. The Stage 1 unit tests (54) still pass.
