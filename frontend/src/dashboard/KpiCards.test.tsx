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
  it("renders every KPI value and label", () => {
    render(<KpiCards stats={BASE} />);
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("Manuscripts checked")).toBeInTheDocument();
    expect(screen.getByText("Escalations awaiting review")).toBeInTheDocument();
  });

  it("hides the underperforming banner when within budget", () => {
    render(<KpiCards stats={BASE} />);
    expect(screen.queryByText("System underperforming", { exact: false })).not.toBeInTheDocument();
  });

  it("shows the underperforming banner when escalation rate breaches the budget", () => {
    render(
      <KpiCards
        stats={{ ...BASE, escalation_rate: 0.5, escalation_budget: 0.2, system_underperforming: true }}
      />,
    );
    const banner = screen.getByRole("status");
    expect(banner).toHaveTextContent("System underperforming");
    expect(banner).toHaveTextContent("50%");
    expect(banner).toHaveTextContent("20%");
  });
});
