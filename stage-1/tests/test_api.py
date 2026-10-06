"""Black-box HTTP tests for the Stage 1 service.

By default the server is started in-process on a free port. Set
TABLEKEEPER_URL=http://127.0.0.1:8080 to run the same tests against a
running container instead.
"""

import copy
import http.client
import json
import os
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from urllib.parse import urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BASE_URL = os.environ.get("TABLEKEEPER_URL")
_server = None


def setUpModule():
    global BASE_URL, _server
    if BASE_URL:
        return
    from app.server import make_server
    _server = make_server(host="127.0.0.1", port=0)
    threading.Thread(target=_server.serve_forever, daemon=True).start()
    BASE_URL = f"http://127.0.0.1:{_server.server_address[1]}"


def tearDownModule():
    if _server is not None:
        _server.shutdown()


def request(method, path, body=None, token=None, key=None, raw=None, headers=None):
    parts = urlsplit(BASE_URL)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=30)
    hdrs = dict(headers or {})
    payload = None
    if raw is not None:
        payload = raw if isinstance(raw, bytes) else raw.encode()
    elif body is not None:
        payload = json.dumps(body).encode()
    if payload is not None:
        hdrs["Content-Type"] = "application/json"
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if key is not None:
        hdrs["Idempotency-Key"] = key
    conn.request(method, path, body=payload, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    parsed = json.loads(data) if data else None
    return resp.status, parsed, resp.getheader("Content-Type")


def next_weekday(start, weekday):
    names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    delta = (names.index(weekday) - start.weekday()) % 7
    return start + timedelta(days=delta)


FUTURE_THU = next_weekday(date(2030, 1, 1), "thu")   # far beyond any cutoff
PAST_THU = next_weekday(date(2020, 1, 1), "thu")
ALL_WEEK = [{"weekday": d, "opens": "00:00", "closes": "23:59"} for d in
            ("mon", "tue", "wed", "thu", "fri", "sat", "sun")]


def fixture(**overrides):
    fx = {
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
            {"id": "u_bob", "email": "bob@example.com", "password": "battery staple", "display_name": "Bob"},
        ],
        "restaurants": [
            {
                "id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin",
                "slot_minutes": 30, "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
                "opening_hours": [
                    {"weekday": "thu", "opens": "18:00", "closes": "23:00"},
                    {"weekday": "fri", "opens": "18:00", "closes": "23:30"},
                ],
                "tables": [
                    {"id": "t_1", "label": "1", "capacity": 2},
                    {"id": "t_2", "label": "2", "capacity": 4},
                    {"id": "t_3", "label": "3", "capacity": 6},
                ],
            },
            {
                "id": "r_ny", "name": "Night Owl", "timezone": "America/New_York",
                "slot_minutes": 30, "reservation_duration_minutes": 60, "cancellation_cutoff_minutes": 0,
                "opening_hours": [{"weekday": "sun", "opens": "00:00", "closes": "05:00"}],
                "tables": [{"id": "t_1", "label": "A", "capacity": 4}],
            },
            {
                "id": "r_berlin_night", "name": "Nachtbar", "timezone": "Europe/Berlin",
                "slot_minutes": 30, "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 0,
                "opening_hours": [{"weekday": "sun", "opens": "00:00", "closes": "05:00"}],
                "tables": [{"id": "t_1", "label": "A", "capacity": 4}],
            },
        ],
        "reservations": [],
    }
    fx.update(overrides)
    return fx


class Base(unittest.TestCase):
    def setUp(self):
        self.reset(fixture())

    def reset(self, fx):
        status, body, _ = request("POST", "/_test/reset", fx)
        self.assertEqual(status, 204, body)

    def login(self, email="ada@example.com", password="correct horse"):
        status, body, _ = request("POST", "/auth/login", {"email": email, "password": password})
        self.assertEqual(status, 200, body)
        return body["token"]

    def book(self, token, key, starts_at_local=None, table_id="t_2", party_size=2, restaurant_id="r_anker"):
        body = {"restaurant_id": restaurant_id, "table_id": table_id, "party_size": party_size,
                "starts_at_local": starts_at_local or f"{FUTURE_THU}T19:00"}
        return request("POST", "/reservations", body, token=token, key=key)

    def assertError(self, result, status, code):
        got_status, body, ctype = result
        self.assertEqual(got_status, status, body)
        self.assertEqual(body["error"]["code"], code, body)
        self.assertIsInstance(body["error"]["message"], str)
        self.assertTrue(ctype.startswith("application/json"))


