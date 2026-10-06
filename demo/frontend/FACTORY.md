# FACTORY.md — Tablekeeper Dark Factory

> Status: **draft**. Anything marked PENDING has not been verified and must be filled in from the real BAND Desktop room before submission. This file does not claim hackathon compliance.

## Seat setup (proposed)

Agent identities: **PENDING** — which model/agent fills each seat has not been recorded.

| Seat | Role | Mandate (generic) |
|---|---|---|
| Foreman | Planner | Break the brief into small, verifiable tasks. Define acceptance checks before work starts. Never write production code. |
| Builder | Implementer | Take one task at a time. Make the smallest change that satisfies its checks. Attach evidence of what changed and why. |
| Inspector | Reviewer | Read every change against its task. Reject work that lacks evidence or widens scope. Explain each rejection in one sentence. |
| Auditor | Verifier | Run the checks independently of the builder. Try to break the result with adversarial inputs. Report pass or fail with reproducible proof. |

## Design rationale

- The planner never writes code; the verifier sees only the artifact, not the builder's reasoning, so checks are not written to pass.
- Core invariants live in storage constraints, not application code, so no upstream agent mistake can violate them.
- Every handoff is a typed record: plan, change, evidence, check.

## What the shipped service does (verified facts)

- Overlapping confirmed bookings on one table are rejected by a database exclusion constraint.
- Each booking attempt carries an idempotency key; repeating it returns the original booking.
- Times are stored as absolute timestamps and converted with each restaurant's IANA time zone.
- Verified locally against PostgreSQL 17 on a fresh database: 50 parallel attempts on one slot → 1 confirmed, 49 refused, 0 errors; same request ×5 → 1 booking. See RUN.md for what is and is not verified.

## BAND room evidence

**PENDING** — room recording, room export and handoff log not yet attached.

## Measured costs

**PENDING** — no tokens, minutes or USD have been measured. Record them on the `/factory` page or here.

## Stages

| Stage | Status |
|---|---|
| stage-1/ | PENDING |
| stage-2/ | PENDING |
| stage-3/ | PENDING |
| stage-4/ | PENDING |

## Recovery from bad work (intended process)

- Inspector rejects changes missing evidence → returned to Builder with a one-line reason.
- Auditor failures reopen the task; the Foreman may split it smaller.
- Every caught failure is logged in the recovery log with problem and fix.

Actual recoveries: **PENDING** — none recorded.

## Test results

- Local stress test and end-to-end flow: passed (see RUN.md, Verification status).
- Docker image build and `--network none` run: **PENDING**.
