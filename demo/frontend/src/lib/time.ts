export function fmtTime(iso: string, tz: string) {
  return new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit" }).format(
    new Date(iso),
  );
}

export function fmtDateTime(iso: string, tz: string) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: tz,
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(new Date(iso));
}

/** Today's calendar date (YYYY-MM-DD) as seen in the given time zone. */
export function todayIn(tz: string) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz }).format(new Date());
}

/** Parse a Postgres tstzrange like ["2026-10-05 17:00:00+00","...") */
export function rangeStart(slot: string) {
  const m = slot.match(/^[[(]"?([^",]+)"?,/);
  return m?.[1] ? new Date(m[1].replace(" ", "T")).toISOString() : slot;
}
