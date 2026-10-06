"""Local-time handling for restaurants: parsing, DST resolution and formatting."""

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_LOCAL_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_HHMM_RE = re.compile(r"[0-9]{2}:[0-9]{2}")

_zone_cache = {}


def get_zone(name):
    """Return a ZoneInfo for an IANA name, or None if it is not a valid zone."""
    if not isinstance(name, str) or not name or len(name) > 255:
        return None
    zone = _zone_cache.get(name)
    if zone is None:
        try:
            zone = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return None
        _zone_cache[name] = zone
    return zone


def parse_local(value):
    """Parse a bare 'YYYY-MM-DDTHH:MM' into a naive datetime, or None."""
    if not isinstance(value, str) or not _LOCAL_RE.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def parse_date(value):
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_hhmm(value):
    """Parse 'HH:MM' (24-hour) into minutes after midnight, or None."""
    if not isinstance(value, str) or not _HHMM_RE.fullmatch(value):
        return None
    hours, minutes = int(value[:2]), int(value[3:])
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


def resolve(naive, zone):
    """Resolve a naive local datetime to a UTC epoch second.

    Returns None when the wall-clock time does not exist (spring-forward gap).
    Ambiguous times (fall-back) resolve to the first occurrence (fold=0).
    """
    aware = naive.replace(tzinfo=zone, fold=0)
    utc = aware.astimezone(timezone.utc)
    if utc.astimezone(zone).replace(tzinfo=None) != naive:
        return None
    return int(utc.timestamp())


def resolve_lenient(naive, zone):
    """Resolve to an instant even inside a gap (used for closing-time bounds)."""
    aware = naive.replace(tzinfo=zone, fold=0)
    return int(aware.astimezone(timezone.utc).timestamp())


def format_instant(epoch, zone):
    """RFC 3339 with an explicit numeric offset in the given zone."""
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(zone)
    return dt.isoformat(timespec="seconds")


def format_utc(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


def local_string(naive):
    return naive.strftime("%Y-%m-%dT%H:%M")


def at_minutes(day, minutes):
    return datetime(day.year, day.month, day.day) + timedelta(minutes=minutes)


def weekday_name(day):
    return WEEKDAYS[day.weekday()]
