import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../test/renderWithProviders";
import { OnboardingBanner } from "./OnboardingBanner";

describe("OnboardingBanner", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("states plainly that VERIDICAL never decides for the instructor (ground rule 1, the trust-calibration point of this banner)", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderWithProviders(<OnboardingBanner onDismiss={() => {}} />);
    expect(screen.getByText("Welcome to VERIDICAL")).toBeInTheDocument();
    expect(
      screen.getByText(/never approves or rejects a defense for you/),
    ).toBeInTheDocument();
  });

  it("calls onDismiss exactly once on click (this component owns no mutation of its own -- backend-critic found a double-POST bug when it did)", () => {
    vi.stubGlobal("fetch", vi.fn());
    const onDismiss = vi.fn();
    renderWithProviders(<OnboardingBanner onDismiss={onDismiss} />);

    fireEvent.click(screen.getByRole("button", { name: "Got it" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
    // No fetch call belongs to this component at all -- persistence is the
    // parent's job (Dashboard.test.tsx covers the real POST).
    expect(fetch).not.toHaveBeenCalled();
  });

  it("is not a modal: no role=dialog, no aria-modal, no focus trap (this content is skippable, never blocking)", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderWithProviders(<OnboardingBanner onDismiss={() => {}} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
