// V-048 smoke check: proves the deployed frontend can reach the deployed
// backend across origins (CORS) — not a product screen, no wireframe ID.
// VITE_API_BASE_URL is a build-time env var (Vercel dashboard in prod,
// empty in local dev where no backend URL is configured yet).
import { useEffect, useState } from "react";
import { Pill, type PillStatus } from "./Pill";

type Health = { status: string; db: string };

export function ApiStatus() {
  const baseUrl = import.meta.env.VITE_API_BASE_URL as string | undefined;
  const [state, setState] = useState<
    { kind: "unconfigured" } | { kind: "loading" } | { kind: "ok"; health: Health } | { kind: "error"; message: string }
  >(baseUrl ? { kind: "loading" } : { kind: "unconfigured" });

  useEffect(() => {
    if (!baseUrl) return;
    let cancelled = false;
    fetch(`${baseUrl}/health`)
      .then((res) => res.json())
      .then((health: Health) => {
        if (!cancelled) setState({ kind: "ok", health });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", message: String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [baseUrl]);

  if (state.kind === "unconfigured") {
    return <Pill status="queued">API base URL not configured</Pill>;
  }
  if (state.kind === "loading") {
    return <Pill status="processing">Checking API…</Pill>;
  }
  if (state.kind === "error") {
    return <Pill status="bad">API unreachable</Pill>;
  }
  const status: PillStatus = state.health.status === "ok" ? "ok" : "warn";
  return (
    <Pill status={status}>
      API {state.health.status} · db {state.health.db}
    </Pill>
  );
}
