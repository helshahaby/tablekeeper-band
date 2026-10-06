import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { backend, describeError } from "@/lib/backend";
import { fmtTime } from "@/lib/time";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Tablekeeper — Book a table, never double-booked" },
      {
        name: "description",
        content: "Find a restaurant and reserve a table. Every booking is guaranteed unique, even under heavy load.",
      },
      { property: "og:title", content: "Tablekeeper — Book a table, never double-booked" },
      { property: "og:description", content: "Restaurant reservations built by a band of coding agents." },
    ],
  }),
  component: Index,
});

function Index() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["backend", "restaurants"],
    queryFn: () => backend.listRestaurants(),
  });

  return (
    <div>
      <section className="mb-12 border-l-4 border-primary pl-6">
        <p className="font-mono text-xs uppercase tracking-[0.3em] text-primary">
          WeAreDevelopers × BAND — Dark Factory
        </p>
        <h1 className="mt-3 max-w-3xl text-5xl font-bold leading-[1.05] tracking-tight md:text-6xl">
          One table. One booking. No exceptions.
        </h1>
        <p className="mt-4 max-w-xl text-muted-foreground">
          Reserve across four cities and four time zones. The guarantee holds even when many people book at
          the same second.
        </p>
      </section>

      {isLoading ? (
        <p className="font-mono text-sm text-muted-foreground">Loading restaurants…</p>
      ) : error ? (
        <p role="alert" className="text-sm text-destructive">
          Could not load restaurants: {describeError(error)}
        </p>
      ) : data?.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          The backend has no restaurants yet. Seed it with <code>scripts/demo-backend-setup.sh</code>.
        </p>
      ) : (
        <div className="grid gap-px overflow-hidden border bg-border md:grid-cols-2">
          {data?.map((r) => (
            <Link
              key={r.id}
              to="/r/$slug"
              params={{ slug: r.id }}
              className="group bg-card p-6 transition-colors hover:bg-accent"
            >
              <h2 className="text-2xl font-bold group-hover:text-primary">{r.name}</h2>
              <p className="mt-4 font-mono text-xs text-muted-foreground">
                {r.timezone} · now {fmtTime(new Date().toISOString(), r.timezone)}
              </p>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
