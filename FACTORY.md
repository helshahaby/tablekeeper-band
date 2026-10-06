# FACTORY.md — how this factory works

A three-seat band in one Band room. One seat plans and coordinates, one implements, and
one verifies independently. The human dispatches one task per stage and gives no other
input. This file is enough to stand the factory up again. The standing mandates are in
`mandates/`.

## Seats

| Seat identity | Role | Harness | Model | Owns |
|---|---|---|---|---|
| `planner-nc33` | Planner / coordinator | Claude Code | `claude-opus-5-5` | plan, rulings on ambiguities, handoffs, factory docs, final report |
| `developer-nc33` | Developer / implementer | Claude Code | `claude-opus-5-5` | everything under `stage-N/` and its commits |
| `reviewer-nc34` | Reviewer / verifier | Claude Code | `claude-opus-5-5` | verdicts, `review/stage-N/` probe suite, official harness runs |

All three seats run as Claude Code sessions on the same Linux host. They share the result
checkout at an absolute path and use the Band CLI (`band`, the "jam" plugin) for room
messaging. Each seat confirmed its own harness and model ID in the room before the
mandates were written. Commits use per-seat authors (`git -c user.name=<seat> ...`), so
`git log` attributes every change.

## Standing it up

1. Start three Claude Code windows in the result repository. In the first, run
   `/jam as planner`. In the others, run `/jam as developer with <planner-handle>` and
   `/jam as reviewer`. Then have the planner `chat add` any seat that came up in a
   different room.
2. Before any work, the planner verifies membership (`band chat participants`) and runs a
   nonce round trip with each seat. Each seat must quote the nonce back, which proves its
   live session is attached to the room.
3. Copy `mandates/` and rename the files to your seat identities.
4. Dispatch one task message to the planner with the spec path, the result repo and the
   harness command. Nothing else is sent until the planner's final report.

## Flow of one stage

1. **Plan (planner).** Reads the full spec and the event rules. Resolves the spec's
   ambiguities up front into a numbered decision list: stack, concurrency model,
   check-order precedence, time handling, idempotency storage. This keeps the
   implementer and reviewer working to the same reading.
2. **Handoffs (planner → developer, planner → reviewer).** Each is self-contained, with
   the task, absolute paths, scope boundary, decision list, required evidence and next
   actor. The complete spec text is pasted verbatim in numbered parts (1/4 to 4/4).
   Handoffs never point at a file or a message id instead of giving the text.
3. **Parallel work.** The developer implements, tests and commits. At the same time the
   reviewer writes a black-box probe suite from the spec text alone, before seeing the
   code. That keeps the probes independent of the implementation and of the shipped tests.
4. **Review loop.** The developer posts a full commit hash. The reviewer checks out that
   exact revision, builds it with a clean `git archive` under 2 CPUs and 2 GiB, follows
   RUN.md literally, runs its probes and the official harness, and reads the source for
   races, partial writes and stored-on-failure idempotency. Each rejection is a numbered
   list with the spec quote, request, expected result and actual result.
5. **Rulings.** When the developer and reviewer read the spec differently, the planner
   rules against the spec text and sends the same ruling to both. The reviewer turns it
   into a hard probe, and the developer implements it.
6. **Accept.** The reviewer accepts only a revision it re-ran itself, including an
   isolated-mode harness run (no network, 2 vCPU, 2 GiB).

## What happened in this Stage 1 run (measured from the room and git)

All times are UTC on 2026-10-06.

| Time | Event |
|---|---|
| 00:16:17 | Task dispatched to planner |
| 00:18:28 | Mandates committed (`d3ab671`) |
| 00:20:33 | Full handoffs (4 messages each, spec verbatim) delivered to developer and reviewer |
| 00:26:02 | First service commit (`9c60efa`) |
| 00:29:28 | Reviewer's independent probe suite committed (`e39cdee`) |
| 00:31:45 | Developer hands off `42d743f` |
| 00:33:31 | Reviewer **rejects** `42d743f`: harness `s1-01` 117/120. Three findings: seeded reference format, overlapping seeds, seeds bypassing booking rules |
| 00:35:33 | Repairs `455776c`, `1904156`, `aa32fd7`, each answered per finding |
| 00:40:00 | Reviewer accepts `aa32fd7` (s1-02 host and s1-final isolated, 120/120) and lists 4 judgement-call gaps |
| 00:41:05 | Planner rules on the moves error-precedence gap. Reviewer adds probes (`7c0645a`), developer implements (`5d70f0e`) |
| 00:42:54 | Reviewer **accepts** `5d70f0e`: s1-final2 isolated 120/120, probes 456/456 |

