"""Service state and its validated construction from fixtures and exports."""

import re
from datetime import datetime, timezone

from . import passwords
from .errors import invalid
from .timeutil import WEEKDAYS, get_zone, parse_hhmm, parse_local, resolve

MAX_ID = 64
REFERENCE_RE = re.compile(r"[A-Z0-9]{6,12}")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_id(value):
    return isinstance(value, str) and 1 <= len(value) <= MAX_ID


def _require(condition, message):
    if not condition:
        raise invalid(message)


def now_utc_string():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Restaurant:
    """A restaurant in fixture shape plus the derived lookups booking needs."""

    __slots__ = ("public", "id", "zone", "slot", "duration", "cutoff", "hours", "tables", "table_order")

    def __init__(self, public):
        self.public = public
        self.id = public["id"]
        self.zone = get_zone(public["timezone"])
        self.slot = public["slot_minutes"]
        self.duration = public["reservation_duration_minutes"]
        self.cutoff = public["cancellation_cutoff_minutes"]
        self.hours = {}
        for entry in public["opening_hours"]:
            self.hours.setdefault(entry["weekday"], []).append(
                (parse_hhmm(entry["opens"]), parse_hhmm(entry["closes"]))
            )
        self.tables = {t["id"]: t for t in public["tables"]}
        self.table_order = [t["id"] for t in public["tables"]]


def parse_restaurant(raw):
    """Validate one fixture restaurant and return its normalized public shape."""
    _require(isinstance(raw, dict), "restaurant must be an object")
    _require(_is_id(raw.get("id")), "restaurant id must be a string of 1..64 characters")
    name = raw.get("name", raw["id"])
    _require(isinstance(name, str), "restaurant name must be a string")
    _require(get_zone(raw.get("timezone")) is not None, "timezone must be a valid IANA zone")
    for field, minimum in (("slot_minutes", 1), ("reservation_duration_minutes", 1)):
        _require(_is_int(raw.get(field)) and minimum <= raw[field] <= 100000, f"{field} must be a positive integer")
    cutoff = raw.get("cancellation_cutoff_minutes", 0)
    _require(_is_int(cutoff) and 0 <= cutoff <= 10_000_000, "cancellation_cutoff_minutes must be a non-negative integer")
    hours_raw = raw.get("opening_hours", [])
    _require(isinstance(hours_raw, list), "opening_hours must be a list")
    hours = []
    for entry in hours_raw:
        _require(isinstance(entry, dict), "opening_hours entries must be objects")
        _require(entry.get("weekday") in WEEKDAYS, "weekday must be one of mon..sun")
        opens, closes = parse_hhmm(entry.get("opens")), parse_hhmm(entry.get("closes"))
        _require(opens is not None and closes is not None, "opens/closes must be HH:MM")
        _require(opens < closes, "closes must be later than opens")
        hours.append({"weekday": entry["weekday"], "opens": entry["opens"], "closes": entry["closes"]})
    tables_raw = raw.get("tables", [])
    _require(isinstance(tables_raw, list), "tables must be a list")
    tables, seen = [], set()
    for table in tables_raw:
        _require(isinstance(table, dict), "tables entries must be objects")
        _require(_is_id(table.get("id")), "table id must be a string of 1..64 characters")
        _require(table["id"] not in seen, "duplicate table id")
        seen.add(table["id"])
        _require(_is_int(table.get("capacity")) and table["capacity"] >= 0, "capacity must be a non-negative integer")
        out = {"id": table["id"]}
        if "label" in table:
            out["label"] = table["label"]
        out["capacity"] = table["capacity"]
        tables.append(out)
    return {
        "id": raw["id"],
        "name": name,
        "timezone": raw["timezone"],
        "slot_minutes": raw["slot_minutes"],
        "reservation_duration_minutes": raw["reservation_duration_minutes"],
        "cancellation_cutoff_minutes": cutoff,
        "opening_hours": hours,
        "tables": tables,
    }


def valid_email(email):
    if not isinstance(email, str) or email.count("@") != 1 or len(email) > 320:
        return False
    local, domain = email.split("@")
    return bool(local) and bool(domain) and not any(c.isspace() for c in email)


