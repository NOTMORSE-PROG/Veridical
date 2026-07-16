import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Tag, type Severity } from "./Tag";

// DESIGN.md §1: severity tags reuse the pill palettes
// (high → bad, medium → warn, low → queued).
const EXPECTED_PAIRS: Record<Severity, [string, string]> = {
  high: ["bg-status-bad-bg", "text-status-bad-text"],
  medium: ["bg-status-warn-bg", "text-status-warn-text"],
  low: ["bg-status-queued-bg", "text-status-queued-text"],
};

describe("Tag", () => {
  it.each(Object.keys(EXPECTED_PAIRS) as Severity[])(
    "renders the %s severity with its reused pill palette",
    (severity) => {
      render(<Tag severity={severity}>label</Tag>);
      const tag = screen.getByText("label");
      const [bg, text] = EXPECTED_PAIRS[severity];
      expect(tag).toHaveClass(bg, text);
    },
  );
});
