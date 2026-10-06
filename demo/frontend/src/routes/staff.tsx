import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { staffFloor } from "@/lib/api.functions";
import { fmtTime, todayIn } from "@/lib/time";
import { useAuth } from "@/hooks/useAuth";

export const Route = createFileRoute("/staff")({
  head: () => ({
    meta: [
      { title: "Staff floor view — Tablekeeper" },
      { name: "description", content: "Today's bookings per table for restaurant staff." },
      { property: "og:title", content: "Staff floor view — Tablekeeper" },
      { property: "og:description", content: "Today's bookings per table for restaurant staff." },
    ],
  }),
  component: StaffPage,
});

function StaffPage() {
  const { user, ready } = useAuth();
  const { data } = useQuery({ enabled: Boolean(user?.isStaff), queryKey: ["mine", "staff-floor"], queryFn: () => staffFloor() });

  if (!ready) return null;
  if (!user)
    return (
      <p>
        <Link to="/auth" className="text-primary underline">Sign in</Link> with a staff account.
      </p>
    );
  if (!user.isStaff)
    return <p className="text-muted-foreground">This view is for restaurant staff only. Staff accounts are set by the server operator.</p>;

  return (
    <div>
      <h1 className="text-4xl font-bold tracking-tight">Floor view — today</h1>
      <div className="mt-8 space-y-8">
        {data?.rests.map((r) => (
          <section key={r.id}>
            <h2 className="font-bold">
              {r.name} <span className="font-mono text-xs text-muted-foreground">{todayIn(r.timezone)} · {r.timezone}</span>
            </h2>
            <div className="mt-2 grid gap-px border bg-border sm:grid-cols-5">
              {r.tables.map((t) => {
                const list = data.bks.filter((b) => b.table_id === t.id);
                return (
                  <div key={t.id} className="bg-card p-3">
                    <p className="font-mono text-xs text-muted-foreground">{t.label} · {t.seats} seats</p>
                    {list.length === 0 && <p className="mt-2 text-xs text-muted-foreground">—</p>}
                    {list.map((b) => (
                      <p key={b.id} className="mt-2 text-sm">
                        <span className="font-mono text-primary">{fmtTime(b.starts_at, r.timezone)}</span> {b.guest_name} ({b.party_size})
                      </p>
                    ))}
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
