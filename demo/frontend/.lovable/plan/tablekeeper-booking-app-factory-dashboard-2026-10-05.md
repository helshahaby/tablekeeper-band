# Tablekeeper: booking app + factory dashboard

## What gets built

**1. Tablekeeper booking site (OpenTable-style)**
- Home page: browse restaurants, pick a date, time and party size, and see open slots.
- Restaurant page: tables, opening hours shown in the restaurant's own time zone, and a booking form.
- Booking confirmation, a "My bookings" page and cancellation.
- Sign in with email and password, so bookings belong to a person.
- Restaurant staff view: today's bookings per table.

**2. Double-booking guarantee (the core of the challenge)**
- The database itself refuses two bookings that overlap on the same table. The app does not rely on checks in the page.
- Retries are safe: every booking request carries a one-time key, so sending the same request twice returns the original booking instead of making a second one.
- All times are stored in one universal clock and converted to the restaurant's time zone for display. This covers daylight-saving changes.
- A "stress test" button on the dashboard fires many booking attempts at the same slot at once, then shows that exactly one succeeded.

**3. Factory dashboard (for the video walkthrough)**
- Stages 1 to 4 with their status.
- Agent seats with their generic mandates (planner, implementer, reviewer, verifier). The text avoids track-specific details, per the rules.
- A handoff timeline showing the plan, the code change, the evidence and the check result.
- A costs table (tokens, time, money per stage) and a "recovery" log of bad work that was caught and redone.
- You enter or paste this data from your BAND room. It is saved in the database.
- Live results from the stress test feed this page as evidence.

## What stays outside Lovable (you do this)
- Running the BAND Desktop room, exporting it, and recording the video.
- Writing FACTORY.md and the stage-1/ to stage-4/ folders. I can draft FACTORY.md text for you to copy.
- **The offline container.** Lovable Cloud is a hosted service, so this app needs internet. For the "clean container, no outbound network" rule, export the code to GitHub. Then add a Dockerfile that bundles a local Postgres with the same schema. I will keep the database logic in plain SQL files so this swap is easy, and I can write the Dockerfile and setup script as files you copy.

## Technical details
- Turn on Lovable Cloud (database plus sign-in).
- Tables: restaurants (with an IANA timezone), tables, bookings (with a timestamptz range, status and a unique idempotency key), user_roles (with a staff role), factory_stages, seats, handoffs, cost_entries, recovery_events.
- Overlap rule: `btree_gist` plus `EXCLUDE USING gist (table_id WITH =, slot WITH &&) WHERE status='confirmed'`. Retries: a unique `(user_id, idempotency_key)` that returns the existing row on conflict.
- Bookings go through a `createServerFn` that calls one SQL function (`book_table`), so the server is the source of truth. The stress test uses `Promise.all` with N parallel calls and records the results.
- RLS: users see only their own bookings, staff see their restaurant, and restaurants and tables are public to read.
- Migrations are written as portable SQL under `supabase/migrations`, so the same files can load into the offline Docker Postgres.
- Routes: `/`, `/r/$id`, `/bookings`, `/staff`, `/factory`, `/auth`. Each page gets its own title and description.
- Design: dark, industrial "factory floor" look with a monospace accent font for evidence and logs. No generic purple gradients.
