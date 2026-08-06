import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SeverityTag, type Severity } from "./SeverityTag";

const EXPECTED_PAIRS: Record<Severity, [string, string, string]> = {
  high: ["bg-severity-high-bg", "text-severity-high-text", "High severity"],
  med: ["bg-severity-med-bg", "text-severity-med-text", "Medium severity"],
  low: ["bg-severity-low-bg", "text-severity-low-text", "Low severity"],
};

describe("SeverityTag", () => {
  it.each(Object.keys(EXPECTED_PAIRS) as Severity[])(
    "renders the %s severity with its own token pair and plain-language label",
    (severity) => {
      render(<SeverityTag severity={severity} />);
      const [bg, text, label] = EXPECTED_PAIRS[severity];
      const tag = screen.getByText(label);
      expect(tag).toHaveClass(bg, text);
    },
  );

  it("gives each severity level a distinct bar-count icon, not color alone (WCAG 1.4.1)", () => {
    render(
      <>
        <SeverityTag severity="high" />
        <SeverityTag severity="med" />
        <SeverityTag severity="low" />
      </>,
    );
    const filledRects = Array.from(document.querySelectorAll("svg")).map(
      (svg) => svg.querySelectorAll('rect[fill="currentColor"]').length,
    );
    expect(filledRects).toEqual([3, 2, 1]);
  });
});
