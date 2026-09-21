import assert from "node:assert/strict";
import test from "node:test";

import { buildBoundedSummary } from "../write-summary.mjs";

test("bounded result summary uses the executed viewport configuration", () => {
  const summary = buildBoundedSummary(
    "0123456789abcdef0123456789abcdef01234567",
    [
      { label: "narrow", width: 390, height: 844 },
      { label: "floor", width: 320, height: 480 }
    ],
  );

  assert.match(summary, /Viewports: 390x844 and 320x480/);
  assert.match(summary, /not physical-device or cross-browser evidence/);
});
