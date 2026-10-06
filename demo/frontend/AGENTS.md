<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

## Architecture rules
- The no-double-booking invariant lives in the database (gist exclusion constraint on table+time range); app code must never be the only guard. Why: concurrency-safe by construction.
- All bookings go through the `book_table` SQL function via a server function, with a per-attempt idempotency key. Why: one write path; retries return the original row.
- Times are stored as timestamptz ranges; restaurant-local wall time is converted using the restaurant's IANA timezone in SQL. Why: DST-correct.
- Factory dashboard data (stages, seats, handoffs, costs, recoveries, stress runs) is stored in plain tables, public-readable. Why: it's shown as judge-facing evidence.
- Runtime has no hosted backend: `DATABASE_URL` selects TCP PostgreSQL, otherwise embedded PGlite; SQL lives in `db/migrations` and `db/seed`, applied on first DB use. Why: the service must start from a clean container with no outbound network.
- Auth is built in (scrypt password hashes, hashed session tokens in an httpOnly cookie). Why: no external identity provider at runtime.
- Migrations run on one reserved connection under an advisory lock. Why: session-level locks must share a session.
- Offline image = Dockerfile with PostgreSQL bound to 127.0.0.1 plus the node-server build. Why: single container, no network required to run.
