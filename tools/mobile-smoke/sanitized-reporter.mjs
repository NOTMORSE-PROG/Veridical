const SAFE_STATUSES = new Set(["passed", "failed", "timedOut", "skipped", "interrupted"]);

export default class SanitizedReporter {
  constructor() {
    this.startedTests = 0;
  }

  onBegin(_config, suite) {
    const count = suite.allTests().length;
    process.stdout.write(`V076 RUN START tests=${count}\n`);
    process.stdout.write("V076 CLAIM touch-enabled Chromium emulation; not physical-device evidence\n");
  }

  onTestBegin() {
    this.startedTests += 1;
  }

  onTestEnd(_test, result) {
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
