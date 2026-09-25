import { loadSmokeConfig } from "./lib/runtime.mjs";

const SAFE_STATUSES = new Set(["passed", "failed", "timedOut", "skipped", "interrupted"]);
export const SAFE_GLOBAL_STAGE_NAMES = new Set([
  "CONFIG",
  "CONTEXT",
  "AUTH",
  "AUTH_FORM",
  "AUTH_SUBMIT",
  "AUTH_ROUTE",
  "AUTH_DESK",
  "AUTH_COOKIE",
  "AUTH_COOKIE_PRESENT",
  "AUTH_COOKIE_HTTP_ONLY",
  "AUTH_COOKIE_SECURE",
  "AUTH_COOKIE_SAME_SITE",
  "PRIVACY_BOUNDARY",
  "LOGOUT"
]);
export const SAFE_VIEWPORT_STAGE_NAMES = new Set([
  "SIGNIN_LAYOUT",
  "LAYOUT",
  "NAVIGATION",
  "REVIEW_JOURNEY",
  "REVIEW_DESK_LOAD",
  "REVIEW_DOCUMENT_OPEN",
  "REVIEW_DOCUMENT",
  "REVIEW_QUEUE_OPEN",
  "REVIEW_QUEUE_TAB",
  "REVIEW_QUEUE_TARGET",
  "REVIEW_QUEUE_TAP",
  "REVIEW_QUEUE_SELECTED",
  "REVIEW_REPORT_OPEN",
  "REVIEW_REPORT_LINK",
  "REVIEW_REPORT_TARGET",
  "REVIEW_REPORT_TAP",
  "REVIEW_REPORT_ROUTE",
  "REVIEW_REPORT",
  "REVIEW_EVIDENCE_OPEN",
  "REVIEW_EVIDENCE",
  "REVIEW_SOURCE_OPEN",
  "REVIEW_SOURCE",
  "REVIEW_TABS",
  "REVIEW_RETURN",
  "RUNTIME_ERRORS"
]);
export const SAFE_STAGE_NAMES = new Set([
  ...SAFE_GLOBAL_STAGE_NAMES,
  ...SAFE_VIEWPORT_STAGE_NAMES
]);
export const SAFE_VIEWPORT_LABELS = new Set(
  loadSmokeConfig().viewports.map((viewport) => viewport.label),
);
const MAX_BUFFERED_STDOUT = 512;

function isSafeStageLine(line) {
  const match = /^V076 STAGE ([A-Z_]+) (START|PASS|FAIL)(?: viewport=([0-9]+x[0-9]+))?$/.exec(
    line,
  );
  if (!match) return false;
  const [, stage, , viewport] = match;
  if (SAFE_GLOBAL_STAGE_NAMES.has(stage)) return viewport === undefined;
  return SAFE_VIEWPORT_STAGE_NAMES.has(stage) && SAFE_VIEWPORT_LABELS.has(viewport);
}

export default class SanitizedReporter {
  constructor() {
    this.startedTests = 0;
    this.stdoutRemainder = "";
  }

  onBegin(_config, suite) {
    const count = suite.allTests().length;
    process.stdout.write(`V076 RUN START tests=${count}\n`);
    process.stdout.write("V076 CLAIM touch-enabled Chromium emulation; not physical-device evidence\n");
  }

  onTestBegin() {
    this.stdoutRemainder = "";
    this.startedTests += 1;
  }

  onStdOut(chunk) {
    this.stdoutRemainder += String(chunk);
    let newline = this.stdoutRemainder.indexOf("\n");
    while (newline !== -1) {
      const line = this.stdoutRemainder.slice(0, newline).replace(/\r$/, "");
      this.stdoutRemainder = this.stdoutRemainder.slice(newline + 1);
      if (isSafeStageLine(line)) {
        process.stdout.write(`${line}\n`);
      }
      newline = this.stdoutRemainder.indexOf("\n");
    }
    if (this.stdoutRemainder.length > MAX_BUFFERED_STDOUT) {
      this.stdoutRemainder = "";
    }
  }

  onTestEnd(_test, result) {
    this.stdoutRemainder = "";
    const status = SAFE_STATUSES.has(result.status) ? result.status : "unknown";
    process.stdout.write(`V076 TEST ${status.toUpperCase()}\n`);
  }

  onError() {
    // Intentionally omit error objects: locator diagnostics, DOM snippets,
    // request URLs, and stacks can contain authenticated production data.
    process.stdout.write("V076 RUNNER ERROR\n");
  }

  onEnd(result) {
    if (this.startedTests === 0) {
      process.stdout.write("V076 RUN NOT_EXECUTED\n");
      return;
    }

    const status = result.status === "passed" ? "PASSED" : "FAILED";
    process.stdout.write(`V076 RUN ${status}\n`);
  }

  printsToStdio() {
    return true;
  }
}
