import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Stepper } from "./Stepper";

const STEPS = [
  { id: "extract", label: "Extract text", state: "done", tagText: "Done" },
  { id: "decompose", label: "AI decomposition", state: "running", tagText: "In progress" },
  { id: "validate", label: "Validation gate", state: "pending", tagText: "Not started yet" },
] as const;

describe("Stepper", () => {
  it("renders every step label and tag as a list", () => {
    render(<Stepper steps={[...STEPS]} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText("Extract text")).toBeInTheDocument();
    expect(screen.getByText("Not started yet")).toBeInTheDocument();
  });

  it("gives done/skipped visually and textually distinct tags (never conflated)", () => {
    const steps = [
      { id: "a", label: "A", state: "done", tagText: "Done" },
      { id: "b", label: "B", state: "skipped", tagText: "Skipped" },
    ] as const;
    render(<Stepper steps={[...steps]} />);
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.getByText("Skipped")).toBeInTheDocument();
  });

  it("marks the actively running/blocked step with aria-current=step", () => {
    render(<Stepper steps={[...STEPS]} />);
    const items = screen.getAllByRole("listitem");
    expect(items[1]).toHaveAttribute("aria-current", "step");
    expect(items[0]).not.toHaveAttribute("aria-current");
  });

  it("emphasizes only the running/blocked step", () => {
    render(<Stepper steps={[...STEPS]} />);
    expect(screen.getByText("AI decomposition")).toHaveClass("font-semibold");
    expect(screen.getByText("Extract text")).not.toHaveClass("font-semibold");
  });

  it("renders an optional detail caption below the row", () => {
    const steps = [
      { id: "a", label: "AI grading", state: "attention", tagText: "Needs review", detail: "3 of 12 criteria were not graded by AI." },
    ] as const;
    render(<Stepper steps={[...steps]} />);
    expect(screen.getByText("3 of 12 criteria were not graded by AI.")).toBeInTheDocument();
  });
});
