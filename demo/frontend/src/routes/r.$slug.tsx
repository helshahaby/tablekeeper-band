import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  backend,
  BackendError,
  describeError,
  localTime,
  tableLabel,
  useSession,
  type Reservation,
  type Slot,
} from "@/lib/backend";
import { fmtDateTime, todayIn } from "@/lib/time";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export const Route = createFileRoute("/r/$slug")({
  head: () => ({
    meta: [
      { title: "Book a table — Tablekeeper" },
      {
        name: "description",
        content: "See open tables and reserve in the restaurant's local time.",
      },
      { property: "og:title", content: "Book a table — Tablekeeper" },
      {
        property: "og:description",
        content: "See open tables and reserve in the restaurant's local time.",
      },
    ],
  }),
  component: RestaurantPage,
});

function RestaurantPage() {
  // The route param is the backend restaurant id.
  const { slug: restaurantId } = Route.useParams();
  const session = useSession();
  const qc = useQueryClient();

  const { data: r, error: restaurantError } = useQuery({
    queryKey: ["backend", "restaurant", restaurantId],
    queryFn: () => backend.getRestaurant(restaurantId),
  });

  const [date, setDate] = useState("");
  const [party, setParty] = useState(2);
  // Selected start (local wall time); the slot itself is read from the latest availability.
  const [slotTime, setSlotTime] = useState<string | null>(null);
  const [tableId, setTableId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Reservation | null>(null);
  // One key per booking attempt: a retried click after a network failure reuses it,
  // so the backend replays the original reservation instead of creating a second one.
  const keyRef = useRef("");

  useEffect(() => {
    if (r && !date) setDate(todayIn(r.timezone));
  }, [r, date]);
  useEffect(() => {
    keyRef.current = crypto.randomUUID();
  }, [slotTime, tableId, party, date]);

  const {
    data: availability,
    isLoading,
    error: availabilityError,
  } = useQuery({
    enabled: Boolean(r && date && party >= 1),
    queryKey: ["backend", "availability", restaurantId, date, party],
    queryFn: () => backend.availability(restaurantId, date, party),
    // Shown availability is a hint; the booking request is what the backend checks.
    refetchOnWindowFocus: false,
  });

  if (restaurantError)
    return (
      <p role="alert" className="text-sm text-destructive">
        Could not load restaurant: {describeError(restaurantError)}
      </p>
    );
  if (!r) return <p className="font-mono text-sm text-muted-foreground">Loading…</p>;

  const slot = availability?.slots.find((s) => s.starts_at_local === slotTime) ?? null;
  const tableFree = Boolean(slot && tableId && slot.available_table_ids.includes(tableId));
  const maxCapacity = Math.max(1, ...r.tables.map((t) => t.capacity));
  const eligible = r.tables.filter((t) => t.capacity >= party);

  function pickSlot(s: Slot) {
    setSlotTime(s.starts_at_local);
    setTableId(s.available_table_ids[0] ?? null);
    setError(null);
  }

  async function confirm() {
    if (!slot || !tableId || !r) return;
    setBusy(true);
    setError(null);
    try {
      const res = await backend.createReservation(
        {
          restaurant_id: r.id,
          table_id: tableId,
          starts_at_local: slot.starts_at_local,
          party_size: party,
        },
        keyRef.current,
      );
      setDone(res);
      setSlotTime(null);
      setTableId(null);
      keyRef.current = crypto.randomUUID();
      toast.success(`Table booked — reference ${res.reference}`);
    } catch (err) {
      const message = describeError(err);
      setError(message);
      toast.error(message);
      // A rejected key is not stored by the backend, so only network failures keep it for retry.
      if (!(err instanceof BackendError && err.status === 0)) keyRef.current = crypto.randomUUID();
    } finally {
      setBusy(false);
      qc.invalidateQueries({ queryKey: ["backend", "availability"] });
      qc.invalidateQueries({ queryKey: ["backend", "reservations"] });
    }
  }

  return (
    <div className="grid gap-10 lg:grid-cols-[1fr_360px]">
      <div>
        <Link to="/" className="font-mono text-xs text-muted-foreground hover:text-primary">
          ← all restaurants
        </Link>
        <h1 className="mt-3 text-5xl font-bold tracking-tight">{r.name}</h1>
        <p className="mt-2 font-mono text-xs text-muted-foreground">
          {r.timezone} · {r.slot_minutes}-min slots · {r.reservation_duration_minutes}-min
          reservations · {r.tables.length} tables
        </p>
        <p className="mt-2 font-mono text-xs text-muted-foreground">
          {r.opening_hours.map((h) => `${h.weekday} ${h.opens}–${h.closes}`).join(" · ") ||
            "No opening hours"}
        </p>

        <div className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor="date">Date (local)</Label>
            <Input
              id="date"
              type="date"
              value={date}
              onChange={(e) => {
                setDate(e.target.value);
                setSlotTime(null);
              }}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="party">Party</Label>
            <Input
              id="party"
              type="number"
              min={1}
              max={maxCapacity}
              value={party}
              onChange={(e) => {
                setParty(Math.max(1, Math.min(maxCapacity, Number(e.target.value) || 1)));
                setSlotTime(null);
              }}
            />
          </div>
        </div>

        <h2 className="mt-8 font-mono text-xs uppercase tracking-widest text-muted-foreground">
          Times shown in {r.timezone}
        </h2>
        <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-5">
          {isLoading && (
            <p className="col-span-full text-sm text-muted-foreground">Checking tables…</p>
          )}
          {availabilityError && (
            <p role="alert" className="col-span-full text-sm text-destructive">
              {describeError(availabilityError)}
            </p>
          )}
          {availability?.slots.length === 0 && (
            <p className="col-span-full text-sm text-muted-foreground">Closed on this date.</p>
          )}
          {availability?.slots.map((s) => {
            const free = s.available_table_ids.length;
            const sel = slot?.starts_at_local === s.starts_at_local;
            return (
              <button
                key={s.starts_at_local}
                disabled={free === 0}
                onClick={() => pickSlot(s)}
                className={`border px-2 py-3 font-mono text-sm transition-colors ${
                  sel
                    ? "border-primary bg-primary text-primary-foreground"
                    : free === 0
                      ? "cursor-not-allowed text-muted-foreground line-through opacity-50"
                      : "bg-card hover:border-primary"
                }`}
              >
                {localTime(s.starts_at_local)}
                <span className="block text-[10px] opacity-70">
                  {free === 0 ? "full" : `${free} free`}
                </span>
              </button>
            );
          })}
        </div>

        {slot && (
          <>
            <h2 className="mt-8 font-mono text-xs uppercase tracking-widest text-muted-foreground">
              Table for {party} at {localTime(slot.starts_at_local)}
            </h2>
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {eligible.map((t) => {
                const free = slot.available_table_ids.includes(t.id);
                const sel = tableId === t.id;
                return (
                  <button
                    key={t.id}
                    disabled={!free}
                    onClick={() => setTableId(t.id)}
                    className={`border px-2 py-3 text-left font-mono text-sm transition-colors ${
                      sel
                        ? "border-primary bg-primary text-primary-foreground"
                        : !free
                          ? "cursor-not-allowed text-muted-foreground opacity-50"
                          : "bg-card hover:border-primary"
                    }`}
                  >
                    Table {t.label ?? t.id}
                    <span className="block text-[10px] opacity-70">
                      {t.capacity} seats · {free ? "free" : "taken"}
                    </span>
                  </button>
                );
              })}
            </div>
          </>
        )}
      </div>

      <aside className="h-fit space-y-4 border bg-card p-6">
        {done && (
          <div>
            <p className="font-mono text-xs uppercase tracking-widest text-success">
              Confirmed by backend
            </p>
            <p className="mt-3 text-lg font-bold">{fmtDateTime(done.starts_at, r.timezone)}</p>
            <p className="text-sm text-muted-foreground">
              Table {tableLabel(r, done.table_id)} · {done.party_size} guests · ref{" "}
              <span className="font-mono">{done.reference}</span>
            </p>
            <Link to="/bookings" className="mt-4 inline-block text-sm text-primary underline">
              See my bookings
            </Link>
            <Button variant="secondary" className="mt-4 w-full" onClick={() => setDone(null)}>
              Book another
            </Button>
          </div>
        )}
        {!done && !session && (
          <div>
            <p className="text-sm">Sign in to book a table.</p>
            <Button asChild className="mt-4 w-full">
              <Link to="/auth">Sign in</Link>
            </Button>
          </div>
        )}
        {!done && session && (
          <div className="space-y-4">
            <h3 className="font-bold">Your reservation</h3>
            <p className="font-mono text-sm text-muted-foreground">
              {slot
                ? `${fmtDateTime(slot.starts_at, r.timezone)} · table ${tableId ? tableLabel(r, tableId) : "—"}`
                : "Pick a time"}
            </p>
            {error && (
              <Alert variant="destructive">
                <AlertTitle>Booking rejected</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <Button className="w-full" disabled={!tableFree || busy} onClick={confirm}>
              {busy ? "Booking…" : `Confirm for ${party}`}
            </Button>
          </div>
        )}
      </aside>
    </div>
  );
}