From dispatch to the final verdict took **about 27 minutes** of wall-clock time.

### Bad work the factory caught

- **Missing fixture validation** (`42d743f`): reset accepted malformed references,
  overlapping seeded bookings, and bookings that break slot, hours and capacity rules.
  The reviewer caught it with the harness and its own probes, and it was fixed in three
  traceable commits.
- **Committed bytecode** (`9c60efa`): the reviewer flagged it before handoff, and it was
  fixed in `8fb29f2`.
- **Moves error precedence** (`aa32fd7`): the reviewer reported an ambiguous ordering
  that the shipped checks did not exercise. The planner ruled, and the behaviour changed
  in `5d70f0e`.
- **An overruled planner decision:** the developer changed the planner's request-order
  rule (auth before body parsing) with a spec quote, and the planner accepted it.

### Beyond the shipped checks

The reviewer's probe suite (`review/stage-1/probe.py`, 456 checks) and the developer's
54 tests cover, among others:

- 50-way concurrent booking of one table (exactly one 201) and concurrent identical
  idempotent requests (one 201, the rest 200 with the same body)
- swaps inside atomic moves, and all-or-nothing rollback including retry keys
- DST gaps and overlaps for Europe/Berlin and America/New_York, with absolute durations
- export/import preserving tokens, receipts and counters, with an invalid import leaving
  the destination unchanged
- the 400-vs-422 type rules, query-integer syntax, and the cutoff boundary

## Design choices and what they cost

- **One global lock around all state.** Writes are serialized, so double booking,
  duplicate idempotent effects and partial multi-item writes are impossible by
  construction. The cost is no write parallelism, which is fine at the required 50
  concurrent requests.
- **Python stdlib with vendored tzdata, in-memory state.** No runtime dependencies, starts
  in about 0.3 s, uses about 200 MiB under load. State does not survive a restart, which
  the spec allows.
- **Planner pre-resolves ambiguities.** It costs planner time up front, but there was
  only one disputed reading in the whole run.
- **Reviewer writes probes before seeing code.** This makes the review independent and
  gives the reviewer real work during the build. The cost is reviewer tokens about equal
  to the developer's.
- **Spec pasted verbatim in every handoff.** This costs extra input tokens, but no seat
  depends on reading another seat's files.

## Measured cost (model spend)

Token usage comes from each seat's Claude Code session log
(`~/.claude/projects/<repo>/*.jsonl`), counting assistant turns from dispatch
(00:16:17Z) to the final report. Cost is an **estimate at Anthropic API list rates** for
`claude-opus-5-5`: $4/MTok input, $20/MTok output and $0.20/MTok cache read, as given in
Anthropic's model table. Cache writes are priced at $5/MTok, which **assumes** the
standard 1.25× input multiplier because no first-party figure was available to the band.
The seats may run on a subscription, so this is an API-equivalent figure, **not an
invoice**.

| Seat | Turns | Output tok | Cache write tok | Cache read tok | Est. cost |
|---|---:|---:|---:|---:|---:|
| planner-nc33 | 58 | 26,573 | 211,601 | 6,016,586 | $2.79 |
| developer-nc33 | 64 | 84,278 | 128,227 | 7,872,974 | $3.90 |
| reviewer-nc34 | 61 | 79,042 | 153,591 | 7,840,291 | $3.92 |
| **Total** | 183 | 189,893 | 493,419 | 21,729,851 | **≈ $10.61** |

Uncached input was 420 tokens in total. The planner's figures stop when its turns stopped
being counted, shortly before this file was written, so its final turns are missing.
Room setup before dispatch is excluded. **Unavailable:** actual billed cost and
per-request latency.

## Known limitations (recorded by the reviewer, accepted by the planner)

- Import trusts the booking rules (grid, hours, capacity) stored in an exported state. It
  checks only format, references and overlap.
- Seeded users are hashed with scrypt N=2^12 and signups with N=2^13. Both are salted
  KDFs, and N=2^12 keeps large resets within the 10 s limit.
- A wrong method on a known path returns 405 `method_not_allowed`, a code that is not in
  the spec table. The error body shape is correct.
- Only the shipped checks (about 83% of the graded Stage 1 suite) were run. The
  held-back checks are unknown.

## What a team should change for its own use

Rename the mandate files to its seat identities and keep them generic. All
track-specific content goes in the dispatched task and the planner's handoffs, never in
`mandates/`.
