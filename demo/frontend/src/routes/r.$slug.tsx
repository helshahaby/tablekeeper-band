import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useServerFn } from "@tanstack/react-start";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { bookTable, getRestaurant, getSlots } from "@/lib/api.functions";
import { fmtDateTime, fmtTime, todayIn } from "@/lib/time";
import { useAuth } from "@/hooks/useAuth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export const Route = createFileRoute("/r/$slug")({
  head: ({ params }) => ({
    meta: [
      { title: `Book ${params.slug.replace(/-/g, " ")} — Tablekeeper` },
      { name: "description", content: "See open tables and reserve in the restaurant's local time." },
      { property: "og:title", content: `Book ${params.slug.replace(/-/g, " ")} — Tablekeeper` },
      { property: "og:description", content: "See open tables and reserve in the restaurant's local time." },
    ],
  }),
  component: RestaurantPage,
});

function RestaurantPage() {
  const { slug } = Route.useParams();
  const { user } = useAuth();
  const qc = useQueryClient();
  const book = useServerFn(bookTable);

  const { data: r } = useQuery({
    queryKey: ["restaurant", slug],
    queryFn: () => getRestaurant({ data: { slug } }),
  });

  const [date, setDate] = useState("");
  const [party, setParty] = useState(2);
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ startsAt: string; table: string } | null>(null);
  // One key per booking attempt: repeat clicks or network retries reuse it.
  const keyRef = useRef<string>("");

  useEffect(() => {
    if (r && !date) setDate(todayIn(r.timezone));
  }, [r, date]);
  useEffect(() => {
    keyRef.current = crypto.randomUUID();
  }, [picked, party, date]);

  const { data: slots, isLoading } = useQuery({
    enabled: Boolean(r && date),
    queryKey: ["slots", r?.id, date, party],
    queryFn: () => getSlots({ data: { restaurantId: r!.id, date, party } }),
  });

  if (!r) return <p className="font-mono text-sm text-muted-foreground">Loading…</p>;

  async function confirm() {
    if (!picked || !r) return;
    setBusy(true);
    try {
      const res = await book({
        data: {
          restaurantId: r.id,
          startsAt: picked,
          party,
          guestName: name || user?.email || "Guest",
          idempotencyKey: keyRef.current,
        },
      });
      if (res.status === "error") throw new Error(res.reason);
      if (res.status === "confirmed" || res.status === "replayed") {
        const t = r.tables.find((x) => x.id === res.table_id);
        setDone({ startsAt: picked, table: t?.label ?? "" });
        toast.success(res.status === "replayed" ? "Already booked — same reservation returned" : "Table booked");
      } else {
        toast.error(res.reason ?? "That time was just taken");
      }
      qc.invalidateQueries({ queryKey: ["slots"] });
      qc.invalidateQueries({ queryKey: ["mine"] });
      setPicked(null);
    } catch {
      toast.error("Network problem. Press confirm again — you won't be booked twice.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-10 lg:grid-cols-[1fr_360px]">
      <div>
        <Link to="/" className="font-mono text-xs text-muted-foreground hover:text-primary">
          ← all restaurants
        </Link>
        <h1 className="mt-3 text-5xl font-bold tracking-tight">{r.name}</h1>
        <p className="mt-2 text-muted-foreground">
          {r.cuisine} · {r.city}
        </p>
        <p className="mt-4 max-w-xl">{r.description}</p>

        <div className="mt-8 grid grid-cols-2 gap-4 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor="date">Date (local)</Label>
            <Input id="date" type="date" value={date} min={todayIn(r.timezone)} onChange={(e) => setDate(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="party">Party</Label>
            <Input
              id="party"
              type="number"
              min={1}
              max={6}
              value={party}
              onChange={(e) => setParty(Math.max(1, Math.min(6, Number(e.target.value) || 1)))}
            />
          </div>
        </div>

        <h2 className="mt-8 font-mono text-xs uppercase tracking-widest text-muted-foreground">
          Times shown in {r.timezone}
        </h2>
        <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-5">
          {isLoading && <p className="col-span-full text-sm text-muted-foreground">Checking tables…</p>}
          {slots?.length === 0 && (
            <p className="col-span-full text-sm text-muted-foreground">No times left on this date.</p>
          )}
          {slots?.map((s) => {
            const full = s.free_tables === 0;
            const sel = picked === s.starts_at;
            return (
              <button
                key={s.starts_at}
                disabled={full}
                onClick={() => setPicked(s.starts_at)}
                className={`border px-2 py-3 font-mono text-sm transition-colors ${
                  sel
                    ? "border-primary bg-primary text-primary-foreground"
                    : full
                      ? "cursor-not-allowed text-muted-foreground line-through opacity-50"
                      : "bg-card hover:border-primary"
                }`}
              >
                {fmtTime(s.starts_at, r.timezone)}
                <span className="block text-[10px] opacity-70">{full ? "full" : `${s.free_tables} free`}</span>
              </button>
            );
          })}
        </div>
      </div>

      <aside className="h-fit border bg-card p-6">
        {done ? (
          <div>
            <p className="font-mono text-xs uppercase tracking-widest text-success">Confirmed</p>
            <p className="mt-3 text-lg font-bold">{fmtDateTime(done.startsAt, r.timezone)}</p>
            <p className="text-sm text-muted-foreground">Table {done.table}</p>
            <Link to="/bookings" className="mt-4 inline-block text-sm text-primary underline">
              See my bookings
            </Link>
            <Button variant="secondary" className="mt-4 w-full" onClick={() => setDone(null)}>
              Book another
            </Button>
          </div>
        ) : !user ? (
          <div>
            <p className="text-sm">Sign in to book a table.</p>
            <Button asChild className="mt-4 w-full">
              <Link to="/auth">Sign in</Link>
            </Button>
          </div>
        ) : (
          <div className="space-y-4">
            <h3 className="font-bold">Your reservation</h3>
            <p className="font-mono text-sm text-muted-foreground">
              {picked ? fmtDateTime(picked, r.timezone) : "Pick a time"}
            </p>
            <div className="space-y-1.5">
              <Label htmlFor="name">Name on booking</Label>
              <Input id="name" value={name} placeholder={user.email ?? ""} onChange={(e) => setName(e.target.value)} />
            </div>
            <Button className="w-full" disabled={!picked || busy} onClick={confirm}>
              {busy ? "Booking…" : `Confirm for ${party}`}
            </Button>
          </div>
        )}
      </aside>
    </div>
  );
}
