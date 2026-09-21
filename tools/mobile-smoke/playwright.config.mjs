import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

const packageDirectory = path.dirname(fileURLToPath(import.meta.url));
const smokeConfig = JSON.parse(
  readFileSync(new URL("./smoke.config.json", import.meta.url), "utf8"),
);
const outputDirectory = process.env.MOBILE_SMOKE_OUTPUT_DIR
  ? path.resolve(process.env.MOBILE_SMOKE_OUTPUT_DIR)
  : path.join(packageDirectory, ".mobile-smoke-output");

export default defineConfig({
  testDir: "./tests",
  testMatch: "production-mobile.spec.mjs",
  timeout: smokeConfig.testTimeoutMs,
  expect: { timeout: smokeConfig.assertionTimeoutMs },
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  outputDir: outputDirectory,
  reporter: [["./sanitized-reporter.mjs"]],
  use: {
    browserName: "chromium",
    headless: true,
    trace: "off",
    screenshot: "off",
    video: "off",
    acceptDownloads: false,
    serviceWorkers: "block",
    ignoreHTTPSErrors: false,
    bypassCSP: false
  }
});
