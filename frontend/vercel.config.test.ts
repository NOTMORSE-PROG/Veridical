// BUG-003 regression, twice over:
//
// 1. `vercel.json`'s /api route is what makes the deployed frontend and
//    backend same-origin from the browser's perspective — the SameSite=Lax
//    session cookie only survives cross-site fetch calls because there IS
//    no cross-site fetch call anymore.
// 2. The SPA catch-all MUST stay under `rewrites`, not `routes` — `routes`
//    has no automatic "check the filesystem for a real static asset
//    first" behavior (that was the now-deprecated `handle: filesystem`
//    step; `rewrites` does this by default). An earlier version of this
//    fix moved BOTH rules into a single `routes` array for what seemed
//    like simpler, more deterministic ordering — and that took production
//    down for every real user: `/assets/*.js`, `.css`, and `favicon.svg`
//    all matched the catch-all and got served as `index.html` with
//    Content-Type: text/html, so the browser refused to execute the
//    module script and the app never booted. `routes` and `rewrites` CAN
//    coexist in the same file (Vercel's own docs: "You can use routes
//    alongside rewrites... You can use both in the same configuration."),
//    which is what fixes this — the API proxy needs `routes` (for its
//    `env` allow-list, which `rewrites` doesn't support), the SPA
//    catch-all needs `rewrites` (for filesystem-first serving).
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const configPath = join(dirname(fileURLToPath(import.meta.url)), "vercel.json");
const config = JSON.parse(readFileSync(configPath, "utf-8"));

describe("vercel.json", () => {
  it("proxies /api/* to $BACKEND_URL via routes (env-interpolated, not hardcoded)", () => {
    expect(config.routes).toEqual([
      { src: "/api/(.*)", dest: "${BACKEND_URL}/$1", env: ["BACKEND_URL"] },
    ]);
  });

  it("keeps the SPA catch-all under rewrites, NOT routes (filesystem-first serving — see file header)", () => {
    expect(config.rewrites).toEqual([{ source: "/(.*)", destination: "/index.html" }]);
    // The catch-all must never live in `routes` alongside the API rule —
    // routes has no filesystem-first check, so it would swallow every
    // static asset request too (the exact live outage this guards against).
    expect(config.routes.some((r: { src?: string }) => r.src === "/(.*)")).toBe(false);
  });
});
