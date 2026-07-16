import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Stepper } from "./Stepper";

const STEPS = [
  { label: "Extract text", state: "done", note: "3 s" },
  { label: "AI decomposition", state: "running" },
  { label: "Validation gate", state: "pending", note: "Waiting" },
] as const;

describe("Stepper", () => {
  it("renders every step label and note as a list", () => {
    render(<Stepper steps={[...STEPS]} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText("Extract text")).toBeInTheDocument();
    expect(screen.getByText("Waiting")).toBeInTheDocument();
  });

  it("gives each state a distinct glyph (state never by color alone)", () => {
    render(<Stepper steps={[...STEPS]} />);
    expect(screen.getByText("✓")).toBeInTheDocument();
    expect(screen.getByText("●")).toBeInTheDocument();
    expect(screen.getByText("○")).toBeInTheDocument();
  });

  it("emphasizes only the running step", () => {
    render(<Stepper steps={[...STEPS]} />);
    expect(screen.getByText("AI decomposition")).toHaveClass("font-semibold");
    expect(screen.getByText("Extract text")).not.toHaveClass("font-semibold");
  });
});
