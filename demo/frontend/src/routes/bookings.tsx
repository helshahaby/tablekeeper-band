import { createFileRoute, Link } from "@tanstack/react-router";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  backend,
  describeError,
  tableLabel,
  useSession,
  type RestaurantDetail,
} from "@/lib/backend";
import { fmtDateTime } from "@/lib/time";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/bookings")({
  head: () => ({
    meta: [
      { title: "My bookings — Tablekeeper" },
      { name: "description", content: "Your upcoming and past table reservations." },
      { property: "og:title", content: "My bookings — Tablekeeper" },
      { property: "og:description", content: "Your upcoming and past table reservations." },
    ],
  }),
  component: BookingsPage,
});

function BookingsPage() {
  const session = useSession();
  const qc = useQueryClient();
  const { data, error, isLoading } = useQuery({
    enabled: Boolean(session),
    queryKey: ["backend", "reservations", session?.userId],
    queryFn: () => backend.listReservations(),
  });

  const restaurantIds = [...new Set(data?.map((b) => b.restaurant_id) ?? [])];
  const restaurants = useQueries({
    queries: restaurantIds.map((id) => ({
      queryKey: ["backend", "restaurant", id],
      queryFn: () => backend.getRestaurant(id),
    })),
  });
  const byId = new Map<string, RestaurantDetail>();
  restaurants.forEach((q) => q.data && byId.set(q.data.id, q.data));

  if (!session)
    return (
      <p>
        <Link to="/auth" className="text-primary underline">
          Sign in
        </Link>{" "}
        to see your bookings.
      </p>
    );

  return (
    <div>
      <h1 className="text-4xl font-bold tracking-tight">My bookings</h1>
      <div className="mt-8 divide-y border">
        {isLoading && <p className="p-6 text-sm text-muted-foreground">Loading…</p>}
        {error && (
          <p role="alert" className="p-6 text-sm text-destructive">
            Could not load bookings: {describeError(error)}
          </p>
        )}
        {data?.length === 0 && (
          <p className="p-6 text-sm text-muted-foreground">No bookings yet.</p>
        )}
        {data?.map((b) => {
          const r = byId.get(b.restaurant_id);
          const tz = r?.timezone ?? "UTC";
          return (
            <div key={b.reservation_id} className="flex flex-wrap items-center gap-4 bg-card p-4">
              <div className="flex-1">
                <p className="font-bold">{r?.name ?? b.restaurant_id}</p>
                <p className="font-mono text-sm text-muted-foreground">
                  {fmtDateTime(b.starts_at, tz)} · table {tableLabel(r, b.table_id)} ·{" "}
                  {b.party_size} guests · ref {b.reference}
                </p>
              </div>
              <span
                className={`font-mono text-xs uppercase ${b.status === "confirmed" ? "text-success" : "text-muted-foreground"}`}
              >
                {b.status}
              </span>
              {b.status === "confirmed" && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={async () => {
                    try {
                      await backend.cancelReservation(b.reference);
                      toast.success("Booking cancelled");
                    } catch (err) {
                      toast.error(describeError(err));
                    }
                    qc.invalidateQueries({ queryKey: ["backend"] });
                  }}
                >
                  Cancel
                </Button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
