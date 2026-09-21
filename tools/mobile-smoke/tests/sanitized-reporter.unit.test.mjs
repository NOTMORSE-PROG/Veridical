import assert from "node:assert/strict";
import test from "node:test";

import SanitizedReporter from "../sanitized-reporter.mjs";

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
