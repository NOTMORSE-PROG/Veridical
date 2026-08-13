// Screen 4e KPI zone (F8.8 first slice) + the D-012 escalation-budget
// self-reporting banner. V-055 / BUG-012 presentation fix: the four
// readiness-status counts and the escalations count are DIFFERENT
// aggregation scopes (manuscripts vs criteria) — kept in two visually and
// texturally distinct zones so they can never again be misread as one
// comparable row (backend/app/dashboard/service.py already fixed the data
// side: the four counts always sum to manuscripts_checked).
import type { StatusPillTone } from "../components/StatusPill";
import { READINESS_TONE } from "../domain/readinessTone";
import type { DashboardStats } from "./useDashboard";

// Tone carried by BOTH the left-border accent and the number's text color
// (V-056) — strengthens WCAG 1.4.1 beyond text-hue alone, and gives the
// card the shadow-sm elevation the flat V-055 cards lacked (a real, named
// driver of the "drab" complaint, not just a recolor).
function StatusKpiCard({
  value,
  label,
  tone,
}: {
  value: number;
  label: string;
  tone: StatusPillTone;
}) {
  return (
    <div
      className="flex flex-col gap-1 rounded-lg border border-border bg-panel p-3 shadow-sm"
      style={{ borderLeftWidth: "4px", borderLeftColor: `var(--color-status-${tone}-text)` }}
    >
      <span
        className="text-2xl font-bold text-ink"
        style={{ color: `var(--color-status-${tone}-text)` }}
      >
        {value}
      </span>
      <span className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">
        {label}
      </span>
    </div>
  );
}

export function KpiCards({ stats }: { stats: DashboardStats }) {
  return (
    <div className="flex flex-col gap-4">
      <section aria-labelledby="kpi-heading" className="flex flex-col gap-2">
        <h2 id="kpi-heading" className="text-sm font-semibold text-ink-secondary">
          {stats.manuscripts_checked} manuscript{stats.manuscripts_checked === 1 ? "" : "s"}{" "}
          checked, by readiness status
        </h2>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <StatusKpiCard value={stats.ready_count} label="Ready" tone={READINESS_TONE.ready} />
          <StatusKpiCard
            value={stats.conditionally_ready_count}
            label="Conditionally ready"
            tone={READINESS_TONE.conditionally_ready}
          />
          <StatusKpiCard
            value={stats.not_ready_count}
            label="Not ready"
            tone={READINESS_TONE.not_ready}
          />
          <StatusKpiCard
            value={stats.needs_review_count}
            label="Needs review"
            tone={READINESS_TONE.needs_review}
          />
        </div>
        <p className="text-xs text-ink-tertiary">
          These four numbers always add up to the manuscripts-checked count above.
        </p>
        {stats.manuscripts_checked > 0 && (
          <p className="text-xs text-ink-tertiary">
            {stats.decided_count} of {stats.manuscripts_checked} decided (approved, returned, or
            rejected).
          </p>
        )}
      </section>

      <section
        aria-labelledby="escalations-heading"
        className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg/40 p-4"
      >
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-bold text-ink">{stats.escalations_awaiting_review}</span>
          <h2 id="escalations-heading" className="text-sm font-semibold text-ink">
            Escalations awaiting your review
          </h2>
        </div>
        <p className="mt-1 text-xs text-ink-secondary">
          A criterion-level count, not a manuscript count: one manuscript can have several
          escalated criteria. Open a manuscript's report below to review its flagged criteria.
        </p>
        {stats.system_underperforming && (
          <div role="status" className="mt-3 rounded-md bg-panel px-3 py-2 text-xs text-ink">
            <b className="text-status-attention-text">System underperforming.</b>{" "}
            {Math.round((stats.escalation_rate ?? 0) * 100)}% of criteria are being escalated,
            above the {Math.round(stats.escalation_budget * 100)}% budget. This is on VERIDICAL to
            fix (better thresholds, more AI grading), not extra work for you.
          </div>
        )}
      </section>
    </div>
  );
}
