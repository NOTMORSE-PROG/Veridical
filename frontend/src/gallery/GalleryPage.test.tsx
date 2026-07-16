import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { GalleryPage } from "./GalleryPage";

// Acceptance criterion (V-002): the gallery route renders every base
// component section for visual review against the wireframes.
describe("GalleryPage", () => {
  it("renders a section per base component", () => {
    render(<GalleryPage />);
    for (const section of [
      "Buttons",
      "Status pills",
      "Severity tags",
      "Panel with header row",
      "KPI cards",
      "Stepper",
      "Modal on dim backdrop",
    ]) {
      expect(
        screen.getByRole("heading", { name: section }),
      ).toBeInTheDocument();
    }
  });

  it("shows all five status pills with readiness vocabulary", () => {
    render(<GalleryPage />);
    for (const label of [
      "Processing",
      "Queued",
      "Not ready",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    // Ready / Conditionally ready also appear in the panel rows
    expect(screen.getAllByText("Ready").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Conditionally ready").length).toBeGreaterThan(0);
  });
});
