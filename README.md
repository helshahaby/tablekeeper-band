# Tablekeeper — Band factory submission

- **Track:** `tablekeeper` (restaurant reservations)
- **Team:** Hossam Elshahaby (human owner; dispatched the task, did not steer the run)
- **Stage reached:** Stage 1 (`stage-1/`). No later stage was attempted in this run.

## How to read this repository

| Path | What it is | Written by |
|---|---|---|
| `mandates/` | Generic standing mandates, one per seat, named after the seat identity | planner-nc33 |
| `FACTORY.md` | Factory design: seats, handoffs, review loop, measured time and cost | planner-nc33 |
| `stage-1/` | The Stage 1 service: source, `Dockerfile`, `RUN.md`, black-box tests | developer-nc33 |
| `review/stage-1/` | The reviewer's independent spec-derived probe suite and clean-build review script | reviewer-nc34 |
| `room.json` | Full Band room download (added by the human after the run; see FACTORY.md) | Band |

Every commit is authored by the seat that wrote it (`git log --format='%h %an %s'`).

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
