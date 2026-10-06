# Tablekeeper — Band factory submission

**Restaurant reservations that refuse double-booking in the reservation service itself,
plus the full factory evidence of how three AI agents built and verified it.**

- **Track:** `tablekeeper` (restaurant reservations)
- **Team:** Hossam Elshahaby (human owner; dispatched the task, did not steer the run)
- **Stage reached:** Stage 1 (`stage-1/`). No later stage was attempted in this run.
- **Demo video:** https://www.loom.com/share/8318b9a6de17450bbfb7fdda287a9a8b
- **Repository:** https://github.com/helshahaby/tablekeeper-band

## What Tablekeeper does

Tablekeeper is a booking service for restaurants. Guests sign up, browse restaurants, see
open slots in the restaurant's own time zone and reserve a table. The service guarantees
that a table can never be booked twice for overlapping times.

Features of the Stage 1 service (`stage-1/`):

- **No double booking.** One lock around all state serializes every write. Two overlapping
  bookings for the same table cannot both succeed, even under 50 concurrent requests.
  The second gets `409 table_unavailable`. The check is in the service, not the page, so
  no client can bypass it.
- **Safe retries.** Each booking carries an `Idempotency-Key`. Repeating a request returns
  the original reservation instead of creating a second one.
- **Correct local time.** Times are converted with each restaurant's IANA time zone and
  handle daylight-saving gaps and overlaps (tested for Europe/Berlin and America/New_York).
- **All-or-nothing changes.** Multi-item moves either fully apply or change nothing.
- **Accounts and reservations.** Sign up, log in (bearer token), availability, booking,
  listing and cancelling with a cutoff.
- **No runtime dependencies.** Python standard library plus vendored `tzdata`. It starts
  in about 0.3 s and needs no network.

Limitation: state is kept **in memory**. Restarting the backend loses all users,
restaurants and reservations.

## How it was built: three agents orchestrated by Band

The service was written by an autonomous "factory" of three Claude Code agents
(`claude-opus-5-5`) working in one Band room. The human sent one task message and gave no
other input. From dispatch to the final accepted revision took about 27 minutes.

| Agent | Seat | Job |
|---|---|---|
| **Planner** | `planner-nc33` | Reads the full spec and resolves its ambiguities up front into a numbered decision list. Writes self-contained handoffs (spec pasted verbatim) to the other two seats, rules on disagreements against the spec text, and writes the factory documents and final report. Never writes product code. |
| **Builder** | `developer-nc33` | Implements the service under `stage-1/`, with the Dockerfile, `RUN.md` and 54 black-box tests. Commits in small steps under its own name and answers each review finding with a fix commit. |
| **Reviewer** | `reviewer-nc34` | Writes an independent 456-check probe suite from the spec alone, before seeing the code. Then checks out each handed-off revision, builds it clean under 2 CPUs and 2 GiB, runs the official harness, and accepts or rejects with numbered, reproducible findings. Never edits product code. |

**Band's role is orchestration.** Band provides the shared room and messaging (the `band`
CLI and the `jam` plugin) that connect the three separate sessions:

- **Membership and liveness.** Before any work, the planner checks room participants and
  runs a nonce round trip with each seat, which proves every agent is attached.
- **Directed handoffs.** Each `@handle` message carries the task, paths, scope, acceptance
  criteria and next actor, so no agent depends on reading another agent's files.
- **The review loop.** Hand-off → independent verification → numbered rejection → fix →
  re-verification. In this run the reviewer rejected the first revision (117/120) and
  accepted `5d70f0e` (120/120, probes 456/456).
- **Evidence.** The full room export is in `room.json`. Every commit is attributed to the
  seat that wrote it.

See [FACTORY.md](FACTORY.md) for the timeline, the bad work the factory caught, the design
trade-offs and the measured cost (≈ $10.61 at API list rates), and `mandates/` for the
standing mandate of each seat.

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
| `presentation/tablekeeper-band.pptx` | 6-slide deck; animations advance on click | Added after the run |

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

