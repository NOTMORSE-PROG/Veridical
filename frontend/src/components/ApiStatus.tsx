// V-048 smoke check: proves the deployed frontend can reach the deployed
// backend — not a product screen, no wireframe ID. Not currently rendered
// anywhere (dead code, confirmed BUG-003).
//
// VITE_API_BASE_URL is a build-time env var. BUG-003: this component
// deliberately does NOT default to "/api" the way api/client.ts does —
// it reads the raw env var so "unconfigured" stays an honest signal if
// someone ever wires it back in without also checking the Vercel
// dashboard is still correct. Do not "fix" this to match client.ts's
// default without re-reading BUG-003's ticket first.
import { useEffect, useState } from "react";
import { StatusPill, type StatusPillTone } from "./StatusPill";

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
    return <StatusPill tone="neutral">API base URL not configured</StatusPill>;
  }
  if (state.kind === "loading") {
    return <StatusPill tone="info">Checking API…</StatusPill>;
  }
  if (state.kind === "error") {
    return <StatusPill tone="danger">API unreachable</StatusPill>;
  }
  const tone: StatusPillTone = state.health.status === "ok" ? "success" : "caution";
  return (
    <StatusPill tone={tone}>
      API {state.health.status} · db {state.health.db}
    </StatusPill>
  );
}
