import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Pill, type PillStatus } from "./Pill";

// Acceptance criterion (V-002): status pills render the exact bg/text token
// pairs from DESIGN.md §1 — each variant class must be the matching pair.
const EXPECTED_PAIRS: Record<PillStatus, [string, string]> = {
  ok: ["bg-status-ok-bg", "text-status-ok-text"],
  warn: ["bg-status-warn-bg", "text-status-warn-text"],
  bad: ["bg-status-bad-bg", "text-status-bad-text"],
  processing: ["bg-status-processing-bg", "text-status-processing-text"],
  queued: ["bg-status-queued-bg", "text-status-queued-text"],
};

describe("Pill", () => {
  it.each(Object.keys(EXPECTED_PAIRS) as PillStatus[])(
    "renders the %s status with its DESIGN.md bg/text token pair",
    (status) => {
      render(<Pill status={status}>label</Pill>);
      const pill = screen.getByText("label");
      const [bg, text] = EXPECTED_PAIRS[status];
      expect(pill).toHaveClass(bg, text);
    },
  );
});
