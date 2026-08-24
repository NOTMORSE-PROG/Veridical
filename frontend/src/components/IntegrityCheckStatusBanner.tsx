import type { IntegrityCheckStatusOut } from "../api/types";
import { CHECK_KIND_META, CHECK_KIND_ORDER } from "../domain/checkKind";

// BUG-125: BUG-073 made `CheckResult.outcome` honest about an F4/F5
// integrity check that didn't fully execute (never a masked "passed" when
// pairs were skipped) -- nothing on the report surfaced that until this.
// Report-level, not flag-level (unlike FirstUploadContextBanner's
// per-flag `first_upload_context`), so this is scoped to Report.tsx
// itself rather than a FlagsPanel group note: a check that was cut short
// AND happened to produce zero flags from the pairs it did reach would
// otherwise leave no trace anywhere in the flags UI, `ui-designer` spec
// (2026-08-24), live-verified against report 48 in the real dev DB.
//
// `attention` tone (not `caution`): DESIGN.md §1.2 scopes `attention` to
// system/process state -- quota, a degraded pipeline stage, "why did this
// fail" -- and `caution` to the ambiguous middle case a human must judge.
// All three outcomes here (quota_exhausted/api_down/unverifiable) are
// system facts, not manuscript judgments.
const OUTCOME_SENTENCE: Record<IntegrityCheckStatusOut["outcome"], string> = {
  quota_exhausted: "this account ran out of daily AI capacity partway through",
  api_down: "VERIDICAL's AI service was unreachable for part of this run",
  unverifiable: "some AI results from this run could not be read",
};

const CAUSE_LABEL: { key: "n_skipped_parse_failure" | "n_skipped_api_down" | "n_skipped_quota"; label: string }[] = [
  { key: "n_skipped_parse_failure", label: "unreadable AI output" },
  { key: "n_skipped_api_down", label: "a service outage" },
  { key: "n_skipped_quota", label: "the daily AI capacity limit" },
];

function countClause(status: IntegrityCheckStatusOut): string {
  const total = status.n_skipped_quota + status.n_skipped_api_down + status.n_skipped_parse_failure;
  const nonzeroCauses = CAUSE_LABEL.filter(({ key }) => status[key] > 0);
  if (nonzeroCauses.length <= 1) {
    return ` ${total} ${total === 1 ? "pair was" : "pairs were"} skipped.`;
  }
  const breakdown = nonzeroCauses.map(({ key, label }) => `${status[key]} due to ${label}`).join(", ");
  return ` ${total} pairs were skipped in total: ${breakdown}.`;
}

function IntegrityCheckStatusBanner({ status }: { status: IntegrityCheckStatusOut }) {
  const check = CHECK_KIND_META[status.check_kind]?.eyebrow ?? status.check_kind;
  return (
    <p
      role="status"
      className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg px-4 py-2.5 text-sm font-medium text-status-attention-text"
    >
      {check}: {OUTCOME_SENTENCE[status.outcome]}, so it did not finish checking every pair.
      {countClause(status)}
    </p>
  );
}

export function IntegrityCheckStatusBanners({ statuses }: { statuses: IntegrityCheckStatusOut[] }) {
  // `ux-critic` finding (P1, live-reproduced): TypeScript's non-optional
  // type is a compile-time promise, not a runtime one -- a backend one
  // release cycle behind the frontend (a rolling deploy, a stale cached
  // bundle, a partial rollback) can serve a response with no
  // `integrity_check_status` key at all. Without this guard,
  // `statuses.length` threw and React Router's error boundary replaced
  // the ENTIRE report page with a generic crash screen -- total page
  // loss for the one page whose job is being the instructor's trust
  // artifact, far worse than the missing-disclosure bug this component
  // exists to fix.
  if (!statuses || statuses.length === 0) return null;
  const ordered = [...statuses].sort(
    (a, b) => CHECK_KIND_ORDER.indexOf(a.check_kind) - CHECK_KIND_ORDER.indexOf(b.check_kind),
  );
  return (
    <>
      {ordered.map((status) => (
        <IntegrityCheckStatusBanner key={status.check_kind} status={status} />
      ))}
    </>
  );
}
