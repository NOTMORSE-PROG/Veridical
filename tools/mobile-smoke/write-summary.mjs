import { appendFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

import { loadSmokeConfig } from "./lib/runtime.mjs";

export function buildBoundedSummary(sourceSha, viewports) {
  if (!/^[0-9a-f]{40}$/i.test(sourceSha)) {
    throw new Error("invalid source revision");
  }
  if (
    !Array.isArray(viewports)
    || viewports.length === 0
    || viewports.some(({ width, height }) => !Number.isInteger(width) || !Number.isInteger(height))
  ) {
    throw new Error("invalid summary viewport configuration");
  }

  const viewportClaim = viewports.map(({ width, height }) => `${width}x${height}`).join(" and ");
  return [
    "## Production mobile browser verification",
    "",
    `- Source revision: \`${sourceSha}\``,
    "- Production frontend: byte-equivalent before and after the journey",
    `- Viewports: ${viewportClaim}`,
    "- Result: named instructor review journey passed",
    "- Boundary: touch-enabled Chromium emulation, not physical-device or cross-browser evidence",
    "- Privacy: share-token and cross-corpus reuse reads were neutralized and were not tested",
    ""
  ].join("\n");
}

export function main(environment = process.env) {
  const summaryPath = environment.GITHUB_STEP_SUMMARY;
  const sourceSha = environment.SOURCE_SHA;
  if (!summaryPath || !sourceSha) {
    throw new Error("summary runtime configuration is missing");
  }

  const config = loadSmokeConfig();
  appendFileSync(summaryPath, buildBoundedSummary(sourceSha, config.viewports), {
    encoding: "utf8"
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
