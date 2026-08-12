// BUG-003 regression: `vercel.json`'s /api route is what makes the
// deployed frontend and backend same-origin from the browser's
// perspective, which is the entire fix — the SameSite=Lax session cookie
// only survives cross-site fetch calls because there IS no cross-site
// fetch call anymore. A future edit that reorders or drops this rule
// would silently reopen BUG-003 (Vercel evaluates `routes` top-to-bottom,
// first match wins, so the catch-all SPA route must never come first).
//
// The destination references `${BACKEND_URL}` via the route's `env`
// allow-list (Vercel's documented pattern for env vars in `routes.dest`,
// vercel.com/docs/project-configuration/vercel-json#using-environment-
// variables-in-routes) rather than hardcoding the Render URL, so it stays
// a live pointer even if the backend's URL ever changes — the actual
// value lives in the Vercel project's own env vars (Project Settings →
// Environment Variables → BACKEND_URL), not in this repo.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const configPath = join(dirname(fileURLToPath(import.meta.url)), "vercel.json");
const config = JSON.parse(readFileSync(configPath, "utf-8"));

describe("vercel.json", () => {
  it("proxies /api/* to $BACKEND_URL before the SPA catch-all", () => {
    const [first, second] = config.routes;
    expect(first).toEqual({
      src: "/api/(.*)",
      dest: "${BACKEND_URL}/$1",
      env: ["BACKEND_URL"],
    });
    expect(second).toEqual({ src: "/(.*)", dest: "/index.html" });
  });
});
