"""Business operations. Every read and write of State happens under one lock."""

import json
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import passwords
from .errors import ApiError, invalid, malformed, not_found, unauthenticated
from .state import State, now_utc_string, valid_email
from .timeutil import (
    at_minutes, format_instant, local_string, parse_date, parse_local, resolve, resolve_lenient,
    weekday_name,
)

REFERENCE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
REFERENCE_LENGTH = 8
MAX_KEY_LENGTH = 255
MAX_MOVES = 8
BOOKING_FIELDS = ("table_id", "starts_at_local", "party_size")

_hash_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="scrypt")


def _hash_many(plaintexts):
    if len(plaintexts) <= 1:
        return [passwords.hash_seed_password(p) for p in plaintexts]
    return list(_hash_pool.map(passwords.hash_seed_password, plaintexts))


def canonical(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_party(value):
    return _is_int(value) and value >= 1


def _check_string_fields(body, fields):
    """400 for a present field whose JSON type is not a string."""
    for field in fields:
        if field in body and not isinstance(body[field], str):
            raise malformed(f"{field} must be a string")


class Service:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = State()

    # -- test control ----------------------------------------------------------

    def reset(self, fixture):
        new_state = State.from_fixture(fixture, _hash_many)
        with self.lock:
            self.state = new_state

    def export(self):
        with self.lock:
            snapshot = json.dumps(self.state.export(), ensure_ascii=True)
        return {"track": "tablekeeper", "format_version": 1, "state": json.loads(snapshot)}

    def import_(self, document):
        if not isinstance(document, dict):
            raise invalid("import body must be an object")
        if document.get("track") != "tablekeeper":
            raise invalid("track must be 'tablekeeper'")
        version = document.get("format_version")
        if not _is_int(version) or version != 1:
            raise invalid("format_version must be 1")
        if "state" not in document:
            raise invalid("state is required")
        new_state = State.from_export(document["state"])
        with self.lock:
            self.state = new_state

    # -- auth ------------------------------------------------------------------

    def signup(self, body):
        _check_string_fields(body, ("email", "password", "display_name"))
        email, password = body.get("email"), body.get("password")
        if email is None or password is None:
            raise invalid("email and password are required")
        if not valid_email(email):
            raise invalid("email must be of the form local@domain")
        if len(password) < 8:
            raise invalid("password must be at least 8 characters")
        display_name = body.get("display_name")
        if display_name is None:
            display_name = email.split("@")[0]
        with self.lock:
            if email.lower() in self.state.emails:
                raise ApiError(409, "email_taken", "Email already registered.")
        digest = passwords.hash_password(password)
        with self.lock:
            state = self.state
            if email.lower() in state.emails:
                raise ApiError(409, "email_taken", "Email already registered.")
            user_id = self._new_user_id(state)
            state.users[user_id] = {
                "id": user_id, "email": email, "display_name": display_name, "password_hash": digest,
            }
            state.emails[email.lower()] = user_id
            token = self._new_token(state, user_id)
        return {"user_id": user_id, "display_name": display_name, "token": token}

    def login(self, body):
        _check_string_fields(body, ("email", "password"))
        email, password = body.get("email"), body.get("password")
        if email is None or password is None:
            raise invalid("email and password are required")
        with self.lock:
            state = self.state
            user = state.users.get(state.emails.get(email.lower(), ""))
        if user is None or not passwords.verify_password(password, user["password_hash"]):
            raise unauthenticated("Wrong email or password.")
        with self.lock:
            if self.state is not state or user["id"] not in state.users:
                raise unauthenticated("Wrong email or password.")
            token = self._new_token(state, user["id"])
        return {"user_id": user["id"], "display_name": user["display_name"], "token": token}

    @staticmethod
    def _new_user_id(state):
        while True:
            candidate = f"u_{state.next_user}"
            state.next_user += 1
            if candidate not in state.users:
                return candidate

    @staticmethod
    def _new_token(state, user_id):
        token = secrets.token_urlsafe(32)
        state.tokens[token] = user_id
        return token

    def authenticate(self, token):
        with self.lock:
            return self._user(self.state, token)

    @staticmethod
    def _user(state, token):
        user_id = state.tokens.get(token) if token else None
        if user_id is None:
            raise unauthenticated("Missing or unknown bearer token.")
        return user_id

    # -- restaurants & availability -------------------------------------------

    def list_restaurants(self):
        with self.lock:
            return {
                "restaurants": [
                    {"id": r.id, "name": r.public["name"], "timezone": r.public["timezone"]}
                    for r in self.state.restaurants.values()
                ]
            }

    def get_restaurant(self, restaurant_id):
        with self.lock:
            restaurant = self.state.restaurants.get(restaurant_id)
            if restaurant is None:
                raise not_found("No such restaurant.")
            return json.loads(json.dumps(restaurant.public))

    def availability(self, query):
        def first(name):
            values = query.get(name)
            return values[0] if values else None

        restaurant_id, date_raw, party_raw = first("restaurant_id"), first("date"), first("party_size")
        if restaurant_id is None or date_raw is None or party_raw is None:
            raise invalid("restaurant_id, date and party_size are required")
        day = parse_date(date_raw)
        if day is None:
            raise invalid("date must be a valid YYYY-MM-DD")
        if not party_raw or not party_raw.isascii() or not party_raw.isdigit():
            raise invalid("party_size must be written as plain decimal digits")
        party = int(party_raw)
        if party < 1:
            raise invalid("party_size must be at least 1")
        with self.lock:
            state = self.state
            restaurant = state.restaurants.get(restaurant_id)
            if restaurant is None:
                raise not_found("No such restaurant.")
            zone, duration = restaurant.zone, restaurant.duration * 60
            slots = {}
            for opens, closes in restaurant.hours.get(weekday_name(day), []):
                close_at = resolve_lenient(at_minutes(day, closes), zone)
                minute = opens
                while minute < closes:
                    naive = at_minutes(day, minute)
                    start = resolve(naive, zone)
                    if start is not None and start + duration <= close_at and minute not in slots:
                        slots[minute] = (naive, start)
                    minute += restaurant.slot
            busy = {}
            if slots:
                lo = min(s for _, s in slots.values()) - duration
                hi = max(s for _, s in slots.values()) + duration
                for rec in state.reservations.values():
                    if (rec["restaurant_id"] == restaurant.id and rec["status"] == "confirmed"
                            and lo < rec["start"] < hi):
                        busy.setdefault(rec["table_id"], []).append(rec["start"])
            eligible = [t for t in restaurant.table_order if restaurant.tables[t]["capacity"] >= party]
            out = []
            for minute in sorted(slots):
                naive, start = slots[minute]
                free = [
                    t for t in eligible
                    if not any(other < start + duration and start < other + duration for other in busy.get(t, ()))
                ]
                out.append({
                    "starts_at_local": local_string(naive),
                    "starts_at": format_instant(start, zone),
                    "available_table_ids": free,
                })
        return {"restaurant_id": restaurant.id, "date": day.isoformat(), "timezone": restaurant.public["timezone"],
                "slots": out}

    # -- reservations: helpers -------------------------------------------------

    @staticmethod
    def render(state, rec):
        restaurant = state.restaurants[rec["restaurant_id"]]
        return {
            "reservation_id": rec["id"],
            "reference": rec["reference"],
            "restaurant_id": rec["restaurant_id"],
            "table_id": rec["table_id"],
            "party_size": rec["party_size"],
            "status": rec["status"],
            "starts_at_local": rec["starts_at_local"],
            "starts_at": format_instant(rec["start"], restaurant.zone),
            "ends_at": format_instant(rec["start"] + restaurant.duration * 60, restaurant.zone),
            "created_at": rec["created_at"],
        }

    @staticmethod
    def _slot_start(restaurant, local):
        """Validate a booking start against DST, opening hours and the grid; return the epoch."""
        naive = parse_local(local)
        if naive is None:
            raise invalid("starts_at_local must be a bare local YYYY-MM-DDTHH:MM")
        start = resolve(naive, restaurant.zone)
        if start is None:
            raise ApiError(422, "invalid_local_time", "That local time does not exist in the restaurant's timezone.")
        minute = naive.hour * 60 + naive.minute
        containing = [(o, c) for o, c in restaurant.hours.get(weekday_name(naive.date()), []) if o <= minute < c]
        if not containing:
            raise ApiError(422, "outside_opening_hours", "The restaurant is not open at that time.")
        on_grid = [(o, c) for o, c in containing if (minute - o) % restaurant.slot == 0]
        if not on_grid:
            raise ApiError(422, "not_on_slot_grid", "starts_at_local is not on the slot grid.")
        end = start + restaurant.duration * 60
        if not any(end <= resolve_lenient(at_minutes(naive.date(), c), restaurant.zone) for _, c in on_grid):
            raise ApiError(422, "outside_opening_hours", "The reservation would end after closing time.")
        return start

    @staticmethod
    def _check_cutoff(restaurant, rec):
        if time.time() >= rec["start"] - restaurant.cutoff * 60:
            raise ApiError(409, "cutoff_passed", "The cancellation/amendment cutoff has passed.")

    def _plan(self, state, restaurant, table_id, local, party):
        """Validate a complete booking (minus overlap); return (table_id, local, start, party)."""
        if table_id not in restaurant.tables:
            raise not_found("No such table at this restaurant.")
        start = self._slot_start(restaurant, local)
        if party > restaurant.tables[table_id]["capacity"]:
            raise ApiError(422, "party_exceeds_capacity", "party_size exceeds the table's capacity.")
        return {"table_id": table_id, "starts_at_local": local, "start": start, "party_size": party}

    @staticmethod
    def _conflicts(state, restaurant, table_id, start, exclude):
        duration = restaurant.duration * 60
        for rec in state.reservations.values():
            if (rec["status"] == "confirmed" and rec["table_id"] == table_id
                    and rec["restaurant_id"] == restaurant.id and rec["id"] not in exclude
                    and rec["start"] < start + duration and start < rec["start"] + duration):
                return True
        return False

    @staticmethod
    def _owned(state, user_id, reference):
        res_id = state.references.get(reference)
        rec = state.reservations.get(res_id) if res_id is not None else None
        if rec is None or rec["user_id"] != user_id:
            raise not_found("No such reservation.")
        return rec

    def _idempotent(self, state, user_id, method, path, key, body, operation):
        """Run `operation` once per (user, method, path, key); replay stored 2xx responses."""
        if key is None or key == "":
            raise ApiError(400, "missing_idempotency_key", "Idempotency-Key header is required.")
        if len(key) > MAX_KEY_LENGTH:
            raise invalid("Idempotency-Key must be 1 to 255 characters.")
        record_key = (user_id, method, path, key)
        body_text = canonical(body)
        stored = state.idempotency.get(record_key)
        if stored is not None:
            if stored["body"] != body_text:
                raise ApiError(409, "idempotency_key_reuse", "Idempotency-Key was used with a different body.")
            return 200, json.loads(json.dumps(stored["response"]))
        status, response = operation()
        state.idempotency[record_key] = {"body": body_text, "status": status,
                                         "response": json.loads(json.dumps(response))}
        return status, response

    def _new_reference(self, state):
        while True:
            ref = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_LENGTH))
            if ref not in state.references:
                return ref

    @staticmethod
    def _new_reservation_id(state):
        while True:
            candidate = f"res_{state.next_reservation}"
            state.next_reservation += 1
            if candidate not in state.reservations:
                return candidate

    # -- reservations: endpoints -----------------------------------------------

    def create_reservation(self, token, key, path, body):
        with self.lock:
            state = self.state
            user_id = self._user(state, token)
            return self._idempotent(state, user_id, "POST", path, key, body,
                                    lambda: self._create(state, user_id, body))

    def _create(self, state, user_id, body):
        _check_string_fields(body, ("restaurant_id", "table_id", "starts_at_local"))
        for field in ("restaurant_id", "table_id", "starts_at_local", "party_size"):
            if field not in body:
                raise invalid(f"{field} is required")
        if not _valid_party(body["party_size"]):
            raise invalid("party_size must be an integer of at least 1")
        if parse_local(body["starts_at_local"]) is None:
            raise invalid("starts_at_local must be a bare local YYYY-MM-DDTHH:MM")
        restaurant = state.restaurants.get(body["restaurant_id"])
        if restaurant is None:
            raise not_found("No such restaurant.")
        plan = self._plan(state, restaurant, body["table_id"], body["starts_at_local"], body["party_size"])
        if self._conflicts(state, restaurant, plan["table_id"], plan["start"], ()):
            raise ApiError(409, "table_unavailable", "The table is taken for an overlapping time.")
        rec = {
            "id": self._new_reservation_id(state),
            "reference": self._new_reference(state),
            "user_id": user_id,
            "restaurant_id": restaurant.id,
            "table_id": plan["table_id"],
            "party_size": plan["party_size"],
            "status": "confirmed",
            "starts_at_local": plan["starts_at_local"],
            "start": plan["start"],
            "created_at": now_utc_string(),
        }
        state.reservations[rec["id"]] = rec
        state.references[rec["reference"]] = rec["id"]
        return 201, self.render(state, rec)

    def list_reservations(self, token):
        with self.lock:
            state = self.state
            user_id = self._user(state, token)
            mine = [(i, rec) for i, rec in enumerate(state.reservations.values()) if rec["user_id"] == user_id]
            mine.sort(key=lambda pair: (pair[1]["start"], pair[0]), reverse=True)
            return {"reservations": [self.render(state, rec) for _, rec in mine]}

    def get_reservation(self, token, reference):
        with self.lock:
            state = self.state
            user_id = self._user(state, token)
            return self.render(state, self._owned(state, user_id, reference))

    def cancel(self, token, reference):
        with self.lock:
            state = self.state
            user_id = self._user(state, token)
            rec = self._owned(state, user_id, reference)
            if rec["status"] == "cancelled":
                return self.render(state, rec)
            self._check_cutoff(state.restaurants[rec["restaurant_id"]], rec)
            rec["status"] = "cancelled"
            return self.render(state, rec)

    def _amendment(self, state, rec, item):
        """Validate one amendment against `rec`; return the planned new values (no overlap check)."""
        if rec["status"] == "cancelled":
            raise ApiError(409, "reservation_cancelled", "The reservation is cancelled.")
        restaurant = state.restaurants[rec["restaurant_id"]]
        self._check_cutoff(restaurant, rec)
        if "party_size" in item and not _valid_party(item["party_size"]):
            raise invalid("party_size must be an integer of at least 1")
        if "starts_at_local" in item and parse_local(item["starts_at_local"]) is None:
            raise invalid("starts_at_local must be a bare local YYYY-MM-DDTHH:MM")
        return self._plan(
            state, restaurant,
            item.get("table_id", rec["table_id"]),
            item.get("starts_at_local", rec["starts_at_local"]),
            item.get("party_size", rec["party_size"]),
        )

    def patch(self, token, reference, body):
        with self.lock:
            state = self.state
            user_id = self._user(state, token)
            _check_string_fields(body, ("table_id", "starts_at_local"))
            rec = self._owned(state, user_id, reference)
            plan = self._amendment(state, rec, body)
            restaurant = state.restaurants[rec["restaurant_id"]]
            if self._conflicts(state, restaurant, plan["table_id"], plan["start"], (rec["id"],)):
                raise ApiError(409, "table_unavailable", "The table is taken for an overlapping time.")
            rec.update(plan)
            return self.render(state, rec)

    def moves(self, token, key, path, body):
        with self.lock:
            state = self.state
            user_id = self._user(state, token)
            return self._idempotent(state, user_id, "POST", path, key, body,
                                    lambda: self._moves(state, user_id, body))

    def _moves(self, state, user_id, body):
        moves = body.get("moves")
        if not isinstance(moves, list) or not 1 <= len(moves) <= MAX_MOVES:
            raise invalid(f"moves must be a list of 1..{MAX_MOVES} objects")
        seen = set()
        for item in moves:
            if not isinstance(item, dict) or not isinstance(item.get("reference"), str):
                raise invalid("each move must be an object with a string reference")
            if item["reference"] in seen:
                raise invalid("move references must be distinct")
            seen.add(item["reference"])
        for item in moves:
            _check_string_fields(item, ("table_id", "starts_at_local"))
        restaurant_id = None
        planned = []
        for item in moves:
            rec = self._owned(state, user_id, item["reference"])
            if restaurant_id is None:
                restaurant_id = rec["restaurant_id"]
            elif rec["restaurant_id"] != restaurant_id:
                raise invalid("all moved reservations must belong to the same restaurant")
            planned.append((rec, self._amendment(state, rec, item)))
        restaurant = state.restaurants[restaurant_id]
        duration = restaurant.duration * 60
        listed = {rec["id"] for rec, _ in planned}
        for i, (rec, plan) in enumerate(planned):
            if self._conflicts(state, restaurant, plan["table_id"], plan["start"], listed):
                raise ApiError(409, "table_unavailable", "A moved reservation overlaps an existing booking.")
            for _, other in planned[:i]:
                if (other["table_id"] == plan["table_id"] and other["start"] < plan["start"] + duration
                        and plan["start"] < other["start"] + duration):
                    raise ApiError(409, "table_unavailable", "Moved reservations overlap each other.")
        for rec, plan in planned:
            rec.update(plan)
        return 201, {"reservations": [self.render(state, rec) for rec, _ in planned]}
