import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { KpiCards } from "./KpiCards";
import type { DashboardStats } from "./useDashboard";

const BASE: DashboardStats = {
  manuscripts_checked: 5,
  ready_count: 2,
  conditionally_ready_count: 1,
  not_ready_count: 1,
  needs_review_count: 1,
  escalations_awaiting_review: 3,
  escalation_rate: 0.1,
  escalation_budget: 0.2,
  system_underperforming: false,
};

describe("KpiCards", () => {
  it("renders the manuscript-scoped status counts under one heading (BUG-012)", () => {
    render(<KpiCards stats={BASE} />);
    expect(screen.getByText("5 manuscripts checked, by readiness status")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("Conditionally ready")).toBeInTheDocument();
    expect(screen.getByText("Not ready")).toBeInTheDocument();
    expect(screen.getByText("Needs review")).toBeInTheDocument();
  });

  it("shows the criterion-scoped escalations count in a visually separate zone (BUG-012)", () => {
    render(<KpiCards stats={BASE} />);
    const escalationsHeading = screen.getByText("Escalations awaiting your review");
    expect(escalationsHeading).toBeInTheDocument();
    // Its own <section>, not one of the four status cards' grid.
    const section = escalationsHeading.closest("section");
    expect(section).toHaveTextContent("3");
    expect(section).toHaveTextContent("criterion-level count, not a manuscript count");
  });

  it("hides the underperforming banner when within budget", () => {
    render(<KpiCards stats={BASE} />);
    expect(screen.queryByText("System underperforming.")).not.toBeInTheDocument();
  });

  it("shows the underperforming banner when escalation rate breaches the budget", () => {
    render(
      <KpiCards
        stats={{ ...BASE, escalation_rate: 0.5, escalation_budget: 0.2, system_underperforming: true }}
      />,
    );
    const banner = screen.getByRole("status");
    expect(banner).toHaveTextContent("System underperforming.");
    expect(banner).toHaveTextContent("50%");
    expect(banner).toHaveTextContent("20%");
  });

  it("uses singular wording for exactly one manuscript checked", () => {
    render(<KpiCards stats={{ ...BASE, manuscripts_checked: 1 }} />);
    expect(screen.getByText("1 manuscript checked, by readiness status")).toBeInTheDocument();
  });
});
