import type { StatisticalCheckScopeOut } from "../api/types";
import { descriptiveAssumptionLimit, reconstructionScopeCopy } from "./statisticalScopeCopy";

// What the statistical check's saved state means, laid out as a short block:
// the check and its state, what that means, then the score reconstruction
// step on its own line.
const SCOPE_COPY = {
  completed: ["Completed", "The check ran on the statistics it could read. This does not mean every reported value is correct, and it does not check the raw data."],
  not_applicable: ["Not applicable", "The check found no statistics it could test in the text it could read. This does not mean the paper has no statistics."],
  incomplete: ["Ran only in part", "Some statistics could not be checked. Anything not checked is not treated as correct."],
  not_recorded: ["Not recorded", "No usable status was saved for this check, so do not assume it ran or that it did not apply."],
} as const;

export function StatisticalCheckScope({ scope }: { scope?: StatisticalCheckScopeOut | null }) {
  const limitation = descriptiveAssumptionLimit(scope);
  const reconstruction = reconstructionScopeCopy(scope?.reconstruction);
  const state = limitation || (scope?.reconstruction && reconstruction.incomplete) ? "incomplete" : scope?.state;
  const key = state === "completed" || state === "not_applicable" || state === "incomplete" ? state : "not_recorded";
  const [label, detail] = SCOPE_COPY[key];
  return <section className="signal-check-scope signal-statistical-scope" aria-label="What the statistical check covered">
    <p className="signal-check-scope__head"><strong>What this check covered</strong><span className={`signal-check-scope__state signal-check-scope__state--${key}`}>{label}</span></p>
    <dl className="signal-fact-rows">
      <div><dt>Statistics in the text and tables</dt><dd>{detail}{limitation && <> {limitation}</>}</dd></div>
      <div><dt>Score reconstruction</dt><dd>{reconstruction.detail}</dd></div>
    </dl>
  </section>;
}
