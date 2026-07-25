// Screen 4e KPI row (F8.8 first slice) + the D-012 escalation-budget
// self-reporting banner — an honest "system underperforming" flag, never
// hidden, when the run-time escalation rate breaches the configured budget.
import { KpiCard } from "../components/KpiCard";
import type { DashboardStats } from "./useDashboard";

export function KpiCards({ stats }: { stats: DashboardStats }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        <KpiCard value={stats.manuscripts_checked} label="Manuscripts checked" />
        <KpiCard value={stats.ready_count} label="Ready" />
        <KpiCard value={stats.conditionally_ready_count} label="Conditionally Ready" />
        <KpiCard value={stats.not_ready_count} label="Not Ready" />
        <KpiCard value={stats.needs_review_count} label="Needs Review" />
        <KpiCard value={stats.escalations_awaiting_review} label="Escalations awaiting review" />
      </div>
      {stats.system_underperforming && (
        <div role="status" className="rounded-control bg-status-warn-bg px-3 py-2 text-xs text-status-warn-text">
          <b>System underperforming</b> — {Math.round((stats.escalation_rate ?? 0) * 100)}% of
          criteria are being escalated, above the {Math.round(stats.escalation_budget * 100)}%
          budget. This is on us to fix (better thresholds, more AI grading), not extra work for
          you.
        </div>
      )}
    </div>
  );
}