class State:
    """All mutable service data. Only touched while holding the service lock."""

    def __init__(self):
        self.users = {}          # id -> {id, email, display_name, password_hash}
        self.emails = {}         # lower-cased email -> user id
        self.tokens = {}         # token -> user id
        self.restaurants = {}    # id -> Restaurant (insertion order = fixture order)
        self.reservations = {}   # id -> record
        self.references = {}     # reference -> reservation id
        self.idempotency = {}    # (user, method, path, key) -> {body, status, response}
        self.next_user = 1
        self.next_reservation = 1

    # -- construction helpers -------------------------------------------------

    def _add_restaurants(self, raw_list):
        _require(isinstance(raw_list, list), "restaurants must be a list")
        for raw in raw_list:
            public = parse_restaurant(raw)
            _require(public["id"] not in self.restaurants, "duplicate restaurant id")
            self.restaurants[public["id"]] = Restaurant(public)

    def _add_user(self, user_id, email, display_name, password_hash):
        _require(_is_id(user_id), "user id must be a string of 1..64 characters")
        _require(isinstance(email, str) and email, "email must be a non-empty string")
        _require(isinstance(display_name, str), "display_name must be a string")
        _require(user_id not in self.users, "duplicate user id")
        _require(email.lower() not in self.emails, "duplicate email")
        self.users[user_id] = {
            "id": user_id, "email": email, "display_name": display_name, "password_hash": password_hash,
        }
        self.emails[email.lower()] = user_id

    def _add_reservation(self, raw, allow_status):
        _require(isinstance(raw, dict), "reservation must be an object")
        _require(_is_id(raw.get("id")), "reservation id must be a string of 1..64 characters")
        _require(raw["id"] not in self.reservations, "duplicate reservation id")
        reference = raw.get("reference")
        _require(isinstance(reference, str) and REFERENCE_RE.fullmatch(reference),
                 "reference must be 6 to 12 characters of A-Z0-9")
        _require(reference not in self.references, "duplicate reference")
        _require(raw.get("user_id") in self.users, "reservation user_id is unknown")
        restaurant = self.restaurants.get(raw.get("restaurant_id")) if isinstance(raw.get("restaurant_id"), str) else None
        _require(restaurant is not None, "reservation restaurant_id is unknown")
        _require(isinstance(raw.get("table_id"), str) and raw["table_id"] in restaurant.tables,
                 "reservation table_id is unknown")
        party = raw.get("party_size")
        _require(_is_int(party) and party >= 1, "party_size must be a positive integer")
        naive = parse_local(raw.get("starts_at_local"))
        _require(naive is not None, "starts_at_local must be YYYY-MM-DDTHH:MM")
        start = resolve(naive, restaurant.zone)
        _require(start is not None, "starts_at_local does not exist in the restaurant timezone")
        status = "confirmed"
        if allow_status:
            status = raw.get("status")
            _require(status in ("confirmed", "cancelled"), "status must be confirmed or cancelled")
        created_at = raw.get("created_at")
        if created_at is None and not allow_status:
            created_at = now_utc_string()
        _require(isinstance(created_at, str) and len(created_at) <= 64, "created_at must be a string")
        self.reservations[raw["id"]] = {
            "id": raw["id"],
            "reference": reference,
            "user_id": raw["user_id"],
            "restaurant_id": restaurant.id,
            "table_id": raw["table_id"],
            "party_size": party,
            "status": status,
            "starts_at_local": raw["starts_at_local"],
            "start": start,
            "created_at": created_at,
        }
        self.references[reference] = raw["id"]

    # -- fixtures --------------------------------------------------------------

    @classmethod
    def from_fixture(cls, fixture, hasher):
        """Build state from a reset fixture. `hasher` maps a list of passwords to hashes."""
        _require(isinstance(fixture, dict), "fixture must be an object")
        state = cls()
        users = fixture.get("users", [])
        _require(isinstance(users, list), "users must be a list")
        for user in users:
            _require(isinstance(user, dict), "users entries must be objects")
            _require(isinstance(user.get("password"), str), "user password must be a string")
            display_name = user.get("display_name", "")
            state._add_user(user.get("id"), user.get("email"), display_name, None)
        state._add_restaurants(fixture.get("restaurants", []))
        reservations = fixture.get("reservations", [])
        _require(isinstance(reservations, list), "reservations must be a list")
        for raw in reservations:
            state._add_reservation(raw, allow_status=False)
        # Hash last so an invalid fixture fails fast without paying for scrypt.
        hashes = hasher([user["password"] for user in users])
        for user, digest in zip(users, hashes):
            state.users[user["id"]]["password_hash"] = digest
        return state

    # -- export / import -------------------------------------------------------

    def export(self):
        return {
            "users": [dict(u) for u in self.users.values()],
            "tokens": [[token, uid] for token, uid in self.tokens.items()],
            "restaurants": [r.public for r in self.restaurants.values()],
            "reservations": [
                {k: v for k, v in rec.items() if k != "start"} for rec in self.reservations.values()
            ],
            "idempotency": [
                {"key": list(key), "body": rec["body"], "status": rec["status"], "response": rec["response"]}
                for key, rec in self.idempotency.items()
            ],
            "next_user": self.next_user,
            "next_reservation": self.next_reservation,
        }

    @classmethod
    def from_export(cls, data):
        _require(isinstance(data, dict), "state must be an object")
        state = cls()
        users = data.get("users")
        _require(isinstance(users, list), "state.users must be a list")
        for user in users:
            _require(isinstance(user, dict), "state.users entries must be objects")
            _require(passwords.is_valid_hash(user.get("password_hash")), "invalid password hash")
            state._add_user(user.get("id"), user.get("email"), user.get("display_name"), user["password_hash"])
        tokens = data.get("tokens")
        _require(isinstance(tokens, list), "state.tokens must be a list")
        for pair in tokens:
            _require(isinstance(pair, list) and len(pair) == 2, "state.tokens entries must be pairs")
            token, uid = pair
            _require(isinstance(token, str) and token and len(token) <= 512, "invalid token")
            _require(uid in state.users, "token refers to an unknown user")
            state.tokens[token] = uid
        state._add_restaurants(data.get("restaurants"))
        reservations = data.get("reservations")
        _require(isinstance(reservations, list), "state.reservations must be a list")
        for raw in reservations:
            state._add_reservation(raw, allow_status=True)
        receipts = data.get("idempotency")
        _require(isinstance(receipts, list), "state.idempotency must be a list")
        for rec in receipts:
            _require(isinstance(rec, dict), "idempotency entries must be objects")
            key = rec.get("key")
            _require(isinstance(key, list) and len(key) == 4 and all(isinstance(k, str) for k in key),
                     "idempotency key must be four strings")
            _require(isinstance(rec.get("body"), str), "idempotency body must be a string")
            _require(_is_int(rec.get("status")) and 200 <= rec["status"] < 300, "idempotency status must be 2xx")
            _require("response" in rec, "idempotency response missing")
            state.idempotency[tuple(key)] = {"body": rec["body"], "status": rec["status"], "response": rec["response"]}
        for field in ("next_user", "next_reservation"):
            _require(_is_int(data.get(field)) and data[field] >= 1, f"state.{field} must be a positive integer")
        state.next_user = data["next_user"]
        state.next_reservation = data["next_reservation"]
        return state
