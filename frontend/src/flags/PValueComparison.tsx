import type { FlagOut } from "../api/types";
import { isPValueFinding } from "../domain/checkKind";
import { systemFindingCopy } from "../domain/systemFindingCopy";

/** Saved values only: the browser neither recomputes nor parses explanation text. */
export function PValueComparison({ flag }: { flag: FlagOut }) {
  if (flag.check_kind !== "statistical_forensics" || !isPValueFinding(flag.ai_verdict_summary)) return null;
  return <div className="signal-reasoning signal-p-value-comparison">
    <h3>Recorded p-value comparison</h3>
    {flag.p_value_comparison ? <dl className="signal-document-finding-facts">
      <div><dt>Reported in manuscript</dt><dd>{flag.p_value_comparison.reported}</dd></div>
      <div><dt>Recalculated by the check</dt><dd>{flag.p_value_comparison.recalculated}</dd></div>
    </dl> : <p>{flag.ai_reasoning ? systemFindingCopy(flag.ai_reasoning, flag.ai_verdict_summary, "reasoning") : "No recalculation details were recorded for this finding."}</p>}
    <p className="signal-field-hint">This compares reported numbers with a saved calculation, not with a second passage.</p>
  </div>;
}
