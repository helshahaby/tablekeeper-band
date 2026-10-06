import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { backend, describeError } from "@/lib/backend";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export const Route = createFileRoute("/auth")({
  head: () => ({
    meta: [
      { title: "Sign in — Tablekeeper" },
      { name: "description", content: "Sign in or create an account to book tables." },
      { property: "og:title", content: "Sign in — Tablekeeper" },
      { property: "og:description", content: "Sign in or create an account to book tables." },
    ],
  }),
  component: AuthPage,
});

function AuthPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [mode, setMode] = useState<"in" | "up">("in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session =
        mode === "in"
          ? await backend.logIn(email, password)
          : await backend.signUp(email, password, displayName.trim() || undefined);
      qc.clear();
      toast.success(`Signed in as ${session.displayName}`);
      navigate({ to: "/" });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-sm border bg-card p-8">
      <h1 className="text-2xl font-bold">{mode === "in" ? "Sign in" : "Create account"}</h1>
      <form onSubmit={submit} noValidate className="mt-6 space-y-4">
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {mode === "up" && (
          <div className="space-y-1.5">
            <Label htmlFor="display-name">Display name (optional)</Label>
            <Input id="display-name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="pw">Password (min 8)</Label>
          <Input id="pw" type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <Button type="submit" className="w-full" disabled={busy}>
          {mode === "in" ? "Sign in" : "Sign up"}
        </Button>
      </form>
      <button className="mt-4 text-sm text-muted-foreground underline" onClick={() => {
          setMode(mode === "in" ? "up" : "in");
          setError(null);
        }}>
        {mode === "in" ? "No account? Sign up" : "Have an account? Sign in"}
      </button>
    </div>
  );
}