### Set up the environment

You need:

- **Docker Engine with Compose v2** (`docker compose version`). Tested with Docker 29.1 and
  Compose 2.40 on Linux.
- **git**, and **port 3000** free on `127.0.0.1`.
- Optional: **Python 3.12+** to run the Stage 1 tests on the host.

You don't need Node, a database or any credentials. Both images build from source, and the
demo tools run in a `node` container. No Lovable Cloud or Supabase credentials are needed.
`demo/frontend/.env.example` lists the unused variables left over from the Lovable editor,
with placeholder values.

```sh
git clone https://github.com/helshahaby/tablekeeper-band.git
cd tablekeeper-band
```

### Run and test in two windows

**Terminal window 1: start both services and watch their logs.**

```sh
docker compose up --build
```

Wait until both containers report healthy. Window 2 can check with `docker compose ps`.

**Terminal window 2: load demo data, check the stack, stop it.**

```sh
docker compose ps                        # backend and frontend show (healthy)
docker compose run --rm demo-setup       # load demo restaurants and users (never automatic)
docker compose run --rm demo-check       # optional scripted end-to-end check
```

`demo-setup` refuses if reservations already exist. To wipe them and reseed, run
`docker compose run --rm demo-setup --yes`.

`demo-check` books a table through the frontend proxy. It then has a second user try an
overlapping booking, which must be rejected with 409. It also checks that the original is
still listed and that `/_test/*` is refused.

**Two browser windows: two guests competing for one table.** Open
**http://127.0.0.1:3000/** in a normal window and in a private/incognito window. The
session is kept in browser storage, so each window is a separate guest.

| Step | Window A (normal) | Window B (private) |
|---|---|---|
| 1 | Sign in as `ada@demo.test` / `demo-password`, or sign up a new account | Sign in as `bob@demo.test` / `demo-password` |
| 2 | | Open **Zum Anker**, pick a date, choose **18:00** and **Table 2**. Don't confirm yet |
| 3 | Open **Zum Anker**, same date, choose **17:30** and **Table 2**, and click **Confirm**. It shows "Confirmed by backend" | |
| 4 | | Click **Confirm**. It shows **Booking rejected**, `table_unavailable`. Reservations last 90 minutes, so 17:30 overlaps 18:00 |
| 5 | Open **My bookings** and refresh the page. The 17:30 reservation is still confirmed | |

**Stop.** Press `Ctrl+C` in window 1, or run this in window 2:

```sh
docker compose down
```

### Background mode (one terminal)

```sh
docker compose up -d --build --wait      # returns when both health checks pass
docker compose run --rm demo-setup
xdg-open http://127.0.0.1:3000/          # or open the URL in a browser
docker compose run --rm demo-check       # optional
docker compose down
```

### Run the Stage 1 tests (optional)

```sh
cd stage-1 && python3 -m unittest discover -s tests -v     # 54 tests, starts the server in-process
```

### How it fits together

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
- The frontend image also contains its own PostgreSQL. It is used only by two pages left
  over from the Lovable build, which aren't linked from the header. One is `/staff`. The
  other is `/factory`, a dashboard placeholder whose sections all say "pending" and which
  shows a four-seat design, not the three seats above. **The real factory evidence is
  FACTORY.md, `room.json`, `review/` and the git log.** The frontend's data is kept in the
  `tablekeeper_frontend-db` volume, which `docker compose down -v` removes.

Tested on 2026-10-06 with the commands above: both health checks passed. In a headless
Chromium browser, a new user signed up, signed out and logged back in, and booked a table.
A second user (`bob@demo.test`) had the same table open at an overlapping time. Their
booking was rejected with `409 table_unavailable`. The first reservation was still listed
as confirmed on My bookings after a browser refresh. `demo-check` passed, in both the
two-terminal and the background flows. `/backend/_test/*` returned 404 for GET and POST,
including encoded and `..` variants. A backend restart left the backend empty, as
expected. The Stage 1 unit tests (54) still pass.