class TestRuntime(Base):
    def test_health(self):
        status, body, ctype = request("GET", "/health")
        self.assertEqual((status, body), (200, {"status": "ok"}))
        self.assertEqual(ctype, "application/json; charset=utf-8")

    def test_unknown_route_is_json_404(self):
        self.assertError(request("GET", "/nope"), 404, "not_found")

    def test_reset_replaces_state(self):
        token = self.login()
        self.assertEqual(self.book(token, "k1")[0], 201)
        self.reset(fixture())
        self.assertError(request("GET", "/reservations", token=token), 401, "unauthenticated")
        token = self.login()
        self.assertEqual(request("GET", "/reservations", token=token)[1], {"reservations": []})

    def test_reset_rejects_bad_fixture_without_change(self):
        long_id = "x" * 65
        bad = fixture()
        bad["restaurants"][0]["id"] = long_id
        self.assertError(request("POST", "/_test/reset", bad), 422, "validation_failed")
        bad = fixture()
        bad["restaurants"][0]["timezone"] = "Mars/Olympus"
        self.assertError(request("POST", "/_test/reset", bad), 422, "validation_failed")
        self.assertError(request("POST", "/_test/reset", raw="{nope"), 400, "malformed_request")
        self.assertEqual(len(request("GET", "/restaurants")[1]["restaurants"]), 3)

    def test_seeded_reservations(self):
        fx = fixture(reservations=[{
            "id": "res_seed", "reference": "SEED01", "user_id": "u_ada", "restaurant_id": "r_anker",
            "table_id": "t_2", "starts_at_local": f"{FUTURE_THU}T19:00", "party_size": 3,
        }])
        self.reset(fx)
        token = self.login()
        status, body, _ = request("GET", "/reservations/SEED01", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "confirmed")
        self.assertEqual(body["reservation_id"], "res_seed")
        self.assertError(self.book(token, "k", table_id="t_2"), 409, "table_unavailable")
        # Generated IDs and references never collide with seeded ones.
        status, created, _ = self.book(token, "k2", table_id="t_3")
        self.assertEqual(status, 201)
        self.assertNotEqual(created["reservation_id"], "res_seed")

    def test_past_seed_is_allowed(self):
        fx = fixture(reservations=[{
            "id": "res_old", "reference": "OLD001", "user_id": "u_ada", "restaurant_id": "r_anker",
            "table_id": "t_2", "starts_at_local": f"{PAST_THU}T19:00", "party_size": 2,
        }])
        self.reset(fx)
        self.assertEqual(request("GET", "/reservations/OLD001", token=self.login())[0], 200)


class TestAuth(Base):
    def test_signup_and_login(self):
        status, body, _ = request("POST", "/auth/signup",
                                  {"email": "cy@example.com", "password": "longenough", "display_name": "Cy"})
        self.assertEqual(status, 201, body)
        self.assertEqual(body["display_name"], "Cy")
        self.assertTrue(body["token"])
        status, login, _ = request("POST", "/auth/login", {"email": "cy@example.com", "password": "longenough"})
        self.assertEqual(status, 200)
        self.assertEqual(login["user_id"], body["user_id"])
        self.assertNotEqual(login["token"], body["token"])
        # Both tokens stay valid.
        for tok in (login["token"], body["token"]):
            self.assertEqual(request("GET", "/reservations", token=tok)[0], 200)

    def test_signup_errors(self):
        self.assertError(request("POST", "/auth/signup",
                                 {"email": "ada@example.com", "password": "longenough", "display_name": "A"}),
                         409, "email_taken")
        self.assertError(request("POST", "/auth/signup",
                                 {"email": "new@example.com", "password": "short", "display_name": "A"}),
                         422, "validation_failed")
        for email in ("nodomain", "@x", "x@", "a@b@c"):
            self.assertError(request("POST", "/auth/signup",
                                     {"email": email, "password": "longenough", "display_name": "A"}),
                             422, "validation_failed")
        self.assertError(request("POST", "/auth/signup", {"email": 5, "password": "longenough"}),
                         400, "malformed_request")
        self.assertError(request("POST", "/auth/signup", raw="not json"), 400, "malformed_request")
        self.assertError(request("POST", "/auth/signup", raw="[1]"), 400, "malformed_request")

    def test_login_errors(self):
        self.assertError(request("POST", "/auth/login", {"email": "ada@example.com", "password": "wrong pass"}),
                         401, "unauthenticated")
        self.assertError(request("POST", "/auth/login", {"email": "who@example.com", "password": "whatever1"}),
                         401, "unauthenticated")

    def test_bearer_required(self):
        self.assertError(request("GET", "/reservations"), 401, "unauthenticated")
        self.assertError(request("GET", "/reservations", headers={"Authorization": "Basic abc"}),
                         401, "unauthenticated")
        self.assertError(request("GET", "/reservations", token="bogus"), 401, "unauthenticated")
        self.assertError(request("POST", "/reservations", {}, key="k"), 401, "unauthenticated")
        # 401 takes precedence over a missing or malformed body.
        self.assertError(request("POST", "/reservations", key="k"), 401, "unauthenticated")
        self.assertError(request("POST", "/reservations", raw="{bad", key="k"), 401, "unauthenticated")
        self.assertError(request("POST", "/reservation-moves", key="k"), 401, "unauthenticated")
        self.assertError(request("PATCH", "/reservations/ABCDEF"), 401, "unauthenticated")
        self.assertError(request("POST", "/reservations/ABCDEF/cancel"), 401, "unauthenticated")

    def test_concurrent_logins_are_fast(self):
        started = time.time()
        with ThreadPoolExecutor(50) as pool:
            results = list(pool.map(lambda _: request("POST", "/auth/login",
                                                      {"email": "ada@example.com", "password": "correct horse"}),
                                    range(50)))
        self.assertTrue(all(r[0] == 200 for r in results))
        self.assertLess(time.time() - started, 5)

    def test_password_not_stored_in_plaintext(self):
        status, export, _ = request("GET", "/_test/export")
        self.assertEqual(status, 200)
        self.assertNotIn("correct horse", json.dumps(export))


