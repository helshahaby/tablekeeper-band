#!/usr/bin/env python3
"""Black-box probe suite for Tablekeeper Stage 1, derived from the spec text only.

Usage: probe.py BASE_URL [--only SUBSTR] [--verbose]

Each probe resets the service to a known fixture, exercises one area of the
spec and records PASS / FAIL (hard rule) or WARN (rule whose expected status
is arguable from the spec text). Every 4xx/5xx body is also checked against
the §5 error shape, and every 5xx is recorded as a failure.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import re
import sys
import threading
import time
import traceback
import uuid

import httpx

BASE = ""
VERBOSE = False
_tls = threading.local()
_lock = threading.Lock()
SERVER_ERRORS: list[str] = []
SHAPE_ERRORS: list[str] = []

RESULTS: list[tuple[str, str, str, str]] = []  # (status, probe, check, detail)
CURRENT = ["?"]


# ---------------------------------------------------------------- HTTP layer

def client() -> httpx.Client:
    c = getattr(_tls, "c", None)
    if c is None:
        c = httpx.Client(base_url=BASE, timeout=15.0)
        _tls.c = c
    return c


class R:
    def __init__(self, status: int, body, text: str):
        self.status = status
        self.body = body
        self.text = text

    @property
    def code(self):
        if isinstance(self.body, dict) and isinstance(self.body.get("error"), dict):
            return self.body["error"].get("code")
        return None

    def __repr__(self):
        t = self.text if len(self.text) < 400 else self.text[:400] + "..."
        return f"{self.status} {t}"


def call(method, path, token=None, key=None, json_body=None, raw=None, params=None,
         headers=None) -> R:
    h = {}
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    if key is not None:
        h["Idempotency-Key"] = key
    if headers:
        h.update(headers)
    content = None
    if raw is not None:
        content = raw if isinstance(raw, (bytes, str)) else None
        h.setdefault("Content-Type", "application/json")
    elif json_body is not None:
        content = json.dumps(json_body)
        h.setdefault("Content-Type", "application/json")
    try:
        resp = client().request(method, path, content=content, params=params, headers=h)
    except httpx.HTTPError as e:
        with _lock:
            SERVER_ERRORS.append(f"[{CURRENT[0]}] {method} {path}: transport error {e!r}")
        return R(0, None, f"transport error {e!r}")
    text = resp.text
    try:
        body = resp.json() if text else None
    except ValueError:
        body = None
    r = R(resp.status_code, body, text)
    if resp.status_code >= 500:
        with _lock:
            SERVER_ERRORS.append(f"[{CURRENT[0]}] {method} {path} -> {r!r}")
    if resp.status_code >= 400:
        ok = (isinstance(body, dict) and isinstance(body.get("error"), dict)
              and isinstance(body["error"].get("code"), str)
              and isinstance(body["error"].get("message"), str))
        if not ok:
            with _lock:
                SHAPE_ERRORS.append(f"[{CURRENT[0]}] {method} {path} -> {r!r}")
    return r


# ---------------------------------------------------------------- recording

def record(status, check, detail=""):
    RESULTS.append((status, CURRENT[0], check, detail))
    if VERBOSE or status != "PASS":
        print(f"  {status:4} {check}" + (f"  -- {detail}" if detail and status != "PASS" else ""))


def check(cond, name, detail=""):
    record("PASS" if cond else "FAIL", name, "" if cond else detail)
    return cond


def soft(cond, name, detail=""):
    record("PASS" if cond else "WARN", name, "" if cond else detail)
    return cond


def expect(r: R, status, code=None, name="", hard=True):
    ok = r.status == status and (code is None or r.code == code)
    want = f"{status}" + (f" {code}" if code else "")
    (check if hard else soft)(ok, name or f"expect {want}", f"want {want}, got {r!r}")
    return ok


# ---------------------------------------------------------------- fixture

LONG_ID = "r_" + "x" * 62  # exactly 64 chars
assert len(LONG_ID) == 64

ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def hours(opens, closes, days=ALL_DAYS):
    return [{"weekday": d, "opens": opens, "closes": closes} for d in days]


def base_fixture():
    return {
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
             "display_name": "Ada"},
            {"id": "u_bob", "email": "bob@example.com", "password": "battery staple",
             "display_name": "Bob"},
            {"id": "u_cyd", "email": "cyd@example.com", "password": "tr0ub4dor&3",
             "display_name": "Cyd"},
        ],
        "restaurants": [
            {   # spec example: thu/fri only
                "id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin",
                "slot_minutes": 30, "reservation_duration_minutes": 90,
                "cancellation_cutoff_minutes": 120,
                "opening_hours": [
                    {"weekday": "thu", "opens": "18:00", "closes": "23:00"},
                    {"weekday": "fri", "opens": "18:00", "closes": "23:30"},
                ],
                "tables": [
                    {"id": "t_1", "label": "1", "capacity": 2},
                    {"id": "t_2", "label": "2", "capacity": 4},
                ],
            },
            {   # general purpose, open every day
                "id": "r_main", "name": "Main", "timezone": "Europe/Berlin",
                "slot_minutes": 30, "reservation_duration_minutes": 90,
                "cancellation_cutoff_minutes": 120,
                "opening_hours": hours("12:00", "23:00"),
                "tables": [
                    {"id": "m_1", "label": "1", "capacity": 2},
                    {"id": "m_2", "label": "2", "capacity": 4},
                    {"id": "m_3", "label": "3", "capacity": 4},
                    {"id": "m_4", "label": "4", "capacity": 8},
                ],
            },
            {   # second restaurant for cross-restaurant checks
                "id": "r_other", "name": "Other", "timezone": "Europe/Berlin",
                "slot_minutes": 30, "reservation_duration_minutes": 90,
                "cancellation_cutoff_minutes": 120,
                "opening_hours": hours("12:00", "23:00"),
                "tables": [{"id": "o_1", "label": "1", "capacity": 4}],
            },
            {   # DST, Berlin, overnight hours
                "id": "r_ber_dst", "name": "Berlin Night", "timezone": "Europe/Berlin",
                "slot_minutes": 30, "reservation_duration_minutes": 90,
                "cancellation_cutoff_minutes": 60,
                "opening_hours": hours("00:00", "06:00"),
                "tables": [{"id": "b_1", "label": "1", "capacity": 4},
                           {"id": "b_2", "label": "2", "capacity": 4}],
            },
            {   # DST, New York
                "id": "r_ny_dst", "name": "NY Night", "timezone": "America/New_York",
                "slot_minutes": 30, "reservation_duration_minutes": 90,
                "cancellation_cutoff_minutes": 60,
                "opening_hours": hours("00:00", "06:00"),
                "tables": [{"id": "n_1", "label": "1", "capacity": 4}],
            },
            {   # odd grid: opens 17:45, slot 30, duration 60, closes 22:00
                "id": "r_grid", "name": "Grid", "timezone": "Europe/Berlin",
                "slot_minutes": 30, "reservation_duration_minutes": 60,
                "cancellation_cutoff_minutes": 0,
                "opening_hours": hours("17:45", "22:00"),
                "tables": [{"id": "g_1", "label": "1", "capacity": 4}],
            },
            {   # minute grid in UTC for cutoff-boundary probes
                "id": "r_utc", "name": "UTC", "timezone": "UTC",
                "slot_minutes": 1, "reservation_duration_minutes": 30,
                "cancellation_cutoff_minutes": 60,
                "opening_hours": hours("00:00", "23:59"),
                "tables": [{"id": "u_t1", "label": "1", "capacity": 4},
                           {"id": "u_t2", "label": "2", "capacity": 4}],
            },
            {   # 64-char ids are valid
                "id": LONG_ID, "name": "Long", "timezone": "Europe/Berlin",
                "slot_minutes": 30, "reservation_duration_minutes": 90,
                "cancellation_cutoff_minutes": 120,
                "opening_hours": hours("12:00", "23:00"),
                "tables": [{"id": "t_" + "y" * 62, "label": "L", "capacity": 4}],
            },
        ],
        "reservations": [],
    }


FUT = "2027-06-10"          # Thursday, Berlin CEST (+02:00)
FUT_WINTER = "2027-01-14"   # Thursday, Berlin CET (+01:00)
PAST = "2020-01-02"         # Thursday


def reset(fixture=None):
    r = call("POST", "/_test/reset", json_body=fixture or base_fixture())
    if r.status != 204:
        raise RuntimeError(f"reset failed: {r!r}")


def login(email, password):
    r = call("POST", "/auth/login", json_body={"email": email, "password": password})
    if r.status != 200:
        raise RuntimeError(f"login {email} failed: {r!r}")
    return r.body["token"]


def tokens():
    return (login("ada@example.com", "correct horse"),
            login("bob@example.com", "battery staple"),
            login("cyd@example.com", "tr0ub4dor&3"))


def k():
    return "k-" + uuid.uuid4().hex


def book(tok, table, local, rest="r_main", party=2, key=None):
    return call("POST", "/reservations", tok, key or k(), {
        "restaurant_id": rest, "table_id": table, "starts_at_local": local,
        "party_size": party})


def must_book(tok, table, local, rest="r_main", party=2):
    r = book(tok, table, local, rest, party)
    if r.status != 201:
        raise RuntimeError(f"setup booking {rest}/{table}@{local} failed: {r!r}")
    return r.body


def avail(rest, date, party=1):
    r = call("GET", "/availability", params={"restaurant_id": rest, "date": date,
                                               "party_size": str(party)})
    if r.status != 200:
        raise RuntimeError(f"availability failed: {r!r}")
    return r.body


def slot(av, local):
    for s in av["slots"]:
        if s["starts_at_local"] == local:
            return s
    return None


def get_res(tok, ref):
    return call("GET", f"/reservations/{ref}", tok)


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


RFC3339_OFF = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?([+-]\d\d:\d\d|Z)$")
RFC3339_STRICT_OFF = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?[+-]\d\d:\d\d$")


def overlaps_ok(all_res):
    """Return list of overlapping confirmed pairs on the same table."""
    bad = []
    by_table = {}
    for x in all_res:
        if x.get("status") != "confirmed":
            continue
        by_table.setdefault((x["restaurant_id"], x["table_id"]), []).append(
            (parse_ts(x["starts_at"]), parse_ts(x["ends_at"]), x["reference"]))
    for key_, items in by_table.items():
        items.sort()
        for a, b in zip(items, items[1:]):
            if b[0] < a[1]:
                bad.append((key_, a[2], b[2]))
    return bad


def all_reservations(*toks):
    out = []
    for t in toks:
        r = call("GET", "/reservations", t)
        out.extend(r.body["reservations"])
    return out



# ================================================================ probes

PROBES = []


def probe(fn):
    PROBES.append(fn)
    return fn


@probe
def health_and_public():
    r = call("GET", "/health")
    check(r.status == 200 and r.body == {"status": "ok"}, "GET /health 200 {status: ok}", repr(r))
    reset()
    r = call("GET", "/restaurants")
    ok = r.status == 200 and isinstance(r.body, dict) and isinstance(r.body.get("restaurants"), list)
    check(ok, "GET /restaurants public 200", repr(r))
    if ok:
        ids = [x.get("id") for x in r.body["restaurants"]]
        check(ids[:2] == ["r_anker", "r_main"] and LONG_ID in ids, "restaurants listed (fixture order)", str(ids))
        first = r.body["restaurants"][0]
        check(first.get("name") == "Zum Anker" and first.get("timezone") == "Europe/Berlin",
              "list item has id/name/timezone", repr(first))
    r = call("GET", "/restaurants/r_anker")
    ok = r.status == 200
    check(ok, "GET /restaurants/{id} public 200", repr(r))
    if ok:
        b = r.body
        fx = base_fixture()["restaurants"][0]
        for f in ("slot_minutes", "reservation_duration_minutes", "cancellation_cutoff_minutes",
                  "opening_hours", "tables", "timezone", "name", "id"):
            check(b.get(f) == fx[f], f"restaurant detail field {f} matches fixture", f"{b.get(f)!r} != {fx[f]!r}")
    expect(call("GET", "/restaurants/nope"), 404, "not_found", "unknown restaurant 404")
    r = call("GET", f"/restaurants/{LONG_ID}")
    check(r.status == 200, "64-char restaurant id accepted", repr(r))
    r = call("GET", "/availability", params={"restaurant_id": "r_main", "date": FUT, "party_size": "2"})
    check(r.status == 200, "GET /availability public (no token)", repr(r))
    r = call("GET", "/health")
    ct = client().get("/health").headers.get("content-type", "")
    check("application/json" in ct, "health content-type is application/json", ct)


@probe
def auth_rules():
    reset()
    r = call("POST", "/auth/signup", json_body={"email": "new@example.com", "password": "12345678",
                                                "display_name": "New"})
    ok = expect(r, 201, name="signup 201 with 8-char password")
    if ok:
        check(set(r.body) >= {"user_id", "display_name", "token"} and r.body["display_name"] == "New",
              "signup body shape", repr(r))
        t1 = r.body["token"]
        uid = r.body["user_id"]
        check(isinstance(uid, str) and len(uid) <= 64, "user_id string <=64", repr(uid))
        expect(call("GET", "/reservations", t1), 200, name="signup token usable")
        r2 = call("POST", "/auth/login", json_body={"email": "new@example.com", "password": "12345678"})
        if expect(r2, 200, name="login after signup 200"):
            check(r2.body.get("user_id") == uid and r2.body.get("display_name") == "New",
                  "login body user_id/display_name", repr(r2))
            t2 = r2.body["token"]
            expect(call("GET", "/reservations", t2), 200, name="second token valid")
            expect(call("GET", "/reservations", t1), 200, name="first token still valid (multiple sessions)")
    expect(call("POST", "/auth/signup", json_body={"email": "new@example.com", "password": "abcdefgh",
                                                    "display_name": "Dup"}), 409, "email_taken",
           "duplicate signup 409 email_taken")
    expect(call("POST", "/auth/signup", json_body={"email": "ada@example.com", "password": "abcdefgh",
                                                    "display_name": "Dup"}), 409, "email_taken",
           "signup with seeded email 409 email_taken")
    expect(call("POST", "/auth/signup", json_body={"email": "short@example.com", "password": "1234567",
                                                    "display_name": "S"}), 422, "validation_failed",
           "7-char password 422")
    for bad in ["noatsign", "@domain.com", "local@", "", "a@@b"]:
        expect(call("POST", "/auth/signup", json_body={"email": bad, "password": "abcdefgh",
                                                        "display_name": "B"}), 422, "validation_failed",
               f"bad email {bad!r} 422", hard=(bad != "a@@b"))
    expect(call("POST", "/auth/signup", json_body={"password": "abcdefgh", "display_name": "B"}),
           422, "validation_failed", "signup missing email 422")
    expect(call("POST", "/auth/signup", json_body={"email": 5, "password": "abcdefgh", "display_name": "B"}),
           400, "malformed_request", "signup email wrong type 400")
    expect(call("POST", "/auth/signup", raw="{not json"), 400, "malformed_request", "signup bad JSON 400")
    # seeded users
    r = call("POST", "/auth/login", json_body={"email": "ada@example.com", "password": "correct horse"})
    if expect(r, 200, name="seeded user logs in immediately"):
        check(r.body.get("user_id") == "u_ada" and r.body.get("display_name") == "Ada",
              "seeded login returns fixture user_id/display_name", repr(r))
    expect(call("POST", "/auth/login", json_body={"email": "ada@example.com", "password": "wrong pass"}),
           401, "unauthenticated", "wrong password 401")
    expect(call("POST", "/auth/login", json_body={"email": "ghost@example.com", "password": "whatever1"}),
           401, "unauthenticated", "unknown email 401")
    expect(call("POST", "/auth/login", json_body={"email": "ada@example.com", "password": "correct horse",
                                                   "extra": [1, 2]}), 200, name="login ignores unknown fields")
    # bearer variants
    expect(call("GET", "/reservations"), 401, "unauthenticated", "no Authorization 401")
    expect(call("GET", "/reservations", headers={"Authorization": "Bearer"}), 401, "unauthenticated",
           "'Bearer' without token 401")
    expect(call("GET", "/reservations", headers={"Authorization": "Basic YWRhOnB3"}), 401, "unauthenticated",
           "Basic scheme 401")
    expect(call("GET", "/reservations", token="definitely-not-a-token"), 401, "unauthenticated",
           "unknown token 401")
    expect(call("POST", "/reservations", key=k(), json_body={"restaurant_id": "r_main"}), 401,
           "unauthenticated", "POST /reservations no token 401")
    expect(call("POST", "/reservation-moves", key=k(), json_body={"moves": []}), 401, "unauthenticated",
           "POST /reservation-moves no token 401")
    expect(call("GET", "/reservations/ABCDEF"), 401, "unauthenticated", "GET /reservations/{ref} no token 401")
    expect(call("POST", "/reservations/ABCDEF/cancel"), 401, "unauthenticated", "cancel no token 401")
    expect(call("PATCH", "/reservations/ABCDEF", json_body={}), 401, "unauthenticated", "PATCH no token 401")


@probe
def availability_rules():
    reset()
    av = avail("r_anker", "2026-09-24", 4)
    check(av.get("restaurant_id") == "r_anker" and av.get("date") == "2026-09-24"
          and av.get("timezone") == "Europe/Berlin", "availability header fields", repr(av)[:300])
    locs = [s["starts_at_local"] for s in av["slots"]]
    want = [f"2026-09-24T{h:02d}:{m:02d}" for h in range(18, 22) for m in (0, 30)]
    want = [w for w in want if w <= "2026-09-24T21:30"]
    check(locs == want, "thu slots 18:00..21:30 (21:30+90=23:00 ok, 22:00 excluded)", str(locs))
    s = slot(av, "2026-09-24T18:00")
    check(s and s["starts_at"] == "2026-09-24T18:00:00+02:00", "starts_at with +02:00 offset", repr(s))
    check(s and s["available_table_ids"] == ["t_2"], "party 4 -> only t_2", repr(s))
    av2 = avail("r_anker", "2026-09-24", 1)
    check(slot(av2, "2026-09-24T18:00")["available_table_ids"] == ["t_1", "t_2"],
          "party 1 -> both tables in fixture order", repr(slot(av2, "2026-09-24T18:00")))
    av5 = avail("r_anker", "2026-09-24", 5)
    check(len(av5["slots"]) == 8 and all(s["available_table_ids"] == [] for s in av5["slots"]),
          "party bigger than all tables -> slots with empty lists", repr(av5)[:300])
    fri = avail("r_anker", "2026-09-25", 2)
    check(fri["slots"][-1]["starts_at_local"] == "2026-09-25T22:00", "fri last slot 22:00 (closes 23:30)",
          repr(fri["slots"][-1:]))
    check(avail("r_anker", "2026-09-28", 2)["slots"] == [], "closed day (mon) -> slots []")
    w = avail("r_main", FUT_WINTER, 2)
    check(slot(w, f"{FUT_WINTER}T12:00")["starts_at"] == f"{FUT_WINTER}T12:00:00+01:00",
          "winter offset +01:00", repr(slot(w, f"{FUT_WINTER}T12:00")))
    g = avail("r_grid", FUT, 1)
    gl = [s["starts_at_local"][11:] for s in g["slots"]]
    check(gl == ["17:45", "18:15", "18:45", "19:15", "19:45", "20:15", "20:45"],
          "grid anchored at opening 17:45, last slot 20:45 (+60 <= 22:00)", str(gl))
    # booked table disappears, half-open interval
    a, b, c = tokens()
    must_book(a, "t_2", "2026-09-24T19:00", rest="r_anker", party=4)
    av = avail("r_anker", "2026-09-24", 4)
    check(slot(av, "2026-09-24T18:00")["available_table_ids"] == [], "18:00 overlaps 19:00 booking",
          repr(slot(av, "2026-09-24T18:00")))
    check(slot(av, "2026-09-24T17:30") is None, "no slot before opening")
    check(slot(av, "2026-09-24T20:00")["available_table_ids"] == [], "20:00 overlaps 19:00-20:30")
    check(slot(av, "2026-09-24T20:30")["available_table_ids"] == ["t_2"],
          "20:30 free (half-open interval)", repr(slot(av, "2026-09-24T20:30")))
    check(slot(av, "2026-09-24T17:30") is None and slot(av, "2026-09-24T18:00") is not None,
          "slot with no table still listed")
    # validation of query params
    base = {"restaurant_id": "r_main", "date": FUT, "party_size": "2"}
    for missing in base:
        p = dict(base)
        del p[missing]
        expect(call("GET", "/availability", params=p), 422, "validation_failed", f"missing {missing} 422")
    for bad in ["1e9", "4.0", "+4", "abc", "0", "-1", "", " 4", "4 "]:
        p = dict(base, party_size=bad)
        expect(call("GET", "/availability", params=p), 422, "validation_failed", f"party_size={bad!r} 422")
    for bad in ["2026-02-30", "2026-13-01", "26-01-01", "2026/09/24", "2026-09-24T00:00", "", "tomorrow"]:
        p = dict(base, date=bad)
        expect(call("GET", "/availability", params=p), 422, "validation_failed", f"date={bad!r} 422")
    expect(call("GET", "/availability", params=dict(base, restaurant_id="nope")), 404, "not_found",
           "unknown restaurant 404")
    r = call("GET", "/availability", params=dict(base, foo="bar", party_size="02"))
    check(r.status == 200, "unknown query params ignored (and '02' digits accepted)", repr(r))
    r = call("GET", "/availability", params=dict(base, party_size="100000000000000000000000"))
    soft(r.status in (200, 422), "huge party_size no 5xx", repr(r))


@probe
def create_reservation_rules():
    reset()
    a, b, c = tokens()
    r = book(a, "m_2", f"{FUT}T19:00", party=4)
    if expect(r, 201, name="create 201"):
        x = r.body
        check(x.get("restaurant_id") == "r_main" and x.get("table_id") == "m_2" and x.get("party_size") == 4
              and x.get("status") == "confirmed" and x.get("starts_at_local") == f"{FUT}T19:00",
              "create echo fields", repr(x))
        check(x.get("starts_at") == f"{FUT}T19:00:00+02:00", "starts_at resolved +02:00", repr(x.get("starts_at")))
        check(x.get("ends_at") == f"{FUT}T20:30:00+02:00", "ends_at = +90 min", repr(x.get("ends_at")))
        check(isinstance(x.get("reference"), str) and re.fullmatch(r"[A-Z0-9]{6,12}", x["reference"]) is not None,
              "reference 6-12 A-Z0-9", repr(x.get("reference")))
        check(isinstance(x.get("reservation_id"), str) and 0 < len(x["reservation_id"]) <= 64,
              "reservation_id string <=64", repr(x.get("reservation_id")))
        check(isinstance(x.get("created_at"), str) and RFC3339_OFF.match(x["created_at"]) is not None,
              "created_at RFC3339 with offset", repr(x.get("created_at")))
        g = get_res(a, x["reference"])
        check(g.status == 200 and g.body == x, "GET by reference equals create body", repr(g))
    # half-open adjacency
    expect(book(a, "m_2", f"{FUT}T20:30", party=4), 201, name="adjacent booking at end time 201")
    expect(book(b, "m_2", f"{FUT}T17:30", party=2), 201, name="adjacent booking ending at start 201")
    expect(book(b, "m_2", f"{FUT}T18:00", party=2), 409, "table_unavailable", "overlap (18:00) 409")
    expect(book(b, "m_2", f"{FUT}T20:00", party=2), 409, "table_unavailable", "overlap (20:00) 409")
    expect(book(a, "m_2", f"{FUT}T19:00", party=4), 409, "table_unavailable", "same slot 409 (same user)")
    # errors
    expect(book(a, "m_1", f"{FUT}T19:15"), 422, "not_on_slot_grid", "off-grid 422 not_on_slot_grid")
    expect(book(a, "g_1", f"{FUT}T18:00", rest="r_grid"), 422, "not_on_slot_grid",
           "grid anchored at opening: 18:00 off grid for 17:45 opening")
    expect(book(a, "g_1", f"{FUT}T21:15", rest="r_grid"), 422, "outside_opening_hours",
           "on-grid slot ending after close 422 outside_opening_hours")
    expect(book(a, "m_1", f"{FUT}T22:00"), 422, "outside_opening_hours", "22:00+90 > 23:00 422")
    expect(book(a, "m_1", f"{FUT}T21:30"), 201, name="21:30+90 == 23:00 allowed")
    expect(book(a, "t_1", "2026-09-28T19:00", rest="r_anker"), 422, "outside_opening_hours",
           "closed day 422 outside_opening_hours")
    expect(book(a, "m_1", f"{FUT}T19:00", party=3), 422, "party_exceeds_capacity", "party > capacity 422")
    for bad, label in [(0, "0"), (-1, "-1"), ("2", "string"), (True, "bool"), (2.5, "float 2.5"),
                       (None, "null")]:
        r = call("POST", "/reservations", a, k(), {"restaurant_id": "r_main", "table_id": "m_4",
                                                   "starts_at_local": f"{FUT}T13:00", "party_size": bad})
        expect(r, 422, "validation_failed", f"party_size {label} 422", hard=(label != "null"))
    r = call("POST", "/reservations", a, k(), {"restaurant_id": "r_main", "table_id": "m_4",
                                               "starts_at_local": f"{FUT}T13:00", "party_size": 2.0})
    soft(r.status in (201, 422), "party_size 2.0 -> 201 or 422 (no 5xx)", repr(r))
    for bad in [f"{FUT}T13:00:00", f"{FUT}T13:00Z", f"{FUT}T13:00+02:00", f"{FUT} 13:00", "2027-02-30T13:00",
                "2027-06-10T25:00", "2027-06-10T13:5", "13:00", ""]:
        expect(book(a, "m_4", bad), 422, "validation_failed", f"starts_at_local {bad!r} 422")
    r = call("POST", "/reservations", a, k(), {"restaurant_id": "r_main", "table_id": "m_4",
                                               "starts_at_local": 1300, "party_size": 2})
    soft(r.status in (400, 422) and r.code in ("malformed_request", "validation_failed"),
         "starts_at_local number -> 400 malformed or 422", repr(r))
    r = call("POST", "/reservations", a, k(), {"restaurant_id": "r_main", "table_id": 7,
                                               "starts_at_local": f"{FUT}T13:00", "party_size": 2})
    expect(r, 400, "malformed_request", "table_id wrong type 400")
    r = call("POST", "/reservations", a, k(), {"restaurant_id": ["r_main"], "table_id": "m_4",
                                               "starts_at_local": f"{FUT}T13:00", "party_size": 2})
    expect(r, 400, "malformed_request", "restaurant_id wrong type 400")
    for f in ("restaurant_id", "table_id", "starts_at_local", "party_size"):
        body = {"restaurant_id": "r_main", "table_id": "m_4", "starts_at_local": f"{FUT}T13:00", "party_size": 2}
        del body[f]
        expect(call("POST", "/reservations", a, k(), body), 422, "validation_failed", f"missing {f} 422")
    expect(call("POST", "/reservations", a, k(), raw="{bad json"), 400, "malformed_request", "bad JSON 400")
    r = call("POST", "/reservations", a, k(), raw="[1,2]")
    expect(r, 400, "malformed_request", "JSON array body 400", hard=False)
    expect(book(a, "m_1", f"{FUT}T13:00", rest="nope"), 404, "not_found", "unknown restaurant 404")
    expect(book(a, "zz", f"{FUT}T13:00"), 404, "not_found", "unknown table 404")
    expect(book(a, "o_1", f"{FUT}T13:00"), 404, "not_found", "table of other restaurant 404")
    r = call("POST", "/reservations", a, k(), {"restaurant_id": "r_main", "table_id": "m_3",
                                               "starts_at_local": f"{FUT}T13:00", "party_size": 2,
                                               "status": "cancelled", "reference": "HACKED1", "zzz": {}})
    if expect(r, 201, name="unknown fields ignored -> 201"):
        check(r.body["status"] == "confirmed" and r.body["reference"] != "HACKED1",
              "client cannot set status/reference", repr(r))
    # past bookings allowed
    r = book(a, "m_1", f"{PAST}T19:00")
    expect(r, 201, name="past date bookable")
    # long ids
    r = book(a, "t_" + "y" * 62, f"{FUT}T19:00", rest=LONG_ID)
    expect(r, 201, name="64-char ids bookable")
    # references unique
    refs = [x["reference"] for x in all_reservations(a, b)]
    check(len(refs) == len(set(refs)), "references unique", str(refs))


@probe
def idempotency_rules():
    reset()
    a, b, c = tokens()
    body = {"restaurant_id": "r_main", "table_id": "m_2", "starts_at_local": f"{FUT}T19:00", "party_size": 2}
    key = k()
    r1 = call("POST", "/reservations", a, key, body)
    expect(r1, 201, name="first use 201")
    raw = '{ "party_size":2,  "starts_at_local":"%sT19:00","table_id":"m_2","restaurant_id":"r_main"}' % FUT
    r2 = call("POST", "/reservations", a, key, raw=raw)
    check(r2.status == 200 and r2.body == r1.body, "replay with reordered keys/whitespace 200 identical", repr(r2))
    check(len(all_reservations(a)) == 1, "replay created nothing")
    # different body -> 409 even if invalid
    expect(call("POST", "/reservations", a, key, dict(body, party_size=3)), 409, "idempotency_key_reuse",
           "same key different body 409")
    expect(call("POST", "/reservations", a, key, dict(body, party_size="x")), 409, "idempotency_key_reuse",
           "same key different invalid body (party_size string) 409")
    expect(call("POST", "/reservations", a, key, {"restaurant_id": "r_main"}), 409, "idempotency_key_reuse",
           "same key body missing fields 409")
    expect(call("POST", "/reservations", a, key, dict(body, table_id="nope")), 409, "idempotency_key_reuse",
           "same key body unknown table 409 (before resource checks)")
    expect(call("POST", "/reservations", a, key, dict(body, table_id=5)), 409, "idempotency_key_reuse",
           "same key body wrong-type field 409", hard=False)
    expect(call("POST", "/reservations", a, key, dict(body, extra=1)), 409, "idempotency_key_reuse",
           "same key body with extra unknown field is a different JSON value -> 409", hard=False)
    # per-user scope: Bob uses Ada's key with a different body
    rb = call("POST", "/reservations", b, key, dict(body, table_id="m_3"))
    expect(rb, 201, name="other user, same key string -> independent 201")
    # other user, same key, SAME body -> not a replay; table taken -> 409 table_unavailable
    expect(call("POST", "/reservations", c, key, body), 409, "table_unavailable",
           "other user same key same body is not a replay")
    # replay after cancel returns original body
    ref = r1.body["reference"]
    rc = call("POST", f"/reservations/{ref}/cancel", a)
    expect(rc, 200, name="cancel 200")
    r3 = call("POST", "/reservations", a, key, body)
    check(r3.status == 200 and r3.body == r1.body and r3.body["status"] == "confirmed",
          "replay after cancel -> 200 original body (status confirmed)", repr(r3))
    check(get_res(a, ref).body["status"] == "cancelled", "replay after cancel did not resurrect")
    # replay after patch
    key2 = k()
    body2 = dict(body, table_id="m_1", starts_at_local=f"{FUT}T13:00")
    o = call("POST", "/reservations", a, key2, body2)
    expect(o, 201, name="create for patch-replay")
    p = call("PATCH", f"/reservations/{o.body['reference']}", a, json_body={"starts_at_local": f"{FUT}T16:00"})
    expect(p, 200, name="patch 200")
    r4 = call("POST", "/reservations", a, key2, body2)
    check(r4.status == 200 and r4.body == o.body, "replay after patch -> original body", repr(r4))
    check(get_res(a, o.body["reference"]).body["starts_at_local"] == f"{FUT}T16:00",
          "replay after patch made no state change")
    # 13:00 slot was freed by the patch; replay must not re-book it
    s = slot(avail("r_main", FUT, 2), f"{FUT}T13:00")
    check("m_1" in s["available_table_ids"], "replay did not re-occupy original slot", repr(s))
    # 4xx original leaves key reusable
    key3 = k()
    bad = dict(body, party_size=0)
    expect(call("POST", "/reservations", a, key3, bad), 422, "validation_failed", "invalid first use 422")
    expect(call("POST", "/reservations", a, key3, bad), 422, "validation_failed",
           "same invalid body again -> evaluated afresh 422 (not 200)")
    good = dict(body, table_id="m_4", starts_at_local=f"{FUT}T15:00")
    expect(call("POST", "/reservations", a, key3, good), 201, name="key reusable after 4xx -> 201")
    # 409 table_unavailable original then reusable
    key4 = k()
    taken = dict(body, table_id="m_4", starts_at_local=f"{FUT}T15:00")
    expect(call("POST", "/reservations", b, key4, taken), 409, "table_unavailable", "conflict first use 409")
    expect(call("POST", "/reservations", b, key4, dict(taken, starts_at_local=f"{FUT}T17:00")), 201,
           name="key reusable after 409 conflict")
    # 404 original then reusable
    key5 = k()
    expect(call("POST", "/reservations", b, key5, dict(body, table_id="nope")), 404, "not_found", "404 first use")
    expect(call("POST", "/reservations", b, key5, dict(body, table_id="m_4", starts_at_local=f"{FUT}T21:00")),
           201, name="key reusable after 404")
    # header rules
    expect(call("POST", "/reservations", a, None, body), 400, "missing_idempotency_key", "absent key 400")
    expect(call("POST", "/reservations", a, "", body), 400, "missing_idempotency_key", "empty key 400")
    expect(call("POST", "/reservations", a, "x" * 256, dict(body, table_id="m_3", starts_at_local=f"{FUT}T12:00")),
           422, "validation_failed", "256-char key 422")
    expect(call("POST", "/reservations", a, "y" * 255, dict(body, table_id="m_3", starts_at_local=f"{FUT}T12:00")),
           201, name="255-char key 201")
    expect(call("POST", "/reservations", a, "z", dict(body, table_id="m_3", starts_at_local=f"{FUT}T21:30")),
           201, name="1-char key 201")
    # same key + same body on a different path is not a replay
    key6 = k()
    rr = must_book(a, "m_1", f"{FUT}T21:30")
    mv_body = {"moves": [{"reference": rr["reference"], "party_size": 1}]}
    m1 = call("POST", "/reservation-moves", a, key6, mv_body)
    expect(m1, 201, name="moves with fresh key 201")
    r = call("POST", "/reservations", a, key6, mv_body)
    check(r.status not in (200, 409) and r.status == 422, "same key+body on /reservations after moves -> normal 422, not replay/reuse",
          repr(r))
    key7 = k()
    res_body = dict(body, table_id="m_3", starts_at_local=f"{FUT}T17:00")
    expect(call("POST", "/reservations", a, key7, res_body), 201, name="reservation with key7")
    r = call("POST", "/reservation-moves", a, key7, res_body)
    check(r.status == 422, "same key+body on /reservation-moves after reservations -> normal 422", repr(r))
    r = call("POST", "/reservation-moves", a, key7, {"moves": [{"reference": rr["reference"], "party_size": 2}]})
    check(r.status == 201, "same key different path valid moves body -> 201 (not reuse)", repr(r))


@probe
def concurrency_same_slot():
    reset()
    toks = []
    for i in range(10):
        r = call("POST", "/auth/signup", json_body={"email": f"c{i}@example.com", "password": "password1",
                                                    "display_name": f"C{i}"})
        toks.append(r.body["token"])
    body = {"restaurant_id": "r_main", "table_id": "m_4", "starts_at_local": f"{FUT}T19:00", "party_size": 2}
    N = 40

    def go(i):
        return call("POST", "/reservations", toks[i % 10], k(), body)
    with cf.ThreadPoolExecutor(N) as ex:
        rs = list(ex.map(go, range(N)))
    codes = sorted((r.status, r.code) for r in rs)
    n201 = sum(1 for r in rs if r.status == 201)
    n409 = sum(1 for r in rs if r.status == 409 and r.code == "table_unavailable")
    check(n201 == 1 and n409 == N - 1, f"{N} parallel same-slot POSTs -> 1x201, {N-1}x409",
          str(codes[:5]) + f" n201={n201} n409={n409}")
    # overlapping (not identical) starts concurrently
    body2 = dict(body, table_id="m_3")
    starts = [f"{FUT}T{h}" for h in ("17:30", "18:00", "18:30", "19:00", "19:30", "20:00")] * 6

    def go2(i):
        return call("POST", "/reservations", toks[i % 10], k(), dict(body2, starts_at_local=starts[i]))
    with cf.ThreadPoolExecutor(len(starts)) as ex:
        rs = list(ex.map(go2, range(len(starts))))
    check(all(r.status in (201, 409) for r in rs), "overlapping parallel POSTs only 201/409",
          str([r.status for r in rs]))
    bad = overlaps_ok(all_reservations(*toks))
    check(not bad, "no overlapping confirmed bookings after parallel overlapping POSTs", str(bad))
    n = sum(1 for r in rs if r.status == 201)
    check(1 <= n <= 2, "at most 2 non-overlapping bookings fit 17:30..21:30 window", f"n201={n}")


@probe
def concurrency_misc():
    reset()
    body = {"email": "race@example.com", "password": "password1", "display_name": "Race"}
    with cf.ThreadPoolExecutor(20) as ex:
        rs = list(ex.map(lambda _: call("POST", "/auth/signup", json_body=body), range(20)))
    check(sum(r.status == 201 for r in rs) == 1 and sum(r.code == "email_taken" for r in rs) == 19,
          "20 parallel signups same email -> 1x201 + 19x409 email_taken", str(sorted(r.status for r in rs)))
    a, b, c = tokens()
    # cancel racing with bookers for the freed slot
    x = must_book(a, "m_2", f"{FUT}T19:00")

    def go(i):
        if i == 0:
            return call("POST", f"/reservations/{x['reference']}/cancel", a)
        return call("POST", "/reservations", b, k(), {"restaurant_id": "r_main", "table_id": "m_2",
                                                     "starts_at_local": f"{FUT}T19:00", "party_size": 2})
    with cf.ThreadPoolExecutor(25) as ex:
        rs = list(ex.map(go, range(25)))
    check(rs[0].status == 200 and all(r.status in (201, 409) for r in rs[1:])
          and sum(r.status == 201 for r in rs[1:]) <= 1, "cancel vs parallel bookers: at most one rebooking",
          str([r.status for r in rs]))
    check(not overlaps_ok(all_reservations(a, b)), "no overlap after cancel race")
    # exports taken during writes are internally consistent and importable
    exports = []

    def writer(i):
        return call("POST", "/reservations", c, k(), {"restaurant_id": "r_main", "table_id": "m_4",
                                                     "starts_at_local": f"{FUT_WINTER}T{12 + (i % 5) * 2:02d}:00",
                                                     "party_size": 2})

    def exporter(_):
        r = call("GET", "/_test/export")
        exports.append(r)
        return r
    with cf.ThreadPoolExecutor(20) as ex:
        list(ex.map(lambda i: writer(i) if i % 2 else exporter(i), range(20)))
    check(all(r.status == 200 for r in exports), "exports under concurrent writes 200")
    if exports:
        r = call("POST", "/_test/import", json_body=exports[-1].body)
        expect(r, 204, name="export taken under load is importable")
        check(not overlaps_ok(all_reservations(a, b, c)), "imported mid-load snapshot has no overlaps")


@probe
def concurrency_idempotent():
    reset()
    a, b, c = tokens()
    body = {"restaurant_id": "r_main", "table_id": "m_2", "starts_at_local": f"{FUT}T19:00", "party_size": 2}
    key = k()
    N = 30
    with cf.ThreadPoolExecutor(N) as ex:
        rs = list(ex.map(lambda _: call("POST", "/reservations", a, key, body), range(N)))
    n201 = [r for r in rs if r.status == 201]
    n200 = [r for r in rs if r.status == 200]
    check(len(n201) == 1 and len(n200) == N - 1, f"{N} parallel identical keyed POSTs -> 1x201 + {N-1}x200",
          str(sorted(r.status for r in rs)))
    if n201:
        check(all(r.body == n201[0].body for r in n200), "all 200 bodies identical to the 201 body")
    check(len(all_reservations(a)) == 1, "exactly one reservation created", str(len(all_reservations(a))))
    # parallel identical moves
    x = must_book(a, "m_1", f"{FUT}T13:00")
    mkey = k()
    mbody = {"moves": [{"reference": x["reference"], "starts_at_local": f"{FUT}T14:00"}]}
    with cf.ThreadPoolExecutor(20) as ex:
        rs = list(ex.map(lambda _: call("POST", "/reservation-moves", a, mkey, mbody), range(20)))
    s201 = [r for r in rs if r.status == 201]
    check(len(s201) == 1 and sum(r.status == 200 for r in rs) == 19,
          "20 parallel identical moves -> 1x201 + 19x200", str(sorted(r.status for r in rs)))
    if s201:
        check(all(r.body == s201[0].body for r in rs if r.status == 200), "moves replay bodies identical")


@probe
def concurrency_patch_and_moves():
    reset()
    a, b, c = tokens()
    # 12 bookings on different tables/times all PATCHed to the same table+slot
    refs = []
    tabs = ["m_1", "m_2", "m_3"]
    times = ["12:00", "13:30", "15:00", "16:30"]
    for t in tabs:
        for tm in times:
            refs.append((a, must_book(a, t, f"{FUT}T{tm}")["reference"]))
    target = {"table_id": "m_4", "starts_at_local": f"{FUT}T20:00"}

    def go(item):
        tok, ref = item
        return call("PATCH", f"/reservations/{ref}", tok, json_body=target)
    with cf.ThreadPoolExecutor(len(refs)) as ex:
        rs = list(ex.map(go, refs))
    n200 = sum(r.status == 200 for r in rs)
    n409 = sum(r.status == 409 and r.code == "table_unavailable" for r in rs)
    check(n200 == 1 and n409 == len(refs) - 1, "parallel PATCH to same slot -> 1x200 rest 409",
          f"n200={n200} n409={n409} {[r.status for r in rs]}")
    check(not overlaps_ok(all_reservations(a, b, c)), "no overlap after parallel PATCHes")
    # failed patches left originals intact
    allr = {x["reference"]: x for x in all_reservations(a)}
    unchanged = sum(1 for r, (tok, ref) in zip(rs, refs) if r.status == 409 and allr[ref]["table_id"] in tabs)
    check(unchanged == n409, "failed parallel patches left originals intact", f"{unchanged} vs {n409}")
    # parallel conflicting moves: many batches try to move different bookings into one slot
    reset()
    a, b, c = tokens()
    bks = [must_book(a, "m_1", f"{FUT}T{tm}") for tm in ("12:00", "13:30", "15:00", "16:30", "18:00", "19:30")]
    bks += [must_book(b, "m_2", f"{FUT}T{tm}") for tm in ("12:00", "13:30", "15:00", "16:30", "18:00", "19:30")]

    def mv(i):
        x = bks[i]
        tok = a if i < 6 else b
        return call("POST", "/reservation-moves", tok, k(),
                    {"moves": [{"reference": x["reference"], "table_id": "m_3", "starts_at_local": f"{FUT}T20:00"}]})
    with cf.ThreadPoolExecutor(len(bks)) as ex:
        rs = list(ex.map(mv, range(len(bks))))
    n201 = sum(r.status == 201 for r in rs)
    check(n201 == 1 and all(r.status in (201, 409) for r in rs), "parallel competing moves -> exactly one 201",
          str([r.status for r in rs]))
    check(not overlaps_ok(all_reservations(a, b, c)), "no overlap after parallel moves")
    # parallel swaps mixed with POSTs into the same table
    reset()
    a, b, c = tokens()
    x1 = must_book(a, "m_1", f"{FUT}T19:00")
    x2 = must_book(a, "m_2", f"{FUT}T19:00")

    def mixed(i):
        if i % 2 == 0:
            return call("POST", "/reservation-moves", a, k(), {"moves": [
                {"reference": x1["reference"], "table_id": "m_2"},
                {"reference": x2["reference"], "table_id": "m_1"}]})
        return call("POST", "/reservations", b, k(), {"restaurant_id": "r_main", "table_id": ["m_1", "m_2"][i % 4 // 2],
                                                      "starts_at_local": f"{FUT}T19:30", "party_size": 2})
    with cf.ThreadPoolExecutor(30) as ex:
        rs = list(ex.map(mixed, range(30)))
    check(all(r.status in (201, 409) for r in rs), "mixed swap/POST load only 201/409",
          str(sorted({(r.status, r.code) for r in rs})))
    check(not overlaps_ok(all_reservations(a, b)), "no overlap after mixed swaps/POSTs",
          str(overlaps_ok(all_reservations(a, b))))


@probe
def state_cancel_patch():
    reset()
    a, b, c = tokens()
    x = must_book(a, "m_2", f"{FUT}T19:00", party=4)
    ref = x["reference"]
    check("m_2" not in slot(avail("r_main", FUT, 4), f"{FUT}T19:00")["available_table_ids"], "booked table hidden")
    expect(get_res(b, ref), 404, "not_found", "other user GET 404")
    expect(call("POST", f"/reservations/{ref}/cancel", b), 404, "not_found", "other user cancel 404")
    expect(call("PATCH", f"/reservations/{ref}", b, json_body={"party_size": 1}), 404, "not_found",
           "other user PATCH 404")
    expect(get_res(a, "NOSUCH1"), 404, "not_found", "unknown reference 404")
    check(get_res(a, ref).body["party_size"] == 4, "other user's attempts changed nothing")
    # PATCH happy path
    p = call("PATCH", f"/reservations/{ref}", a, json_body={"starts_at_local": f"{FUT}T19:30"})
    if expect(p, 200, name="PATCH shift 30 min on same table (self-overlap ok) 200"):
        check(p.body["reference"] == ref and p.body["reservation_id"] == x["reservation_id"],
              "reference/reservation_id survive PATCH", repr(p))
        check(p.body["starts_at"] == f"{FUT}T19:30:00+02:00" and p.body["ends_at"] == f"{FUT}T21:00:00+02:00",
              "PATCH recomputes starts_at/ends_at", repr(p))
        check(p.body["created_at"] == x["created_at"], "created_at unchanged by PATCH", repr(p))
        check(p.body["party_size"] == 4 and p.body["table_id"] == "m_2", "omitted fields retained", repr(p))
    av = avail("r_main", FUT, 4)
    check("m_2" in slot(av, f"{FUT}T17:30")["available_table_ids"], "old slot released after PATCH (17:30 free)")
    check("m_2" not in slot(av, f"{FUT}T20:30")["available_table_ids"], "new slot occupied after PATCH")
    p = call("PATCH", f"/reservations/{ref}", a, json_body={"table_id": "m_3", "party_size": 3})
    expect(p, 200, name="PATCH table+party 200")
    check("m_2" in slot(avail("r_main", FUT, 4), f"{FUT}T19:30")["available_table_ids"], "old table freed")
    # failure leaves original intact
    y = must_book(b, "m_4", f"{FUT}T19:30", party=2)
    before = get_res(a, ref).body
    av_before = avail("r_main", FUT, 1)
    for patch, st, code, label in [
        ({"table_id": "m_4"}, 409, "table_unavailable", "PATCH into taken table 409"),
        ({"starts_at_local": f"{FUT}T19:45"}, 422, "not_on_slot_grid", "PATCH off-grid 422"),
        ({"starts_at_local": f"{FUT}T22:00"}, 422, "outside_opening_hours", "PATCH ends after close 422"),
        ({"party_size": 5}, 422, "party_exceeds_capacity", "PATCH party > capacity 422"),
        ({"table_id": "m_1"}, 422, "party_exceeds_capacity", "PATCH to smaller table 422"),
        ({"party_size": 0}, 422, "validation_failed", "PATCH party 0 422"),
        ({"party_size": "3"}, 422, "validation_failed", "PATCH party string 422"),
        ({"table_id": "nope"}, 404, "not_found", "PATCH unknown table 404"),
        ({"table_id": "o_1"}, 404, "not_found", "PATCH other restaurant's table 404"),
        ({"starts_at_local": f"{FUT}T19:30:00"}, 422, "validation_failed", "PATCH bad local format 422"),
        ({"starts_at_local": "2027-03-28T02:30"}, 422, "invalid_local_time", "PATCH into DST gap 422"),
        ({"table_id": 4}, 400, "malformed_request", "PATCH table_id wrong type 400"),
    ]:
        r = call("PATCH", f"/reservations/{ref}", a, json_body=patch)
        hard = label != "PATCH into DST gap 422"  # r_main opens 12:00 so 02:30 might be outside hours first
        if label == "PATCH into DST gap 422":
            soft(r.status == 422 and r.code in ("invalid_local_time", "outside_opening_hours"),
                 "PATCH into DST gap (closed hour) 422 invalid_local_time/outside_opening_hours", repr(r))
        else:
            expect(r, st, code, label)
        now = get_res(a, ref).body
        check(now == before, f"{label}: original unchanged", f"{before} -> {now}")
    check(avail("r_main", FUT, 1) == av_before, "failed patches left availability unchanged")
    expect(call("PATCH", f"/reservations/{ref}", a, raw="{oops"), 400, "malformed_request", "PATCH bad JSON 400")
    r = call("PATCH", f"/reservations/{ref}", a, json_body={})
    soft(r.status == 200 and r.body == before, "PATCH empty body = no-op 200", repr(r))
    r = call("PATCH", f"/reservations/{ref}", a, json_body={"unknown": 1})
    soft(r.status == 200, "PATCH unknown field ignored", repr(r))
    # cancel
    c1 = call("POST", f"/reservations/{ref}/cancel", a)
    if expect(c1, 200, name="cancel 200"):
        check(c1.body.get("status") == "cancelled" and c1.body.get("reference") == ref
              and c1.body.get("reservation_id") == x["reservation_id"], "cancel body", repr(c1))
    check("m_3" in slot(avail("r_main", FUT, 3), f"{FUT}T19:30")["available_table_ids"],
          "cancel frees slot immediately")
    c2 = call("POST", f"/reservations/{ref}/cancel", a)
    check(c2.status == 200 and c2.body.get("status") == "cancelled", "double cancel 200 cancelled", repr(c2))
    expect(call("PATCH", f"/reservations/{ref}", a, json_body={"party_size": 2}), 409, "reservation_cancelled",
           "PATCH cancelled 409 reservation_cancelled")
    expect(book(b, "m_3", f"{FUT}T19:30"), 201, name="freed slot bookable by another user")
    g = get_res(a, ref)
    check(g.status == 200 and g.body["status"] == "cancelled", "cancelled booking still readable", repr(g))
    # listing order
    reset()
    a, b, c = tokens()
    r1 = must_book(a, "m_1", f"{FUT}T13:00")
    r2 = must_book(a, "m_1", f"{PAST}T13:00")
    r3 = must_book(a, "m_1", f"{FUT}T20:00")
    r4 = must_book(a, "m_2", "2028-01-05T12:00")
    must_book(b, "m_3", f"{FUT}T13:00")
    call("POST", f"/reservations/{r1['reference']}/cancel", a)
    lst = call("GET", "/reservations", a)
    refs = [x["reference"] for x in lst.body["reservations"]]
    check(refs == [r4["reference"], r3["reference"], r1["reference"], r2["reference"]],
          "GET /reservations: own only, starts_at desc, includes cancelled", str(refs))
    check(lst.body["reservations"][2]["status"] == "cancelled", "cancelled entry has status cancelled")
    keys = set(r1.keys())
    check(all(set(x.keys()) >= keys for x in lst.body["reservations"]), "list entries have create shape")
    expect(call("GET", "/reservations", c), 200, name="empty list 200")
    check(call("GET", "/reservations", c).body == {"reservations": []}, "empty list body")
    # cutoff on past booking
    expect(call("POST", f"/reservations/{r2['reference']}/cancel", a), 409, "cutoff_passed", "cancel past booking 409")
    expect(call("PATCH", f"/reservations/{r2['reference']}", a, json_body={"starts_at_local": f"{FUT}T15:00"}),
           409, "cutoff_passed", "PATCH past booking to future 409 cutoff (measured on current start)")
    expect(call("PATCH", f"/reservations/{r2['reference']}", a, json_body={"party_size": 99}),
           409, "cutoff_passed", "cutoff precedes validation on PATCH", hard=False)


@probe
def cutoff_boundary():
    reset()
    a, b, c = tokens()
    now = dt.datetime.now(dt.timezone.utc)
    # r_utc: slot 1 min, duration 30, cutoff 60, open 00:00-23:59
    def at(minutes):
        t = (now + dt.timedelta(minutes=minutes)).replace(second=0, microsecond=0)
        return t
    far, near, last = at(63), at(57), at(150)
    if last.date() != now.date() or last.hour * 60 + last.minute + 30 > 23 * 60 + 59:
        record("WARN", "cutoff boundary skipped (too close to UTC midnight)")
        return
    f = must_book(a, "u_t1", far.strftime("%Y-%m-%dT%H:%M"), rest="r_utc")
    n = must_book(a, "u_t2", near.strftime("%Y-%m-%dT%H:%M"), rest="r_utc")
    expect(call("PATCH", f"/reservations/{n['reference']}", a, json_body={"party_size": 1}), 409, "cutoff_passed",
           "PATCH 57 min before start (cutoff 60) 409")
    expect(call("POST", f"/reservations/{n['reference']}/cancel", a), 409, "cutoff_passed",
           "cancel 57 min before start 409")
    expect(call("PATCH", f"/reservations/{f['reference']}", a, json_body={"party_size": 1}), 200,
           name="PATCH 63 min before start 200")
    # moving a far booking to a near time: cutoff measured against current start -> allowed
    r = call("PATCH", f"/reservations/{f['reference']}", a, json_body={"starts_at_local": at(40).strftime("%Y-%m-%dT%H:%M")})
    soft(r.status == 200, "PATCH far booking into near time allowed (cutoff on current start)", repr(r))
    f2 = must_book(a, "u_t2", at(150).strftime("%Y-%m-%dT%H:%M"), rest="r_utc")
    expect(call("POST", f"/reservations/{f2['reference']}/cancel", a), 200, name="cancel 150 min before start 200")
    # zero cutoff restaurant: future booking cancellable
    g = must_book(a, "g_1", f"{FUT}T17:45", rest="r_grid")
    expect(call("POST", f"/reservations/{g['reference']}/cancel", a), 200, name="cutoff 0 cancel future 200")


@probe
def dst():
    reset()
    a, b, c = tokens()
    # Berlin spring forward 2026-03-29
    av = avail("r_ber_dst", "2026-03-29", 2)
    locs = [s["starts_at_local"][11:] for s in av["slots"]]
    check("02:00" not in locs and "02:30" not in locs, "Berlin spring: 02:00/02:30 absent", str(locs))
    check(len(locs) == len(set(locs)), "Berlin spring: no duplicate slots", str(locs))
    s = slot(av, "2026-03-29T01:30")
    check(s and s["starts_at"] == "2026-03-29T01:30:00+01:00", "Berlin 01:30 +01:00", repr(s))
    s = slot(av, "2026-03-29T03:00")
    check(s and s["starts_at"] == "2026-03-29T03:00:00+02:00", "Berlin 03:00 +02:00", repr(s))
    check(locs and locs[0] == "00:00" and locs[-1] == "04:30", "Berlin spring first 00:00 last 04:30", str(locs))
    expect(book(a, "b_1", "2026-03-29T02:30", rest="r_ber_dst"), 422, "invalid_local_time", "Berlin gap 02:30 422")
    expect(book(a, "b_1", "2026-03-29T02:00", rest="r_ber_dst"), 422, "invalid_local_time", "Berlin gap 02:00 422")
    r = book(a, "b_1", "2026-03-29T01:30", rest="r_ber_dst")
    if expect(r, 201, name="Berlin 01:30 bookable on spring day"):
        check(r.body["ends_at"] == "2026-03-29T04:00:00+02:00", "spring 01:30 +90 abs -> 04:00+02:00",
              repr(r.body["ends_at"]))
        av = avail("r_ber_dst", "2026-03-29", 2)
        check("b_1" not in slot(av, "2026-03-29T03:30")["available_table_ids"], "03:30 overlaps (ends 04:00)")
        check("b_1" in slot(av, "2026-03-29T04:00")["available_table_ids"], "04:00 free")
    # Berlin fall back 2026-10-25
    av = avail("r_ber_dst", "2026-10-25", 2)
    locs = [s["starts_at_local"][11:] for s in av["slots"]]
    check(locs.count("02:00") == 1 and locs.count("02:30") == 1, "Berlin fall: 02:00/02:30 once each", str(locs))
    s = slot(av, "2026-10-25T02:30")
    check(s and s["starts_at"] == "2026-10-25T02:30:00+02:00", "Berlin fall 02:30 first occurrence +02:00", repr(s))
    s = slot(av, "2026-10-25T03:00")
    check(s and s["starts_at"] == "2026-10-25T03:00:00+01:00", "Berlin fall 03:00 +01:00", repr(s))
    r = book(a, "b_1", "2026-10-25T01:30", rest="r_ber_dst")
    if expect(r, 201, name="Berlin fall 01:30 bookable"):
        check(r.body["starts_at"] == "2026-10-25T01:30:00+02:00", "Berlin fall 01:30 +02:00", repr(r.body))
        check(r.body["ends_at"] == "2026-10-25T02:00:00+01:00", "Berlin fall 01:30+90 abs -> 02:00+01:00",
              repr(r.body["ends_at"]))
    r = book(a, "b_2", "2026-10-25T02:30", rest="r_ber_dst")
    if expect(r, 201, name="Berlin fall 02:30 bookable"):
        check(r.body["starts_at"] == "2026-10-25T02:30:00+02:00", "Berlin fall 02:30 resolves to first (+02:00)",
              repr(r.body))
        check(r.body["ends_at"] == "2026-10-25T03:00:00+01:00", "Berlin fall 02:30+90 -> 03:00+01:00",
              repr(r.body["ends_at"]))
    av = avail("r_ber_dst", "2026-10-25", 2)
    # b_1 booked 01:30(+2) .. 02:00(+1) == 23:30Z..01:00Z; 02:00(+2)=00:00Z overlaps; 02:30(+2)=00:30Z overlaps;
    # 03:00(+1)=02:00Z free
    check("b_1" not in slot(av, "2026-10-25T02:30")["available_table_ids"], "b_1 02:30(+02) overlaps 01:30 booking")
    check("b_1" in slot(av, "2026-10-25T03:00")["available_table_ids"], "b_1 03:00(+01) free (absolute duration)",
          repr(slot(av, "2026-10-25T03:00")))
    # b_2 booked 00:30Z..02:00Z. 01:00(+2)=23:00Z..00:30Z adjacent -> free. 03:00(+1)=02:00Z free
    check("b_2" in slot(av, "2026-10-25T01:00")["available_table_ids"], "b_2 01:00 free (ends exactly at 02:30 start)",
          repr(slot(av, "2026-10-25T01:00")))
    check("b_2" in slot(av, "2026-10-25T03:00")["available_table_ids"], "b_2 03:00(+01) free")
    check(slot(av, "2026-10-25T04:30") is not None, "fall back last slot 04:30 present", str([s["starts_at_local"] for s in av["slots"]]))
    # New York spring 2026-03-08
    av = avail("r_ny_dst", "2026-03-08", 2)
    locs = [s["starts_at_local"][11:] for s in av["slots"]]
    check("02:00" not in locs and "02:30" not in locs, "NY spring: 02:00/02:30 absent", str(locs))
    check(slot(av, "2026-03-08T01:30")["starts_at"] == "2026-03-08T01:30:00-05:00", "NY 01:30 -05:00")
    check(slot(av, "2026-03-08T03:00")["starts_at"] == "2026-03-08T03:00:00-04:00", "NY 03:00 -04:00")
    expect(book(a, "n_1", "2026-03-08T02:30", rest="r_ny_dst"), 422, "invalid_local_time", "NY gap 02:30 422")
    expect(book(a, "n_1", "2026-03-08T02:00", rest="r_ny_dst"), 422, "invalid_local_time", "NY gap 02:00 422")
    # New York fall back 2026-11-01 (01:00-02:00 repeated)
    av = avail("r_ny_dst", "2026-11-01", 2)
    locs = [s["starts_at_local"][11:] for s in av["slots"]]
    check(locs.count("01:00") == 1 and locs.count("01:30") == 1, "NY fall: 01:00/01:30 once", str(locs))
    check(slot(av, "2026-11-01T01:30")["starts_at"] == "2026-11-01T01:30:00-04:00", "NY fall 01:30 first occurrence -04:00",
          repr(slot(av, "2026-11-01T01:30")))
    check(slot(av, "2026-11-01T02:00")["starts_at"] == "2026-11-01T02:00:00-05:00", "NY 02:00 -05:00")
    r = book(a, "n_1", "2026-11-01T01:30", rest="r_ny_dst")
    if expect(r, 201, name="NY fall 01:30 bookable"):
        check(r.body["starts_at"] == "2026-11-01T01:30:00-04:00", "NY 01:30 -> -04:00", repr(r.body))
        check(r.body["ends_at"] == "2026-11-01T02:00:00-05:00", "NY 01:30+90 abs -> 02:00-05:00 (spec example)",
              repr(r.body["ends_at"]))
    av = avail("r_ny_dst", "2026-11-01", 2)
    check("n_1" in slot(av, "2026-11-01T02:00")["available_table_ids"], "NY 02:00(-05) free right after",
          repr(slot(av, "2026-11-01T02:00")))
    check("n_1" not in slot(av, "2026-11-01T01:00")["available_table_ids"], "NY 01:00(-04) overlaps")
    # Ordinary offsets
    check(slot(avail("r_ny_dst", "2026-07-01", 1), "2026-07-01T01:00")["starts_at"] == "2026-07-01T01:00:00-04:00",
          "NY summer -04:00")
    check(slot(avail("r_ny_dst", "2026-12-01", 1), "2026-12-01T01:00")["starts_at"] == "2026-12-01T01:00:00-05:00",
          "NY winter -05:00")
    bad = [x for x in all_reservations(a)
           if not (RFC3339_STRICT_OFF.match(x["starts_at"]) and RFC3339_STRICT_OFF.match(x["ends_at"]))]
    check(not bad, "DST reservations carry explicit numeric offsets", repr(bad))
    utc_av = avail("r_utc", FUT, 1)
    st = utc_av["slots"][0]["starts_at"]
    soft(st.endswith("+00:00"), "UTC restaurant offset rendered +00:00 (Z also RFC3339)", st)


def make_moves_setup():
    reset()
    a, b, c = tokens()
    B1 = must_book(a, "m_1", f"{FUT}T19:00")          # cap 2
    B2 = must_book(a, "m_2", f"{FUT}T19:00")          # cap 4
    B3 = must_book(b, "m_3", f"{FUT}T19:00")          # other user, unlisted
    B4 = must_book(a, "m_1", f"{FUT}T13:00")
    return a, b, c, B1, B2, B3, B4


def mv(tok, moves, key=None):
    return call("POST", "/reservation-moves", tok, key or k(), {"moves": moves})


@probe
def moves_rules():
    a, b, c, B1, B2, B3, B4 = make_moves_setup()
    key = k()
    r = mv(a, [{"reference": B1["reference"], "table_id": "m_2"}, {"reference": B2["reference"], "table_id": "m_1"}], key)
    if expect(r, 201, name="swap tables 201"):
        out = r.body["reservations"]
        check([x["reference"] for x in out] == [B1["reference"], B2["reference"]], "response in input order")
        check(out[0]["table_id"] == "m_2" and out[1]["table_id"] == "m_1", "tables swapped", repr(out))
        check(out[0]["reservation_id"] == B1["reservation_id"] and out[0]["created_at"] == B1["created_at"],
              "identity and created_at preserved", repr(out[0]))
        g = get_res(a, B1["reference"]).body
        check(g == out[0], "GET reflects move", f"{g} vs {out[0]}")
    # replay after later change
    call("POST", f"/reservations/{B1['reference']}/cancel", a)
    r2 = mv(a, [{"reference": B1["reference"], "table_id": "m_2"}, {"reference": B2["reference"], "table_id": "m_1"}], key)
    check(r2.status == 200 and r2.body == r.body, "moves replay after cancel -> 200 original", repr(r2))
    check(get_res(a, B1["reference"]).body["status"] == "cancelled", "moves replay changed nothing")
    expect(mv(a, [{"reference": B1["reference"], "table_id": "m_1"}], key), 409, "idempotency_key_reuse",
           "moves key reuse different body 409")
    expect(mv(a, [{"reference": B1["reference"], "party_size": "bad"}], key), 409, "idempotency_key_reuse",
           "moves key reuse with invalid body 409")
    expect(call("POST", "/reservation-moves", a, key, {"moves": "nope"}), 409, "idempotency_key_reuse",
           "moves key reuse with invalid shape 409")
    expect(mv(a, [{"reference": B1["reference"], "party_size": 1}]), 409, "reservation_cancelled",
           "move cancelled booking 409 reservation_cancelled")

    # overlap with unlisted booking -> nothing changes, key reusable
    a, b, c, B1, B2, B3, B4 = make_moves_setup()
    snap = {x["reference"]: x for x in all_reservations(a, b)}
    av0 = avail("r_main", FUT, 1)
    key = k()
    body_bad = [{"reference": B2["reference"], "starts_at_local": f"{FUT}T15:00"},
                {"reference": B1["reference"], "table_id": "m_3"}]
    expect(mv(a, body_bad, key), 409, "table_unavailable", "overlap with unlisted booking 409")
    check({x["reference"]: x for x in all_reservations(a, b)} == snap, "failed batch: records unchanged")
    check(avail("r_main", FUT, 1) == av0, "failed batch: occupancy unchanged")
    expect(mv(a, [{"reference": B2["reference"], "starts_at_local": f"{FUT}T15:00"}], key), 201,
           name="failed batch key reusable with different body")
    a, b, c, B1, B2, B3, B4 = make_moves_setup()
    key = k()
    body_bad = [{"reference": B2["reference"], "starts_at_local": f"{FUT}T15:00"},
                {"reference": B1["reference"], "table_id": "m_3"}]
    expect(mv(a, body_bad, key), 409, "table_unavailable", "conflict batch (again)")
    call("POST", f"/reservations/{B3['reference']}/cancel", b)
    r = mv(a, body_bad, key)
    expect(r, 201, name="same key+same body after 409 -> fresh evaluation 201")
    # overlap among resulting bookings
    a, b, c, B1, B2, B3, B4 = make_moves_setup()
    expect(mv(a, [{"reference": B1["reference"], "table_id": "m_4"},
                  {"reference": B2["reference"], "table_id": "m_4"}]), 409, "table_unavailable",
           "two listed moved onto same table/time 409")
    # unchanged listed booking retains occupancy
    expect(mv(a, [{"reference": B1["reference"], "table_id": "m_2"}, {"reference": B2["reference"]}]), 409,
           "table_unavailable", "unchanged listed booking keeps occupancy -> 409")
    # swap times on same table
    a, b, c, B1, B2, B3, B4 = make_moves_setup()
    r = mv(a, [{"reference": B1["reference"], "starts_at_local": f"{FUT}T13:00"},
               {"reference": B4["reference"], "starts_at_local": f"{FUT}T19:00"}])
    if expect(r, 201, name="swap start times on one table 201"):
        check(r.body["reservations"][0]["starts_at_local"] == f"{FUT}T13:00"
              and r.body["reservations"][1]["starts_at_local"] == f"{FUT}T19:00", "times swapped")
    # chain move: B1 into B4's slot while B4 moves elsewhere
    a, b, c, B1, B2, B3, B4 = make_moves_setup()
    r = mv(a, [{"reference": B1["reference"], "starts_at_local": f"{FUT}T13:30"},
               {"reference": B4["reference"], "starts_at_local": f"{FUT}T16:00"}])
    expect(r, 201, name="move into slot vacated by another listed booking 201")
    check(not overlaps_ok(all_reservations(a, b)), "no overlap after chain move")
    # no-op moves
    a, b, c, B1, B2, B3, B4 = make_moves_setup()
    r = mv(a, [{"reference": B1["reference"]}, {"reference": B2["reference"], "zzz": 1}])
    if expect(r, 201, name="no-op moves 201"):
        check(r.body["reservations"][0] == get_res(a, B1["reference"]).body == B1, "no-op retains all values",
              f"{r.body['reservations'][0]} vs {B1}")
        check(r.body["reservations"][1] == B2, "no-op (unknown field ignored) retains values")
    r = mv(a, [{"reference": B2["reference"], "party_size": 3}, {"reference": B1["reference"]}])
    if expect(r, 201, name="mixed changed + unchanged items 201"):
        check(len(r.body["reservations"]) == 2 and r.body["reservations"][1]["reference"] == B1["reference"],
              "unchanged items included in input order")
    # shape validation
    a, b, c, B1, B2, B3, B4 = make_moves_setup()
    expect(call("POST", "/reservation-moves", a, k(), {"moves": []}), 422, "validation_failed", "0 moves 422")
    many = [{"reference": f"REF{i:03d}"} for i in range(9)]
    expect(call("POST", "/reservation-moves", a, k(), {"moves": many}), 422, "validation_failed", "9 moves 422")
    expect(mv(a, [{"reference": B1["reference"]}, {"reference": B1["reference"], "party_size": 1}]), 422,
           "validation_failed", "duplicate references 422")
    expect(call("POST", "/reservation-moves", a, k(), {}), 422, "validation_failed", "missing moves 422")
    expect(call("POST", "/reservation-moves", a, k(), {"moves": [{"table_id": "m_1"}]}), 422, "validation_failed",
           "item without reference 422")
    expect(call("POST", "/reservation-moves", a, k(), {"moves": [{"reference": 5}]}), 422, "validation_failed",
           "non-string reference 422 (invalid shape)", hard=False)
    expect(call("POST", "/reservation-moves", a, k(), {"moves": "x"}), 422, "validation_failed",
           "moves not a list 422 (invalid shape)", hard=False)
    expect(call("POST", "/reservation-moves", a, k(), {"moves": ["x"]}), 422, "validation_failed",
           "move item not an object 422", hard=False)
    expect(call("POST", "/reservation-moves", a, k(), raw="{nope"), 400, "malformed_request", "moves bad JSON 400")
    expect(call("POST", "/reservation-moves", a, None, {"moves": [{"reference": B1["reference"]}]}), 400,
           "missing_idempotency_key", "moves without key 400")
    expect(call("POST", "/reservation-moves", a, "", {"moves": [{"reference": B1["reference"]}]}), 400,
           "missing_idempotency_key", "moves empty key 400")
    expect(call("POST", "/reservation-moves", a, "q" * 256, {"moves": [{"reference": B1["reference"]}]}), 422,
           "validation_failed", "moves 256-char key 422")
    eight = [must_book(c, "m_4", f"{FUT_WINTER}T{h:02d}:00") for h in (12, 14, 16, 18, 20)]
    eight += [must_book(c, "m_3", f"{FUT_WINTER}T{h:02d}:00") for h in (12, 14, 16)]
    expect(mv(c, [{"reference": x["reference"]} for x in eight]), 201, name="8 moves accepted")
    # ownership / restaurants
    expect(mv(a, [{"reference": B1["reference"]}, {"reference": B3["reference"]}]), 404, "not_found",
           "other owner's reference 404")
    expect(mv(a, [{"reference": "NOSUCH9"}]), 404, "not_found", "unknown reference 404")
    O = must_book(a, "o_1", f"{FUT}T19:00", rest="r_other")
    expect(mv(a, [{"reference": B1["reference"]}, {"reference": O["reference"]}]), 422, "validation_failed",
           "cross-restaurant 422")
    expect(mv(a, [{"reference": B1["reference"], "table_id": "o_1"}]), 404, "not_found",
           "move to other restaurant's table 404")
    # error precedence
    P = must_book(a, "m_3", f"{PAST}T19:00")
    e1 = mv(a, [{"reference": B1["reference"], "starts_at_local": f"{FUT}T19:15"},
                {"reference": B2["reference"], "party_size": 99}])
    expect(e1, 422, "not_on_slot_grid", "precedence: first item error wins (grid)")
    e2 = mv(a, [{"reference": B2["reference"], "party_size": 99},
                {"reference": B1["reference"], "starts_at_local": f"{FUT}T19:15"}])
    expect(e2, 422, "party_exceeds_capacity", "precedence: first item error wins (capacity)")
    e3 = mv(a, [{"reference": B1["reference"], "table_id": "m_3"},   # occupancy conflict with B3
                {"reference": B2["reference"], "party_size": 99}])
    expect(e3, 422, "party_exceeds_capacity", "non-occupancy error beats earlier occupancy conflict")
    e4 = mv(a, [{"reference": P["reference"], "starts_at_local": f"{FUT}T19:15"}])
    expect(e4, 409, "cutoff_passed", "cutoff precedes other errors for same booking")
    e5 = mv(a, [{"reference": B1["reference"], "starts_at_local": f"{FUT}T19:15"},
                {"reference": P["reference"], "party_size": 1}])
    expect(e5, 422, "not_on_slot_grid", "input order across items: grid (item 1) before cutoff (item 2)")
    e6 = mv(a, [{"reference": B1["reference"], "party_size": 1},
                {"reference": P["reference"], "party_size": 1}])
    expect(e6, 409, "cutoff_passed", "cutoff on item 2 when item 1 valid")
    # planner ruling: resolve all references (404), then single restaurant (422), then per-item checks
    expect(mv(a, [{"reference": B1["reference"], "starts_at_local": f"{FUT}T19:15"}, {"reference": B3["reference"]}]),
           404, "not_found", "ruling: other owner's ref (item 2) beats item 1 grid error")
    expect(mv(a, [{"reference": P["reference"], "party_size": 1}, {"reference": "NOSUCH9"}]),
           404, "not_found", "ruling: unknown ref (item 2) beats item 1 cutoff")
    expect(mv(a, [{"reference": B1["reference"], "starts_at_local": f"{FUT}T19:15"}, {"reference": O["reference"]}]),
           422, "validation_failed", "ruling: cross-restaurant beats item 1 grid error")
    expect(mv(a, [{"reference": O["reference"]}, {"reference": B1["reference"]}, {"reference": "NOSUCH9"}]),
           404, "not_found", "ruling: unknown ref beats cross-restaurant")
    expect(mv(a, [{"reference": B1["reference"], "table_id": "m_3"}, {"reference": P["reference"]}]),
           409, "cutoff_passed", "ruling: per-item cutoff beats occupancy conflict")
    e7 = mv(a, [{"reference": B1["reference"], "starts_at_local": f"{FUT}T22:00"}])
    expect(e7, 422, "outside_opening_hours", "move outside hours 422")
    e8 = mv(a, [{"reference": B1["reference"], "party_size": 0}])
    expect(e8, 422, "validation_failed", "move party 0 422")
    e9 = mv(a, [{"reference": B1["reference"], "table_id": "zz"}])
    expect(e9, 404, "not_found", "move to unknown table 404")
    e10 = mv(a, [{"reference": B1["reference"], "starts_at_local": f"{FUT}T19:00:00"}])
    expect(e10, 422, "validation_failed", "move bad local format 422")
    check(get_res(a, B1["reference"]).body == B1 and get_res(a, B2["reference"]).body == B2,
          "all failed batches left records unchanged")
    expect(call("POST", "/reservation-moves", None, k(), {"moves": [{"reference": B1["reference"]}]}), 401,
           "unauthenticated", "moves without token 401")


@probe
def export_import():
    reset()
    a, b, c = tokens()
    r = call("GET", "/_test/export")
    ok = expect(r, 200, name="export 200")
    if ok:
        check(r.body.get("track") == "tablekeeper" and r.body.get("format_version") == 1
              and isinstance(r.body.get("state"), dict), "export shape", repr(r)[:300])
    # build state
    s = call("POST", "/auth/signup", json_body={"email": "eve@example.com", "password": "password9",
                                                "display_name": "Eve"})
    eve = s.body["token"]
    key1, key2, keyf = k(), k(), k()
    body1 = {"restaurant_id": "r_main", "table_id": "m_2", "starts_at_local": f"{FUT}T19:00", "party_size": 2}
    o1 = call("POST", "/reservations", a, key1, body1)
    X = must_book(a, "m_1", f"{FUT}T13:00")
    Y = must_book(eve, "m_3", f"{FUT}T13:00")
    mbody = {"moves": [{"reference": X["reference"], "starts_at_local": f"{FUT}T14:00"}]}
    m1 = call("POST", "/reservation-moves", a, key2, mbody)
    expect(call("POST", "/reservations", a, keyf, dict(body1, party_size=0)), 422, "validation_failed", "failed key")
    Z = must_book(b, "m_4", f"{FUT}T20:00")
    call("POST", f"/reservations/{Z['reference']}/cancel", b)
    before_a = call("GET", "/reservations", a).body
    before_b = call("GET", "/reservations", b).body
    before_eve = call("GET", "/reservations", eve).body
    av_before = avail("r_main", FUT, 1)
    exp = call("GET", "/_test/export")
    expect(exp, 200, name="export after writes 200")
    snapshot = json.loads(exp.text)
    exp_text = exp.text
    # later write must not affect captured export
    LATE = must_book(a, "m_4", f"{FUT}T13:00")
    exp2 = call("GET", "/_test/export")
    check(exp2.text != exp_text, "later export differs after write (sanity)")
    # fresh destination: reset to a different fixture, add data that import must wipe
    fx = base_fixture()
    fx["users"] = [{"id": "u_zed", "email": "zed@example.com", "password": "zzzzzzzz", "display_name": "Zed"}]
    reset(fx)
    zed = login("zed@example.com", "zzzzzzzz")
    r = call("POST", "/_test/import", json_body=snapshot)
    expect(r, 204, name="import 204")
    expect(call("GET", "/reservations", zed), 401, "unauthenticated", "import removes previous tokens")
    expect(call("POST", "/auth/login", json_body={"email": "zed@example.com", "password": "zzzzzzzz"}), 401,
           "unauthenticated", "import removes previous accounts")
    check(call("GET", "/reservations", a).body == before_a, "old token works; Ada's reservations identical",
          repr(call("GET", "/reservations", a)))
    check(call("GET", "/reservations", b).body == before_b, "Bob's reservations identical (cancelled kept)")
    check(call("GET", "/reservations", eve).body == before_eve, "signup user's token + reservations preserved")
    check(avail("r_main", FUT, 1) == av_before, "occupancy identical after import (late write absent)")
    expect(get_res(a, LATE["reference"]), 404, "not_found", "write after export absent after import")
    r = call("POST", "/auth/login", json_body={"email": "eve@example.com", "password": "password9"})
    expect(r, 200, name="signup user's hashed-password login after import")
    expect(call("POST", "/auth/login", json_body={"email": "ada@example.com", "password": "correct horse"}), 200,
           name="seeded login after import")
    expect(call("POST", "/auth/signup", json_body={"email": "eve@example.com", "password": "password9",
                                                    "display_name": "Eve2"}), 409, "email_taken",
           "imported email still taken")
    rp = call("POST", "/reservations", a, key1, body1)
    check(rp.status == 200 and rp.body == o1.body, "single receipt replay after import -> 200 original", repr(rp))
    rp = call("POST", "/reservation-moves", a, key2, mbody)
    check(rp.status == 200 and rp.body == m1.body, "batch receipt replay after import -> 200 original", repr(rp))
    expect(call("POST", "/reservations", a, key1, dict(body1, party_size=3)), 409, "idempotency_key_reuse",
           "key reuse detection after import")
    r = call("POST", "/reservations", a, keyf, dict(body1, table_id="m_4", starts_at_local=f"{FUT}T16:00"))
    expect(r, 201, name="failed key reusable after import")
    # counters: new records do not collide
    new = [must_book(c, "m_4", f"{FUT_WINTER}T{h}:00") for h in ("12", "14", "16", "18")]
    s2 = call("POST", "/auth/signup", json_body={"email": "new2@example.com", "password": "password9",
                                                 "display_name": "N"})
    allr = all_reservations(a, b, c, eve)
    ids = [x["reservation_id"] for x in allr]
    refs = [x["reference"] for x in allr]
    check(len(ids) == len(set(ids)) and len(refs) == len(set(refs)), "no id/reference collisions after import",
          f"{ids} {refs}")
    if s2.status == 201:
        uids = {s.body["user_id"], s2.body["user_id"], "u_ada", "u_bob", "u_cyd"}
        check(len(uids) == 5, "new user_id does not collide after import", str(uids))
    # repeat import: no duplication
    r = call("POST", "/_test/import", json_body=snapshot)
    expect(r, 204, name="re-import 204")
    check(call("GET", "/reservations", a).body == before_a, "re-import restores exactly (no duplicates)")
    expect(get_res(c, new[0]["reference"]), 404, "not_found", "re-import is replacement, not merge")
    # invalid imports leave state unchanged (observable state, plus export JSON)
    def observable():
        return json.dumps([call("GET", "/reservations", t).body for t in (a, b, c, eve)], sort_keys=True)
    obs_before = observable()
    state_before = json.loads(call("GET", "/_test/export").text)
    for label, payload in [
        ("missing state", {"track": "tablekeeper", "format_version": 1}),
        ("wrong track", dict(snapshot, track="pocketful")),
        ("wrong version", dict(snapshot, format_version=2)),
        ("missing track", {k_: v for k_, v in snapshot.items() if k_ != "track"}),
        ("missing version", {k_: v for k_, v in snapshot.items() if k_ != "format_version"}),
        ("state not object", dict(snapshot, state="garbage")),
        ("state empty object", dict(snapshot, state={})),
        ("state with junk", dict(snapshot, state={"foo": [1, 2, 3]})),
    ]:
        r = call("POST", "/_test/import", json_body=payload)
        expect(r, 422, "validation_failed", f"invalid import ({label}) 422", hard=(label != "state empty object"))
        check(observable() == obs_before, f"invalid import ({label}) left state unchanged")
        if r.status == 204:
            call("POST", "/_test/import", json_body=snapshot)
    soft(json.loads(call("GET", "/_test/export").text) == state_before, "export JSON unchanged by invalid imports")
    r = call("POST", "/_test/import", raw="{nope")
    expect(r, 400, "malformed_request", "import invalid JSON 400")
    # corrupt a nested part of the state
    st = json.loads(json.dumps(snapshot))
    def corrupt(o):
        if isinstance(o, dict):
            for kk in list(o):
                if isinstance(o[kk], list) and o[kk]:
                    o[kk] = [None]
                    return True
                if corrupt(o[kk]):
                    return True
        return False
    corrupt(st["state"])
    r = call("POST", "/_test/import", json_body=st)
    soft(r.status == 422, "import with corrupted nested state -> 422", repr(r))
    soft(observable() == obs_before if r.status != 204 else True, "corrupted import left state unchanged")
    if r.status == 204:
        call("POST", "/_test/import", json_body=snapshot)
    # reset clears imported state
    reset()
    expect(call("GET", "/reservations", a), 401, "unauthenticated", "reset clears imported tokens")
    expect(call("POST", "/auth/login", json_body={"email": "eve@example.com", "password": "password9"}), 401,
           "unauthenticated", "reset clears imported accounts")
    # export is read-only and stable without writes
    e1 = call("GET", "/_test/export").text
    e2 = call("GET", "/_test/export").text
    check(e1 == e2 or json.loads(e1) == json.loads(e2), "export without writes is stable/read-only")


@probe
def reset_rules():
    reset()
    a, b, c = tokens()
    must_book(a, "m_1", f"{FUT}T13:00")
    reset()
    expect(call("GET", "/reservations", a), 401, "unauthenticated", "reset invalidates old tokens")
    a2 = login("ada@example.com", "correct horse")
    check(call("GET", "/reservations", a2).body == {"reservations": []}, "reset clears reservations")
    fx = base_fixture()
    fx["reservations"] = [{"id": "res_seed", "reference": "SEED01", "user_id": "u_ada", "restaurant_id": "r_main",
                           "table_id": "m_2", "starts_at_local": f"{FUT}T19:00", "party_size": 3}]
    reset(fx)
    a = login("ada@example.com", "correct horse")
    g = get_res(a, "SEED01")
    if expect(g, 200, name="seeded reservation visible to owner"):
        check(g.body["reservation_id"] == "res_seed" and g.body["status"] == "confirmed"
              and g.body["starts_at"] == f"{FUT}T19:00:00+02:00" and g.body["ends_at"] == f"{FUT}T20:30:00+02:00",
              "seeded reservation fields", repr(g))
    expect(book(a, "m_2", f"{FUT}T20:00", party=2), 409, "table_unavailable", "seeded booking occupies table")
    check("m_2" not in slot(avail("r_main", FUT, 1), f"{FUT}T19:00")["available_table_ids"], "seed in availability")
    expect(call("POST", "/reservations/SEED01/cancel", a), 200, name="seeded booking cancellable")
    # created reservations after seed do not collide with seeded ids/references
    news = [must_book(a, "m_4", f"{FUT}T{h}:00")["reservation_id"] for h in ("12", "14", "16", "18")]
    check("res_seed" not in news, "new ids don't collide with seeded id")
    # fixture validation: seeded reservations must obey the same rules as created ones
    def seeded(**over):
        fx = base_fixture()
        res = {"id": "res_s", "reference": "SEED02", "user_id": "u_ada", "restaurant_id": "r_main",
               "table_id": "m_2", "starts_at_local": f"{FUT}T19:00", "party_size": 2}
        res.update(over)
        fx["reservations"] = [res]
        return fx
    for ref in ["x", "lower01", "TOO-LONG-WITH-DASH", "ABCDE", "ABCDEFGHIJKLM", "ABC-12", ""]:
        r = call("POST", "/_test/reset", json_body=seeded(reference=ref))
        expect(r, 422, "validation_failed", f"fixture reference {ref!r} (not 6-12 A-Z0-9) 422")
    for ref in ["ABCDEF", "ABCDEFGHIJKL", "123456"]:
        expect(call("POST", "/_test/reset", json_body=seeded(reference=ref)), 204,
               name=f"fixture reference {ref!r} accepted")
    fx = seeded()
    fx["reservations"].append(dict(fx["reservations"][0], id="res_t", reference="SEED03",
                                   starts_at_local=f"{FUT}T19:30"))
    check(call("POST", "/_test/reset", json_body=fx).status == 422, "fixture with overlapping seeded bookings 422")
    for label, over in [("party > capacity", {"party_size": 9}), ("off grid", {"starts_at_local": f"{FUT}T19:15"}),
                        ("outside hours", {"starts_at_local": f"{FUT}T22:30"}),
                        ("unknown user", {"user_id": "u_nobody"}), ("unknown table", {"table_id": "zz"}),
                        ("id > 64", {"id": "r" * 65}), ("party 0", {"party_size": 0})]:
        r = call("POST", "/_test/reset", json_body=seeded(**over))
        check(r.status == 422 and r.code == "validation_failed", f"fixture seeded booking {label} 422", repr(r))
    # planner ruling: invalid fixture leaves state unchanged; past seeded bookings are fine
    reset(seeded())
    a = login("ada@example.com", "correct horse")
    for label, over in [("DST gap", {"restaurant_id": "r_ber_dst", "table_id": "b_1",
                                     "starts_at_local": "2026-03-29T02:30"}),
                        ("unknown restaurant", {"restaurant_id": "nope"}),
                        ("other restaurant's table", {"table_id": "o_1"}),
                        ("bad local format", {"starts_at_local": f"{FUT}T19:00:00"})]:
        r = call("POST", "/_test/reset", json_body=seeded(**over))
        check(r.status == 422 and r.code == "validation_failed", f"fixture seeded booking {label} 422", repr(r))
    g = get_res(a, "SEED02")
    check(g.status == 200 and g.body["starts_at_local"] == f"{FUT}T19:00", "invalid resets left prior state unchanged",
          repr(g))
    expect(call("POST", "/_test/reset", json_body=seeded(starts_at_local=f"{PAST}T19:00")), 204,
           name="seeded booking in the past accepted")
    for label, mut in [("bad timezone", lambda r: r.update(timezone="Mars/Olympus")),
                       ("bad weekday", lambda r: r.update(opening_hours=[{"weekday": "xyz", "opens": "12:00",
                                                                          "closes": "13:00"}])),
                       ("closes <= opens", lambda r: r.update(opening_hours=[{"weekday": "mon", "opens": "13:00",
                                                                              "closes": "12:00"}])),
                       ("slot 0", lambda r: r.update(slot_minutes=0)),
                       ("negative capacity", lambda r: r["tables"][0].update(capacity=-1)),
                       ("table id > 64", lambda r: r["tables"][0].update(id="t" * 65))]:
        fx = base_fixture()
        mut(fx["restaurants"][1])
        r = call("POST", "/_test/reset", json_body=fx)
        soft(r.status == 422 and r.code == "validation_failed", f"fixture {label} 422", repr(r))
    fx = base_fixture()
    fx["users"].append(dict(fx["users"][0], id="u_" + "q" * 63))
    soft(call("POST", "/_test/reset", json_body=fx).status == 422, "fixture user id > 64 / duplicate email 422")
    reset()
    # long id > 64 in fixture
    fx = base_fixture()
    fx["restaurants"][1]["id"] = "r_" + "z" * 63
    r = call("POST", "/_test/reset", json_body=fx)
    soft(r.status == 422 or r.status == 400, "fixture id >64 chars rejected (4xx)", repr(r))
    reset()


# ================================================================ main

def main():
    global BASE, VERBOSE
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--only", default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    BASE = args.base_url.rstrip("/")
    VERBOSE = args.verbose
    t0 = time.time()
    for fn in PROBES:
        if args.only and args.only not in fn.__name__:
            continue
        CURRENT[0] = fn.__name__
        print(f"== {fn.__name__}")
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            record("FAIL", "probe crashed", f"{e!r}\n{traceback.format_exc()}")
    CURRENT[0] = "global"
    check(not SERVER_ERRORS, "no 5xx / transport errors", "\n    " + "\n    ".join(SERVER_ERRORS[:20]))
    check(not SHAPE_ERRORS, "every 4xx/5xx has {error:{code,message}} body", "\n    " + "\n    ".join(SHAPE_ERRORS[:20]))
    n = {s: sum(1 for r in RESULTS if r[0] == s) for s in ("PASS", "FAIL", "WARN")}
    print(f"\nSUMMARY pass={n['PASS']} fail={n['FAIL']} warn={n['WARN']} in {time.time()-t0:.1f}s")
    if n["FAIL"]:
        print("FAILURES:")
        for s, p, c, d in RESULTS:
            if s == "FAIL":
                print(f"  [{p}] {c} -- {d}")
    if n["WARN"]:
        print("WARNINGS:")
        for s, p, c, d in RESULTS:
            if s == "WARN":
                print(f"  [{p}] {c} -- {d}")
    sys.exit(1 if n["FAIL"] else 0)


if __name__ == "__main__":
    main()
