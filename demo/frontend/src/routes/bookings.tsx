import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { cancelBooking, myBookings } from "@/lib/api.functions";
import { fmtDateTime } from "@/lib/time";
import { useAuth } from "@/hooks/useAuth";
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
  const { user, ready } = useAuth();
  const qc = useQueryClient();
  const { data } = useQuery({ enabled: Boolean(user), queryKey: ["mine", user?.id], queryFn: () => myBookings() });

  if (!ready) return null;
  if (!user)
    return (
      <p>
        <Link to="/auth" className="text-primary underline">Sign in</Link> to see your bookings.
      </p>
    );

  return (
    <div>
      <h1 className="text-4xl font-bold tracking-tight">My bookings</h1>
      <div className="mt-8 divide-y border">
        {data?.length === 0 && <p className="p-6 text-sm text-muted-foreground">No bookings yet.</p>}
        {data?.map((b) => (
          <div key={b.id} className="flex flex-wrap items-center gap-4 bg-card p-4">
            <div className="flex-1">
              <p className="font-bold">{b.restaurant}</p>
              <p className="font-mono text-sm text-muted-foreground">
                {fmtDateTime(b.starts_at, b.timezone)} · table {b.table_label} · {b.party_size} guests
              </p>
            </div>
            <span className={`font-mono text-xs uppercase ${b.status === "confirmed" ? "text-success" : "text-muted-foreground"}`}>
              {b.status}
            </span>
            {b.status === "confirmed" && (
              <Button
                size="sm"
                variant="outline"
                onClick={async () => {
                  await cancelBooking({ data: { bookingId: b.id } });
                  toast.success("Booking cancelled");
                  qc.invalidateQueries();
                }}
              >
                Cancel
              </Button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