class TestRestaurants(Base):
    def test_public_listing(self):
        status, body, _ = request("GET", "/restaurants")
        self.assertEqual(status, 200)
        self.assertEqual(body["restaurants"][0], {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin"})

    def test_detail(self):
        status, body, _ = request("GET", "/restaurants/r_anker")
        self.assertEqual(status, 200)
        self.assertEqual(body["slot_minutes"], 30)
        self.assertEqual(body["reservation_duration_minutes"], 90)
        self.assertEqual(body["cancellation_cutoff_minutes"], 120)
        self.assertEqual(body["opening_hours"][0], {"weekday": "thu", "opens": "18:00", "closes": "23:00"})
        self.assertEqual(body["tables"][0], {"id": "t_1", "label": "1", "capacity": 2})
        self.assertError(request("GET", "/restaurants/zzz"), 404, "not_found")


class TestAvailability(Base):
    def avail(self, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return request("GET", f"/availability?{query}")

    def test_slots_and_capacity(self):
        status, body, _ = self.avail(restaurant_id="r_anker", date=FUTURE_THU, party_size=4)
        self.assertEqual(status, 200)
        self.assertEqual(body["timezone"], "Europe/Berlin")
        locals_ = [s["starts_at_local"] for s in body["slots"]]
        # 18:00 .. 21:30 (21:30 + 90 = 23:00 = closes).
        self.assertEqual(locals_[0], f"{FUTURE_THU}T18:00")
        self.assertEqual(locals_[-1], f"{FUTURE_THU}T21:30")
        self.assertEqual(len(locals_), 8)
        self.assertEqual(body["slots"][0]["available_table_ids"], ["t_2", "t_3"])
        self.assertTrue(body["slots"][0]["starts_at"].endswith("+01:00"))

    def test_closed_day(self):
        self.assertEqual(self.avail(restaurant_id="r_anker", date=FUTURE_THU + timedelta(days=3),
                                    party_size=2)[1]["slots"], [])

    def test_booked_table_removed_and_freed_on_cancel(self):
        token = self.login()
        status, res, _ = self.book(token, "k1", table_id="t_2")
        self.assertEqual(status, 201)
        slots = {s["starts_at_local"]: s["available_table_ids"]
                 for s in self.avail(restaurant_id="r_anker", date=FUTURE_THU, party_size=2)[1]["slots"]}
        self.assertEqual(slots[f"{FUTURE_THU}T18:00"], ["t_1", "t_3"])  # 18:00-19:30 overlaps 19:00
        self.assertEqual(slots[f"{FUTURE_THU}T18:30"], ["t_1", "t_3"])
        self.assertEqual(slots[f"{FUTURE_THU}T20:00"], ["t_1", "t_3"])
        self.assertEqual(slots[f"{FUTURE_THU}T20:30"], ["t_1", "t_2", "t_3"])  # half-open interval
        request("POST", f"/reservations/{res['reference']}/cancel", token=token)
        slots = {s["starts_at_local"]: s["available_table_ids"]
                 for s in self.avail(restaurant_id="r_anker", date=FUTURE_THU, party_size=2)[1]["slots"]}
        self.assertEqual(slots[f"{FUTURE_THU}T19:00"], ["t_1", "t_2", "t_3"])

    def test_param_validation(self):
        self.assertError(self.avail(date=FUTURE_THU, party_size=2), 422, "validation_failed")
        self.assertError(self.avail(restaurant_id="r_anker", party_size=2), 422, "validation_failed")
        self.assertError(self.avail(restaurant_id="r_anker", date=FUTURE_THU), 422, "validation_failed")
        for bad in ("1e9", "4.0", "%2B4", "-1", "0", "", "abc"):
            self.assertError(self.avail(restaurant_id="r_anker", date=FUTURE_THU, party_size=bad),
                             422, "validation_failed")
        for bad in ("2026-02-30", "2026-9-24", "tomorrow", "0001-01-01", "9999-12-31", "0000-01-01"):
            self.assertError(self.avail(restaurant_id="r_anker", date=bad, party_size=2), 422, "validation_failed")
        self.assertError(self.avail(restaurant_id="nope", date=FUTURE_THU, party_size=2), 404, "not_found")
        # Unknown params are ignored.
        self.assertEqual(self.avail(restaurant_id="r_anker", date=FUTURE_THU, party_size=2, foo="bar")[0], 200)


class TestReservations(Base):
    def test_create_shape(self):
        token = self.login()
        status, body, _ = self.book(token, "k1", party_size=4)
        self.assertEqual(status, 201, body)
        self.assertEqual(set(body), {"reservation_id", "reference", "restaurant_id", "table_id", "party_size",
                                     "status", "starts_at_local", "starts_at", "ends_at", "created_at"})
        self.assertRegex(body["reference"], r"^[A-Z0-9]{6,12}$")
        self.assertEqual(body["status"], "confirmed")
        self.assertEqual(body["starts_at"], f"{FUTURE_THU}T19:00:00+01:00")
        self.assertEqual(body["ends_at"], f"{FUTURE_THU}T20:30:00+01:00")
        self.assertRegex(body["created_at"], r"\+00:00$")
        self.assertLessEqual(len(body["reservation_id"]), 64)

    def test_create_errors(self):
        token = self.login()
        self.assertEqual(self.book(token, "a", table_id="t_2")[0], 201)
        self.assertError(self.book(token, "b", table_id="t_2", starts_at_local=f"{FUTURE_THU}T20:00"),
                         409, "table_unavailable")
        self.assertEqual(self.book(token, "c", table_id="t_2", starts_at_local=f"{FUTURE_THU}T20:30")[0], 201)
        self.assertError(self.book(token, "d", starts_at_local=f"{FUTURE_THU}T19:15"), 422, "not_on_slot_grid")
        self.assertError(self.book(token, "e", starts_at_local=f"{FUTURE_THU}T17:30"), 422, "outside_opening_hours")
        self.assertError(self.book(token, "f", starts_at_local=f"{FUTURE_THU}T22:00"), 422, "outside_opening_hours")
        self.assertError(self.book(token, "g", starts_at_local=f"{FUTURE_THU + timedelta(days=2)}T19:00"),
                         422, "outside_opening_hours")
        self.assertError(self.book(token, "h", table_id="t_1", party_size=3), 422, "party_exceeds_capacity")
        for bad in (0, -1, 2.5, 2.0, "2", True, None):
            self.assertError(self.book(token, f"p{bad!r}", party_size=bad), 422, "validation_failed")
        for bad in (f"{FUTURE_THU}T19:00:00", f"{FUTURE_THU}T19:00+01:00", f"{FUTURE_THU}T19:00Z",
                    f"{FUTURE_THU} 19:00", "2030-02-30T19:00", "garbage", "9999-12-31T23:30",
                    "0001-01-01T00:00"):
            self.assertError(self.book(token, f"s{bad}", starts_at_local=bad), 422, "validation_failed")
        self.assertError(self.book(token, "i", restaurant_id="nope"), 404, "not_found")
        self.assertError(self.book(token, "j", table_id="nope"), 404, "not_found")
        self.assertError(request("POST", "/reservations", {"restaurant_id": 1, "table_id": "t_1",
                                                          "starts_at_local": f"{FUTURE_THU}T19:00", "party_size": 2},
                                 token=token, key="k"), 400, "malformed_request")
        self.assertError(request("POST", "/reservations", {"restaurant_id": "r_anker", "table_id": "t_1",
                                                          "starts_at_local": 1900, "party_size": 2},
                                 token=token, key="k"), 400, "malformed_request")
        self.assertError(request("POST", "/reservations", {"restaurant_id": "r_anker", "table_id": "t_1",
                                                          "party_size": 2}, token=token, key="k"),
                         422, "validation_failed")
        self.assertError(request("POST", "/reservations", raw="{bad", token=token, key="k"), 400, "malformed_request")

    def test_table_belongs_to_other_restaurant(self):
        fx = fixture()
        fx["restaurants"][1]["tables"][0]["id"] = "t_ny"
        self.reset(fx)
        token = self.login()
        self.assertError(self.book(token, "x", table_id="t_ny"), 404, "not_found")

    def test_unknown_fields_ignored(self):
        token = self.login()
        body = {"restaurant_id": "r_anker", "table_id": "t_2", "party_size": 2,
                "starts_at_local": f"{FUTURE_THU}T19:00", "extra": {"x": 1}}
        self.assertEqual(request("POST", "/reservations", body, token=token, key="k")[0], 201)

    def test_list_and_get_privacy(self):
        ada, bob = self.login(), self.login("bob@example.com", "battery staple")
        _, r1, _ = self.book(ada, "1", starts_at_local=f"{FUTURE_THU}T18:00")
        _, r2, _ = self.book(ada, "2", starts_at_local=f"{FUTURE_THU}T21:00")
        _, r3, _ = self.book(bob, "1", table_id="t_3")
        request("POST", f"/reservations/{r1['reference']}/cancel", token=ada)
        listed = request("GET", "/reservations", token=ada)[1]["reservations"]
        self.assertEqual([r["reference"] for r in listed], [r2["reference"], r1["reference"]])
        self.assertEqual(listed[1]["status"], "cancelled")
        self.assertError(request("GET", f"/reservations/{r3['reference']}", token=ada), 404, "not_found")
        self.assertError(request("POST", f"/reservations/{r3['reference']}/cancel", token=ada), 404, "not_found")
        self.assertError(request("PATCH", f"/reservations/{r3['reference']}", {"party_size": 1}, token=ada),
                         404, "not_found")
        self.assertEqual(request("GET", f"/reservations/{r2['reference']}", token=ada)[1], r2)

    def test_cancel(self):
        token = self.login()
        _, res, _ = self.book(token, "k")
        status, body, _ = request("POST", f"/reservations/{res['reference']}/cancel", token=token)
        self.assertEqual((status, body["status"], body["reference"]), (200, "cancelled", res["reference"]))
        status, again, _ = request("POST", f"/reservations/{res['reference']}/cancel", token=token)
        self.assertEqual((status, again), (200, body))
        self.assertError(request("POST", "/reservations/NOPE99/cancel", token=token), 404, "not_found")

    def test_cutoff(self):
        fx = fixture(reservations=[
            {"id": "res_past", "reference": "PAST01", "user_id": "u_ada", "restaurant_id": "r_anker",
             "table_id": "t_2", "starts_at_local": f"{PAST_THU}T19:00", "party_size": 2},
        ])
        self.reset(fx)
        token = self.login()
        self.assertError(request("POST", "/reservations/PAST01/cancel", token=token), 409, "cutoff_passed")
        self.assertError(request("PATCH", "/reservations/PAST01", {"party_size": 1}, token=token),
                         409, "cutoff_passed")
        # Booking in the past is not rejected solely for being past.
        status, past, _ = self.book(token, "k", starts_at_local=f"{PAST_THU}T20:00", table_id="t_3")
        self.assertEqual(status, 201, past)

    def test_cutoff_boundary_near_now(self):
        # A restaurant open all week with a 60-minute cutoff: a booking about
        # 30 minutes ahead is inside the cutoff, one 3 hours ahead is not.
        from datetime import datetime, timezone
        fx = fixture()
        fx["restaurants"].append({
            "id": "r_utc", "name": "UTC", "timezone": "UTC", "slot_minutes": 1,
            "reservation_duration_minutes": 1, "cancellation_cutoff_minutes": 60,
            "opening_hours": ALL_WEEK, "tables": [{"id": "t", "capacity": 4}],
        })
        self.reset(fx)
        token = self.login()
        now = datetime.now(timezone.utc)
        near = (now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M")
        far = (now + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
        if near[:10] != now.strftime("%Y-%m-%d") or far[:10] != now.strftime("%Y-%m-%d") or near[11:] > "23:57" or far[11:] > "23:57":
            self.skipTest("too close to midnight UTC")
        _, n, _ = self.book(token, "n", starts_at_local=near, restaurant_id="r_utc", table_id="t")
        _, f, _ = self.book(token, "f", starts_at_local=far, restaurant_id="r_utc", table_id="t")
        self.assertError(request("POST", f"/reservations/{n['reference']}/cancel", token=token), 409, "cutoff_passed")
        self.assertEqual(request("POST", f"/reservations/{f['reference']}/cancel", token=token)[0], 200)

    def test_patch(self):
        token = self.login()
        _, res, _ = self.book(token, "k", table_id="t_2", party_size=2)
        _, other, _ = self.book(token, "k2", table_id="t_3", starts_at_local=f"{FUTURE_THU}T19:00")
        ref = res["reference"]
        status, body, _ = request("PATCH", f"/reservations/{ref}", {"starts_at_local": f"{FUTURE_THU}T20:30"},
                                  token=token)
        self.assertEqual(status, 200, body)
        self.assertEqual((body["reference"], body["reservation_id"]), (ref, res["reservation_id"]))
        self.assertEqual(body["ends_at"], f"{FUTURE_THU}T22:00:00+01:00")
        self.assertEqual(body["created_at"], res["created_at"])
        # Old slot released.
        self.assertEqual(self.book(token, "k3", table_id="t_2")[0], 201)
        # Overlap with another booking -> 409, original untouched.
        self.assertError(request("PATCH", f"/reservations/{ref}", {"table_id": "t_3",
                                                                    "starts_at_local": f"{FUTURE_THU}T19:30"},
                                 token=token), 409, "table_unavailable")
        self.assertEqual(request("GET", f"/reservations/{ref}", token=token)[1], body)
        # Moving within its own interval is not a self-conflict.
        self.assertEqual(request("PATCH", f"/reservations/{ref}", {"starts_at_local": f"{FUTURE_THU}T21:00"},
                                 token=token)[0], 200)
        self.assertError(request("PATCH", f"/reservations/{ref}", {"party_size": 5}, token=token),
                         422, "party_exceeds_capacity")
        self.assertError(request("PATCH", f"/reservations/{ref}", {"party_size": "5"}, token=token),
                         422, "validation_failed")
        self.assertError(request("PATCH", f"/reservations/{ref}", {"table_id": 7}, token=token),
                         400, "malformed_request")
        self.assertError(request("PATCH", f"/reservations/{ref}", {"table_id": "nope"}, token=token),
                         404, "not_found")
        self.assertError(request("PATCH", f"/reservations/{ref}", {"starts_at_local": f"{FUTURE_THU}T21:10"},
                                 token=token), 422, "not_on_slot_grid")
        request("POST", f"/reservations/{other['reference']}/cancel", token=token)
        self.assertError(request("PATCH", f"/reservations/{other['reference']}", {"party_size": 1}, token=token),
                         409, "reservation_cancelled")
        self.assertError(request("PATCH", "/reservations/NOPE99", {"party_size": 1}, token=token), 404, "not_found")


class TestIdempotency(Base):
    def test_replay_and_reuse(self):
        token = self.login()
        status, first, _ = self.book(token, "key-1")
        self.assertEqual(status, 201)
        # Same JSON value, different key order and whitespace.
        raw = json.dumps({"starts_at_local": f"{FUTURE_THU}T19:00", "party_size": 2, "table_id": "t_2",
                          "restaurant_id": "r_anker"}, indent=3)
        status, again, _ = request("POST", "/reservations", raw=raw, token=token, key="key-1")
        self.assertEqual((status, again), (200, first))
        # Replay still returns the original after cancellation.
        request("POST", f"/reservations/{first['reference']}/cancel", token=token)
        status, again, _ = self.book(token, "key-1")
        self.assertEqual((status, again), (200, first))
        self.assertEqual(len(request("GET", "/reservations", token=token)[1]["reservations"]), 1)
        # Different body, even an invalid one, is 409.
        self.assertError(self.book(token, "key-1", party_size=3), 409, "idempotency_key_reuse")
        self.assertError(request("POST", "/reservations", {"junk": True}, token=token, key="key-1"),
                         409, "idempotency_key_reuse")

    def test_key_header_rules(self):
        token = self.login()
        self.assertError(request("POST", "/reservations", {"restaurant_id": "r_anker"}, token=token),
                         400, "missing_idempotency_key")
        self.assertError(self.book(token, ""), 400, "missing_idempotency_key")
        self.assertError(self.book(token, "x" * 256), 422, "validation_failed")
        self.assertEqual(self.book(token, "x" * 255)[0], 201)

    def test_failed_key_is_reusable(self):
        token = self.login()
        self.assertError(self.book(token, "retry", table_id="t_1", party_size=3), 422, "party_exceeds_capacity")
        self.assertEqual(self.book(token, "retry", table_id="t_2", party_size=3)[0], 201)

    def test_scoped_per_user_and_path(self):
        ada, bob = self.login(), self.login("bob@example.com", "battery staple")
        self.assertEqual(self.book(ada, "same", table_id="t_2")[0], 201)
        self.assertEqual(self.book(bob, "same", table_id="t_3")[0], 201)
        _, res, _ = self.book(ada, "other", table_id="t_1", starts_at_local=f"{FUTURE_THU}T18:00")
        # Same key on a different path is not a replay.
        status, body, _ = request("POST", "/reservation-moves",
                                  {"moves": [{"reference": res["reference"], "party_size": 1}]},
                                  token=ada, key="same")
        self.assertEqual(status, 201, body)

    def test_concurrent_identical_requests(self):
        token = self.login()
        with ThreadPoolExecutor(30) as pool:
            results = list(pool.map(lambda _: self.book(token, "race"), range(30)))
        statuses = sorted(r[0] for r in results)
        self.assertEqual(statuses.count(201), 1, statuses)
        self.assertEqual(statuses.count(200), 29, statuses)
        bodies = {json.dumps(r[1], sort_keys=True) for r in results}
        self.assertEqual(len(bodies), 1)
        self.assertEqual(len(request("GET", "/reservations", token=token)[1]["reservations"]), 1)


class TestConcurrency(Base):
    def test_no_double_booking(self):
        token = self.login()
        with ThreadPoolExecutor(50) as pool:
            results = list(pool.map(lambda i: self.book(token, f"k{i}"), range(50)))
        statuses = [r[0] for r in results]
        self.assertEqual(statuses.count(201), 1, statuses)
        self.assertEqual(statuses.count(409), 49, statuses)

    def test_overlapping_starts_race(self):
        token = self.login()
        times = [f"{FUTURE_THU}T{h}" for h in ("18:00", "18:30", "19:00", "19:30", "20:00")] * 10
        with ThreadPoolExecutor(50) as pool:
            results = list(pool.map(lambda p: self.book(token, f"k{p[0]}", starts_at_local=p[1]),
                                    enumerate(times)))
        self.assertTrue(all(r[0] in (201, 409) for r in results))
        booked = sorted(r[1]["starts_at_local"] for r in results if r[0] == 201)
        for a, b in zip(booked, booked[1:]):
            ta, tb = int(a[11:13]) * 60 + int(a[14:]), int(b[11:13]) * 60 + int(b[14:])
            self.assertGreaterEqual(tb - ta, 90, booked)


class TestDST(Base):
    def avail_locals(self, restaurant_id, day):
        status, body, _ = request("GET", f"/availability?restaurant_id={restaurant_id}&date={day}&party_size=2")
        self.assertEqual(status, 200)
        return {s["starts_at_local"][11:]: s["starts_at"] for s in body["slots"]}

    def test_berlin_spring_forward(self):
        slots = self.avail_locals("r_berlin_night", "2026-03-29")
        self.assertNotIn("02:00", slots)
        self.assertNotIn("02:30", slots)
        self.assertEqual(slots["01:30"], "2026-03-29T01:30:00+01:00")
        self.assertEqual(slots["03:00"], "2026-03-29T03:00:00+02:00")
        token = self.login()
        self.assertError(self.book(token, "a", restaurant_id="r_berlin_night", table_id="t_1",
                                   starts_at_local="2026-03-29T02:30"), 422, "invalid_local_time")
        status, body, _ = self.book(token, "b", restaurant_id="r_berlin_night", table_id="t_1",
                                    starts_at_local="2026-03-29T01:00")
        self.assertEqual(status, 201, body)
        # 01:00+01:00 plus 90 real minutes = 03:30+02:00.
        self.assertEqual(body["ends_at"], "2026-03-29T03:30:00+02:00")

    def test_berlin_fall_back(self):
        slots = self.avail_locals("r_berlin_night", "2026-10-25")
        self.assertEqual(slots["02:00"], "2026-10-25T02:00:00+02:00")
        self.assertEqual(slots["02:30"], "2026-10-25T02:30:00+02:00")
        self.assertEqual(slots["03:00"], "2026-10-25T03:00:00+01:00")
        token = self.login()
        status, body, _ = self.book(token, "a", restaurant_id="r_berlin_night", table_id="t_1",
                                    starts_at_local="2026-10-25T01:30")
        self.assertEqual(status, 201, body)
        self.assertEqual(body["starts_at"], "2026-10-25T01:30:00+02:00")
        self.assertEqual(body["ends_at"], "2026-10-25T02:00:00+01:00")
        status, body, _ = self.book(token, "b", restaurant_id="r_berlin_night", table_id="t_1",
                                    starts_at_local="2026-10-25T03:00")
        self.assertEqual(status, 201, body)

    def test_berlin_fall_back_last_slot_uses_real_time(self):
        # 05:00 close; duration 90. 03:30+01:00 ends 05:00 -> allowed.
        slots = self.avail_locals("r_berlin_night", "2026-10-25")
        self.assertIn("03:30", slots)
        self.assertNotIn("04:00", slots)

    def test_new_york_spring_forward(self):
        slots = self.avail_locals("r_ny", "2026-03-08")
        self.assertNotIn("02:00", slots)
        self.assertNotIn("02:30", slots)
        self.assertEqual(slots["01:30"], "2026-03-08T01:30:00-05:00")
        self.assertEqual(slots["03:00"], "2026-03-08T03:00:00-04:00")
        token = self.login()
        self.assertError(self.book(token, "a", restaurant_id="r_ny", table_id="t_1",
                                   starts_at_local="2026-03-08T02:00"), 422, "invalid_local_time")
        status, body, _ = self.book(token, "b", restaurant_id="r_ny", table_id="t_1",
                                    starts_at_local="2026-03-08T01:30")
        self.assertEqual(status, 201, body)
        self.assertEqual(body["ends_at"], "2026-03-08T03:30:00-04:00")

    def test_new_york_fall_back(self):
        slots = self.avail_locals("r_ny", "2026-11-01")
        self.assertEqual(slots["01:00"], "2026-11-01T01:00:00-04:00")
        self.assertEqual(slots["01:30"], "2026-11-01T01:30:00-04:00")
        self.assertEqual(slots["02:00"], "2026-11-01T02:00:00-05:00")
        self.assertEqual(len(slots), len(set(slots)))
        token = self.login()
        status, body, _ = self.book(token, "a", restaurant_id="r_ny", table_id="t_1",
                                    starts_at_local="2026-11-01T01:30")
        self.assertEqual(status, 201, body)
        self.assertEqual(body["starts_at"], "2026-11-01T01:30:00-04:00")
        self.assertEqual(body["ends_at"], "2026-11-01T01:30:00-05:00")
        # The 01:00 first occurrence ends at 01:00-05:00 and overlaps the 01:30 booking.
        self.assertError(self.book(token, "b", restaurant_id="r_ny", table_id="t_1",
                                   starts_at_local="2026-11-01T01:00"), 409, "table_unavailable")
        # The booking occupies until 01:30-05:00, so 02:00-05:00 is free again.
        status, body, _ = self.book(token, "c", restaurant_id="r_ny", table_id="t_1",
                                    starts_at_local="2026-11-01T02:00")
        self.assertEqual(status, 201, body)
        self.assertEqual(body["starts_at"], "2026-11-01T02:00:00-05:00")


class TestMoves(Base):
    def setUp(self):
        super().setUp()
        self.token = self.login()
        _, self.a, _ = self.book(self.token, "a", table_id="t_2", starts_at_local=f"{FUTURE_THU}T19:00")
        _, self.b, _ = self.book(self.token, "b", table_id="t_3", starts_at_local=f"{FUTURE_THU}T19:00")

    def move(self, moves, key="m", token=None):
        return request("POST", "/reservation-moves", {"moves": moves}, token=token or self.token, key=key)

    def test_swap_tables(self):
        status, body, _ = self.move([{"reference": self.a["reference"], "table_id": "t_3"},
                                     {"reference": self.b["reference"], "table_id": "t_2"}])
        self.assertEqual(status, 201, body)
        self.assertEqual([r["table_id"] for r in body["reservations"]], ["t_3", "t_2"])
        self.assertEqual(body["reservations"][0]["reservation_id"], self.a["reservation_id"])
        self.assertEqual(body["reservations"][0]["created_at"], self.a["created_at"])
        # Replay -> 200 identical even after changes.
        request("POST", f"/reservations/{self.a['reference']}/cancel", token=self.token)
        status, again, _ = self.move([{"reference": self.a["reference"], "table_id": "t_3"},
                                      {"reference": self.b["reference"], "table_id": "t_2"}])
        self.assertEqual((status, again), (200, body))
        self.assertError(self.move([{"reference": self.a["reference"]}]), 409, "idempotency_key_reuse")

    def test_no_op_and_input_order(self):
        status, body, _ = self.move([{"reference": self.b["reference"]},
                                     {"reference": self.a["reference"], "party_size": 3}])
        self.assertEqual(status, 201, body)
        self.assertEqual([r["reference"] for r in body["reservations"]], [self.b["reference"], self.a["reference"]])
        self.assertEqual(body["reservations"][0], self.b)
        self.assertEqual(body["reservations"][1]["party_size"], 3)

    def test_all_or_nothing(self):
        _, c, _ = self.book(self.token, "c", table_id="t_1", starts_at_local=f"{FUTURE_THU}T21:00")
        # Second move collides with unlisted booking c -> nothing changes.
        self.assertError(self.move([{"reference": self.a["reference"], "starts_at_local": f"{FUTURE_THU}T18:00"},
                                    {"reference": self.b["reference"], "table_id": "t_1",
                                     "starts_at_local": f"{FUTURE_THU}T20:00"}]), 409, "table_unavailable")
        self.assertEqual(request("GET", f"/reservations/{self.a['reference']}", token=self.token)[1], self.a)
        # Key unused after failure.
        self.assertEqual(self.move([{"reference": self.a["reference"], "party_size": 1}])[0], 201)

    def test_resulting_overlap_between_listed(self):
        self.assertError(self.move([{"reference": self.a["reference"], "table_id": "t_3",
                                     "starts_at_local": f"{FUTURE_THU}T19:30"},
                                    {"reference": self.b["reference"]}]), 409, "table_unavailable")

    def test_validation(self):
        ref = self.a["reference"]
        for moves in ([], [{"reference": ref}] * 2, "x", [{"table_id": "t_1"}], [{"reference": 5}], [1],
                      [{"reference": f"R{i}"} for i in range(9)]):
            self.assertError(self.move(moves, key=f"v{moves!r}"), 422, "validation_failed")
        self.assertError(request("POST", "/reservation-moves", {}, token=self.token, key="v"),
                         422, "validation_failed")
        self.assertError(self.move([{"reference": "NOPE99"}], key="n"), 404, "not_found")
        bob = self.login("bob@example.com", "battery staple")
        self.assertError(self.move([{"reference": ref}], key="n", token=bob), 404, "not_found")
        self.assertError(request("POST", "/reservation-moves", {"moves": [{"reference": ref}]}), 401,
                         "unauthenticated")
        self.assertError(request("POST", "/reservation-moves", {"moves": [{"reference": ref}]}, token=self.token),
                         400, "missing_idempotency_key")
        self.assertError(self.move([{"reference": ref, "party_size": 9}], key="cap"), 422, "party_exceeds_capacity")
        self.assertError(self.move([{"reference": ref, "starts_at_local": f"{FUTURE_THU}T19:10"}], key="g"),
                         422, "not_on_slot_grid")

    def test_different_restaurants(self):
        _, ny, _ = self.book(self.token, "ny", restaurant_id="r_ny", table_id="t_1",
                             starts_at_local=f"{next_weekday(date(2030, 1, 1), 'sun')}T01:00")
        self.assertError(self.move([{"reference": self.a["reference"]}, {"reference": ny["reference"]}]),
                         422, "validation_failed")

    def test_cancelled_and_cutoff(self):
        request("POST", f"/reservations/{self.b['reference']}/cancel", token=self.token)
        self.assertError(self.move([{"reference": self.a["reference"]}, {"reference": self.b["reference"]}]),
                         409, "reservation_cancelled")
        fx = fixture(reservations=[
            {"id": "res_past", "reference": "PAST01", "user_id": "u_ada", "restaurant_id": "r_anker",
             "table_id": "t_2", "starts_at_local": f"{PAST_THU}T19:00", "party_size": 2},
        ])
        self.reset(fx)
        token = self.login()
        self.assertError(self.move([{"reference": "PAST01", "party_size": 99}], token=token), 409, "cutoff_passed")

    def test_concurrent_moves_idempotent(self):
        moves = [{"reference": self.a["reference"], "table_id": "t_3"},
                 {"reference": self.b["reference"], "table_id": "t_2"}]
        with ThreadPoolExecutor(20) as pool:
            results = list(pool.map(lambda _: self.move(moves, key="race"), range(20)))
        statuses = sorted(r[0] for r in results)
        self.assertEqual(statuses.count(201), 1, statuses)
        self.assertEqual(statuses.count(200), 19, statuses)
        a = request("GET", f"/reservations/{self.a['reference']}", token=self.token)[1]
        self.assertEqual(a["table_id"], "t_3")


class TestExportImport(Base):
    def test_round_trip(self):
        token = self.login()
        _, res, _ = self.book(token, "k1")
        _, other, _ = self.book(token, "k2", table_id="t_3")
        _, moved, _ = request("POST", "/reservation-moves",
                              {"moves": [{"reference": other["reference"], "party_size": 5}]}, token=token, key="mv")
        request("POST", f"/reservations/{other['reference']}/cancel", token=token)
        self.book(token, "failed", table_id="t_1", party_size=9)  # 422, key stays reusable
        status, export, _ = request("GET", "/_test/export")
        self.assertEqual(status, 200)
        self.assertEqual((export["track"], export["format_version"]), ("tablekeeper", 1))
        self.assertIsInstance(export["state"], dict)
        snapshot = copy.deepcopy(export)
        # Later writes do not change the export snapshot.
        self.book(token, "k3", table_id="t_1")
        self.reset(fixture(users=[], restaurants=[], reservations=[]))
        for _ in range(2):  # repeated import is replacement, not merge
            self.assertEqual(request("POST", "/_test/import", snapshot)[0], 204)
        self.assertEqual(export, snapshot)
        # Token, reservations, statuses and timestamps survive.
        listed = request("GET", "/reservations", token=token)[1]["reservations"]
        self.assertEqual(len(listed), 2)
        self.assertEqual(request("GET", f"/reservations/{res['reference']}", token=token)[1], res)
        self.assertEqual(request("GET", f"/reservations/{other['reference']}", token=token)[1]["status"],
                         "cancelled")
        # Receipts replay.
        self.assertEqual(self.book(token, "k1"), (200, res, "application/json; charset=utf-8"))
        self.assertEqual(request("POST", "/reservation-moves",
                                 {"moves": [{"reference": other["reference"], "party_size": 5}]},
                                 token=token, key="mv")[:2], (200, moved))
        self.assertError(self.book(token, "k1", party_size=1), 409, "idempotency_key_reuse")
        # Failed key reusable; new ids never collide.
        status, fresh, _ = self.book(token, "failed", table_id="t_1", party_size=2)
        self.assertEqual(status, 201)
        self.assertNotIn(fresh["reservation_id"], {res["reservation_id"], other["reservation_id"]})
        self.assertNotIn(fresh["reference"], {res["reference"], other["reference"]})
        # Hashed-password login still works.
        self.login()
        # Reset clears imported state.
        self.reset(fixture())
        self.assertError(request("GET", "/reservations", token=token), 401, "unauthenticated")

    def test_import_rejects_invalid_without_change(self):
        token = self.login()
        _, res, _ = self.book(token, "k1")
        _, export, _ = request("GET", "/_test/export")
        bad_docs = [
            {}, {"track": "other", "format_version": 1, "state": export["state"]},
            {"track": "tablekeeper", "format_version": 2, "state": export["state"]},
            {"track": "tablekeeper", "format_version": 1},
            {"track": "tablekeeper", "format_version": 1, "state": {"users": "nope"}},
            {"track": "tablekeeper", "format_version": 1, "state": []},
        ]
        for doc in bad_docs:
            self.assertError(request("POST", "/_test/import", doc), 422, "validation_failed")
        self.assertError(request("POST", "/_test/import", raw="{oops"), 400, "malformed_request")
        self.assertEqual(request("GET", f"/reservations/{res['reference']}", token=token)[1], res)

    def test_import_removes_previous_data(self):
        self.reset(fixture(users=[{"id": "u_x", "email": "x@example.com", "password": "password1"}]))
        _, export, _ = request("GET", "/_test/export")
        self.reset(fixture())
        ada = self.login()
        self.assertEqual(request("POST", "/_test/import", export)[0], 204)
        self.assertError(request("GET", "/reservations", token=ada), 401, "unauthenticated")
        self.login("x@example.com", "password1")


if __name__ == "__main__":
    unittest.main()
