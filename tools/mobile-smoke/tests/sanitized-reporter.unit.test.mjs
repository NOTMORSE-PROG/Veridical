import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import SanitizedReporter, {
  SAFE_GLOBAL_STAGE_NAMES,
  SAFE_STAGE_NAMES,
  SAFE_VIEWPORT_LABELS,
  SAFE_VIEWPORT_STAGE_NAMES
} from "../sanitized-reporter.mjs";
import { loadSmokeConfig } from "../lib/runtime.mjs";

async function captureStdout(run) {
  let output = "";
  const originalWrite = process.stdout.write;

  process.stdout.write = (chunk) => {
    output += String(chunk);
    return true;
  };

  try {
    await run();
  } finally {
    process.stdout.write = originalWrite;
  }

  return output;
}

test("reports discovery-only runs as not executed", async () => {
  const reporter = new SanitizedReporter();
  const output = await captureStdout(() => reporter.onEnd({ status: "passed" }));

  assert.equal(output, "V076 RUN NOT_EXECUTED\n");
});

test("reports passed only after a test actually starts", async () => {
  const reporter = new SanitizedReporter();
  reporter.onTestBegin();
  const output = await captureStdout(() => reporter.onEnd({ status: "passed" }));

  assert.equal(output, "V076 RUN PASSED\n");
});

test("forwards only allowlisted fixed stage lines from worker stdout", async () => {
  const reporter = new SanitizedReporter();
  const output = await captureStdout(() => {
    reporter.onStdOut(Buffer.from("V076 STAGE CONFIG START\n"));
    reporter.onStdOut(Buffer.from("V076 STAGE SIGNIN_"));
    reporter.onStdOut(Buffer.from("LAYOUT FAIL viewport=390x844\n"));
    reporter.onStdOut(Buffer.from("V076 STAGE AUTH_ROUTE PASS\n"));
    reporter.onStdOut(Buffer.from("V076 STAGE AUTH_COOKIE_SECURE FAIL\n"));
    reporter.onStdOut(Buffer.from("V076 STAGE REVIEW_DOCUMENT PASS viewport=320x480\n"));
    reporter.onStdOut(Buffer.from("V076 STAGE REVIEW_QUEUE_OPEN PASS viewport=390x844\n"));
    reporter.onStdOut(Buffer.from("V076 STAGE REVIEW_QUEUE_SELECTED PASS viewport=390x844\n"));
    reporter.onStdOut(Buffer.from("V076 STAGE REVIEW_REPORT_TARGET PASS viewport=390x844\n"));
    reporter.onStdOut(Buffer.from("credential=value\n"));
    reporter.onStdOut(Buffer.from("V076 STAGE UNKNOWN FAIL\n"));
    reporter.onStdOut(Buffer.from("V076 STAGE AUTH FAIL viewport=secret\n"));
    reporter.onStdOut(Buffer.from("V076 STAGE AUTH FAIL viewport=390x844\n"));
    reporter.onStdOut(Buffer.from("V076 STAGE REVIEW_DOCUMENT FAIL\n"));
    reporter.onStdOut(Buffer.from("V076 STAGE REVIEW_DOCUMENT FAIL viewport=123x456\n"));
  });

  assert.equal(
    output,
    "V076 STAGE CONFIG START\n"
      + "V076 STAGE SIGNIN_LAYOUT FAIL viewport=390x844\n"
      + "V076 STAGE AUTH_ROUTE PASS\n"
      + "V076 STAGE AUTH_COOKIE_SECURE FAIL\n"
      + "V076 STAGE REVIEW_DOCUMENT PASS viewport=320x480\n"
      + "V076 STAGE REVIEW_QUEUE_OPEN PASS viewport=390x844\n"
      + "V076 STAGE REVIEW_QUEUE_SELECTED PASS viewport=390x844\n"
      + "V076 STAGE REVIEW_REPORT_TARGET PASS viewport=390x844\n",
  );
});

test("drops oversized unterminated worker output before a later safe line", async () => {
  const reporter = new SanitizedReporter();
  const output = await captureStdout(() => {
    reporter.onStdOut("sensitive".repeat(100));
    reporter.onStdOut("V076 STAGE AUTH START\n");
  });

  assert.equal(output, "V076 STAGE AUTH START\n");
});

test("does not combine unterminated worker output across test boundaries", async () => {
  const reporter = new SanitizedReporter();
  reporter.onTestBegin();
  const output = await captureStdout(() => {
    reporter.onStdOut("V076 STAGE AUTH ");
    reporter.onTestEnd(undefined, { status: "passed" });
    reporter.onTestBegin();
    reporter.onStdOut("START\n");
  });

  assert.equal(output, "V076 TEST PASSED\n");
});

test("keeps the reporter stage allowlist in parity with the production journey", () => {
  const source = readFileSync(new URL("./production-mobile.spec.mjs", import.meta.url), "utf8");
  const emitted = new Set(
    [...source.matchAll(/runStage\(\s*"([A-Z_]+)"/g)].map((match) => match[1]),
  );

  assert.deepEqual([...SAFE_STAGE_NAMES].sort(), [...emitted].sort());
  assert.deepEqual(
    [...SAFE_VIEWPORT_LABELS].sort(),
    loadSmokeConfig().viewports.map((viewport) => viewport.label).sort(),
  );
  assert.equal(
    [...SAFE_GLOBAL_STAGE_NAMES].some((stage) => SAFE_VIEWPORT_STAGE_NAMES.has(stage)),
    false,
  );
});
