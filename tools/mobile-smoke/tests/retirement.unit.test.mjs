import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import test from "node:test";

const packageDirectory = new URL("../", import.meta.url);
const read = (name) => readFileSync(new URL(name, packageDirectory), "utf8");
const canary = "retired-credential-must-not-be-read-or-printed";

function rejected(result) {
  assert.notEqual(result.status, 0);
  assert.equal(result.error, undefined);
  assert.equal(result.signal, null);
  const output = `${result.stdout}${result.stderr}`;
  assert.match(output, /D-030: Production mobile verification is retired/);
  assert.doesNotMatch(output, /RETIREMENT_ENV_READ|RETIREMENT_NETWORK_ATTEMPT/);
  assert(!output.includes(canary));
}

test("production npm scripts reject rather than launching Playwright or deployment reads", () => {
  const { scripts } = JSON.parse(read("package.json"));
  assert.equal(scripts["test:production"], "node retired-production.mjs");
  assert.equal(scripts["verify:deployment"], "node retired-production.mjs");
  for (const credentials of [false, true]) {
    rejected(spawnSync(process.execPath, ["retired-production.mjs"], {
      cwd: packageDirectory,
      encoding: "utf8",
      timeout: 5000,
      env: { ...process.env, PROD_SMOKE_EMAIL: credentials ? canary : "", PROD_SMOKE_PASSWORD: credentials ? canary : "", PROD_MOBILE_ENABLED: "true" },
    }));
  }
});

test("direct config and alternate-config spec loading reject before runtime reads or network", () => {
  assert.match(read("playwright.config.mjs"), /^throw new Error\(/);
  for (const file of ["playwright.config.mjs", "tests/production-mobile.spec.mjs", "warm-production.mjs", "verify-deployment.mjs"]) {
    const script = `
      globalThis.fetch = () => { throw new Error("RETIREMENT_NETWORK_ATTEMPT"); };
      process.env = new Proxy(process.env, { get(target, key) {
        if (typeof key === "string" && /^(PROD_SMOKE_|MOBILE_SMOKE_|PROD_MOBILE_)/.test(key)) throw new Error("RETIREMENT_ENV_READ");
        return target[key];
      }});
      await import(${JSON.stringify(new URL(file, packageDirectory).href)});
    `;
    rejected(spawnSync(process.execPath, ["--input-type=module", "--eval", script], {
      cwd: packageDirectory, encoding: "utf8", timeout: 5000,
      env: { ...process.env, PROD_SMOKE_EMAIL: canary, PROD_SMOKE_PASSWORD: canary },
    }));
  }
});

test("summary execution cannot produce a new success receipt", () => {
  const summary = new URL("retired-summary-should-not-exist.md", packageDirectory);
  assert.equal(existsSync(summary), false);
  rejected(spawnSync(process.execPath, ["write-summary.mjs"], {
    cwd: packageDirectory, encoding: "utf8", timeout: 5000,
    env: { ...process.env, GITHUB_STEP_SUMMARY: summary.pathname, SOURCE_SHA: "0".repeat(40) },
  }));
  assert.equal(existsSync(summary), false);
});

test("the retired workflow is outside discovery and documentation states current desktop scope", () => {
  const workflows = new URL("../../.github/workflows/", packageDirectory);
  assert.equal(existsSync(new URL("production-mobile.yml", workflows)), false);
  for (const name of readdirSync(workflows).filter((name) => /\.ya?ml$/.test(name))) {
    assert.doesNotMatch(readFileSync(new URL(name, workflows), "utf8"), /mobile-smoke|production-mobile/);
  }
  assert.match(read("archive/production-mobile.yml"), /^# HISTORICAL ONLY/);
  assert.match(read("README.md"), /^# Archived .*retired/);
  const features = readFileSync(new URL("../../FEATURES.md", packageDirectory), "utf8");
  assert.match(features, /Current platform scope.*desktop\/laptop only \(D-030/);
  assert.match(features, /native browser zoom and resizable-window usability/);
});
