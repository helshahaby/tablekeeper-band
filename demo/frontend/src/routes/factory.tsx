import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { addFactoryEntry, getFactory, runStressTest, type StressReport } from "@/lib/api.functions";
import { fmtDateTime } from "@/lib/time";
import { useAuth } from "@/hooks/useAuth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export const Route = createFileRoute("/factory")({
  head: () => ({
    meta: [
      { title: "Dark Factory dashboard — Tablekeeper" },
      {
        name: "description",
        content: "Agent seats, handoffs, measured costs, recoveries and live concurrency evidence.",
      },
      { property: "og:title", content: "Dark Factory dashboard — Tablekeeper" },
      { property: "og:description", content: "How a band of coding agents built and verified Tablekeeper." },
    ],
  }),
  component: FactoryPage,
});


const H = ({ n, children }: { n: string; children: React.ReactNode }) => (
  <h2 className="mb-3 mt-12 flex items-baseline gap-3 text-xl font-bold">
    <span className="font-mono text-xs text-primary">{n}</span>
    {children}
  </h2>
);

function FactoryPage() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const { data: f } = useQuery({ queryKey: ["factory"], queryFn: () => getFactory() });
  const stages = { data: f?.stages };
  const seats = { data: f?.seats };
  const handoffs = { data: f?.handoffs };
  const costs = { data: f?.costs };
  const recov = { data: f?.recoveries };
  const runs = { data: f?.runs };
  const [attempts, setAttempts] = useState(20);
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState<StressReport | null>(null);

  const totals = (costs.data ?? []).reduce(
    (a, c) => ({ tokens: a.tokens + Number(c.tokens), minutes: a.minutes + Number(c.minutes), usd: a.usd + Number(c.usd) }),
    { tokens: 0, minutes: 0, usd: 0 },
  );
  const byStage = new Map<number, { tokens: number; minutes: number; usd: number }>();
  for (const c of costs.data ?? []) {
    const s = byStage.get(c.stage_number) ?? { tokens: 0, minutes: 0, usd: 0 };
    byStage.set(c.stage_number, { tokens: s.tokens + Number(c.tokens), minutes: s.minutes + Number(c.minutes), usd: s.usd + Number(c.usd) });
  }

  async function go() {
    setRunning(true);
    try {
      const r = await runStressTest({ data: { attempts } });
      setReport(r);
      qc.invalidateQueries({ queryKey: ["factory"] });
      toast[r.passed ? "success" : "error"](r.passed ? "Invariant held" : "Invariant FAILED");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  async function add(row: Record<string, unknown>) {
    try {
      await addFactoryEntry({ data: row as never });
      qc.invalidateQueries({ queryKey: ["factory"] });
      toast.success("Saved");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  return (
    <div>
      <p className="font-mono text-xs uppercase tracking-[0.3em] text-primary">Dark Factory</p>
      <h1 className="mt-2 text-5xl font-bold tracking-tight">Factory evidence</h1>
      <p className="mt-3 max-w-2xl text-muted-foreground">
        Proposed seat design and evidence log. Everything below is pending until recorded from a real BAND Desktop room.
      </p>

      <H n="01">Stages</H>
      <div className="grid gap-px border bg-border md:grid-cols-4">
        {stages.data?.map((s) => (
          <div key={s.id} className="bg-card p-4">
            <p className={`font-mono text-xs uppercase ${s.status === "done" ? "text-success" : s.status === "in_progress" ? "text-primary" : "text-muted-foreground"}`}>
              {s.status.replace("_", " ")}
            </p>
            <p className="mt-2 font-bold">{s.title}</p>
            <p className="mt-2 text-sm text-muted-foreground">{s.summary}</p>
          </div>
        ))}
      </div>

      <H n="02">Seats &amp; mandates</H>
      <div className="grid gap-px border bg-border md:grid-cols-2">
        {seats.data?.map((s) => (
          <div key={s.id} className="bg-card p-4">
            <p className="font-bold">
              {s.name} <span className="font-mono text-xs text-muted-foreground">/ {s.role}{s.model && ` · ${s.model}`}</span>
            </p>
            <p className="mt-2 text-sm">{s.mandate}</p>
          </div>
        ))}
      </div>

      <H n="03">Live evidence — concurrency stress test</H>
      <div className="border bg-card p-5">
        <p className="text-sm text-muted-foreground">
          Fires many booking attempts at the same table and time at once, then sends one request five times in a row.
          It passes only if exactly one booking is made each time. Test bookings are cancelled afterwards.
        </p>
        {user ? (
          <div className="mt-4 flex items-end gap-3">
            <div>
              <label className="font-mono text-xs text-muted-foreground" htmlFor="att">Parallel attempts</label>
              <Input id="att" type="number" min={2} max={50} value={attempts} className="w-28"
                onChange={(e) => setAttempts(Math.max(2, Math.min(50, Number(e.target.value) || 2)))} />
            </div>
            <Button onClick={go} disabled={running}>{running ? "Running…" : "Run stress test"}</Button>
          </div>
        ) : (
          <p className="mt-4 text-sm"><Link to="/auth" className="text-primary underline">Sign in</Link> to run the test.</p>
        )}
        {report && (
          <div className="mt-5 border-t pt-4 font-mono text-sm">
            <p className={report.passed ? "text-success" : "text-destructive"}>{report.passed ? "PASS" : "FAIL"} · {report.durationMs} ms · engine {report.engine}</p>
            <p className="mt-1 text-muted-foreground">
              {report.contested.restaurant} · table {report.contested.table} · {fmtDateTime(report.contested.startsAt, report.contested.timezone)}
            </p>
            <p className="mt-2">{report.attempts} at once → {report.confirmed} confirmed, {report.conflicts} refused, {report.errors} errors</p>
            <p>Same request ×{report.retry.calls} → {report.retry.distinctBookings} booking ({report.retry.confirmed} new, {report.retry.replayed} returned the original)</p>
          </div>
        )}
        <table className="mt-5 w-full font-mono text-xs">
          <thead className="text-left text-muted-foreground">
            <tr><th className="py-1">When</th><th>Attempts</th><th>Confirmed</th><th>Refused</th><th>ms</th><th>Result</th></tr>
          </thead>
          <tbody>
            {runs.data?.slice(0, 8).map((r) => (
              <tr key={r.id} className="border-t">
                <td className="py-1">{new Date(r.created_at).toISOString().slice(0, 19).replace("T", " ")}Z</td>
                <td>{r.attempts}</td><td>{r.confirmed}</td><td>{r.conflicts}</td><td>{r.duration_ms}</td>
                <td className={r.passed ? "text-success" : "text-destructive"}>{r.passed ? "PASS" : "FAIL"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <H n="04">Handoff timeline</H>
      {handoffs.data?.length === 0 && <p className="text-sm text-muted-foreground">Pending — no handoffs recorded.</p>}
      <ol className="border-l-2 border-primary/40 pl-5">
        {handoffs.data?.map((h) => (
          <li key={h.id} className="relative pb-4">
            <span className={`absolute -left-[27px] top-1.5 h-3 w-3 ${h.result === "pass" ? "bg-success" : h.result === "fail" ? "bg-destructive" : "bg-primary"}`} />
            <p className="font-mono text-xs text-muted-foreground">S{h.stage_number} · {h.kind} · {h.from_seat} → {h.to_seat}</p>
            <p className="text-sm">{h.summary}</p>
          </li>
        ))}
      </ol>
      {user && (
        <MiniForm
          fields={["stage_number", "from_seat", "to_seat", "kind (plan/change/evidence/check)", "summary", "result (pass/fail/info)"]}
          onSave={(v) => add({ table: "handoffs", stage_number: Number(v[0]), from_seat: v[1]!, to_seat: v[2]!, kind: v[3] as "plan", summary: v[4]!, result: (v[5] || "info") as "info" })}
        />
      )}

      <H n="05">Measured costs</H>
      {costs.data?.length === 0 && <p className="mb-2 text-sm text-muted-foreground">Pending — no measured costs recorded.</p>}
      <table className="w-full border font-mono text-sm">
        <thead className="bg-muted text-left text-xs text-muted-foreground">
          <tr><th className="p-2">Stage</th><th>Tokens</th><th>Minutes</th><th>USD</th></tr>
        </thead>
        <tbody>
          {[...byStage.entries()].sort((a, b) => a[0] - b[0]).map(([n, s]) => (
            <tr key={n} className="border-t"><td className="p-2">{n}</td><td>{s.tokens.toLocaleString()}</td><td>{s.minutes}</td><td>${s.usd.toFixed(2)}</td></tr>
          ))}
          <tr className="border-t font-bold text-primary"><td className="p-2">Total</td><td>{totals.tokens.toLocaleString()}</td><td>{totals.minutes}</td><td>${totals.usd.toFixed(2)}</td></tr>
        </tbody>
      </table>
      {user && (
        <MiniForm
          fields={["stage_number", "seat", "tokens", "minutes", "usd"]}
          onSave={(v) => add({ table: "cost_entries", stage_number: Number(v[0]), seat: v[1]!, tokens: Number(v[2]), minutes: Number(v[3]), usd: Number(v[4]) })}
        />
      )}

      <H n="06">Recovery log — bad work caught</H>
      {recov.data?.length === 0 && <p className="text-sm text-muted-foreground">Pending — no recoveries recorded.</p>}
      <div className="divide-y border">
        {recov.data?.map((r) => (
          <div key={r.id} className="grid gap-2 bg-card p-4 md:grid-cols-[120px_1fr_1fr]">
            <p className="font-mono text-xs text-muted-foreground">S{r.stage_number} · {r.caught_by}</p>
            <p className="text-sm"><span className="text-destructive">✕</span> {r.problem}</p>
            <p className="text-sm"><span className="text-success">✓</span> {r.fix}</p>
          </div>
        ))}
      </div>
      {user && (
        <MiniForm
          fields={["stage_number", "caught_by", "problem", "fix"]}
          onSave={(v) => add({ table: "recovery_events", stage_number: Number(v[0]), caught_by: v[1]!, problem: v[2]!, fix: v[3]! })}
        />
      )}
    </div>
  );
}

function MiniForm({ fields, onSave }: { fields: string[]; onSave: (v: string[]) => void }) {
  const [v, setV] = useState<string[]>(fields.map(() => ""));
  return (
    <form
      className="mt-3 flex flex-wrap gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(v);
        setV(fields.map(() => ""));
      }}
    >
      {fields.map((f, i) => (
        <Input key={f} placeholder={f} value={v[i]} className="h-8 w-auto min-w-28 flex-1 text-xs"
          onChange={(e) => setV(v.map((x, j) => (j === i ? e.target.value : x)))} />
      ))}
      <Button size="sm" variant="secondary" type="submit">Add</Button>
    </form>
  );
}
