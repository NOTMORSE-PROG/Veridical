import type { StatisticalCheckScopeOut } from "../api/types";
import { descriptiveAssumptionLimit } from "./statisticalScopeCopy";

const SCOPE_COPY = {
  completed: [
    "Statistical checks: Completion recorded.",
    "The saved check records completed statistical processing. This does not establish that every reported value is correct or verify the raw data.",
  ],
  not_applicable: [
    "Statistical checks: Not applicable.",
    "This is the saved applicability result for the extracted content the check supports. It does not establish that the manuscript contains no statistics.",
  ],
  incomplete: [
    "Statistical checks: Not fully assessed.",
    "The saved statistical assessment is incomplete. Unassessed content is not treated as passed.",
  ],
  not_recorded: [
    "Statistical checks: Not recorded.",
    "This run has no usable saved statistical status. Do not infer that the check ran or was not applicable.",
  ],
} as const;

export function StatisticalCheckScope({ scope }: { scope?: StatisticalCheckScopeOut | null }) {
  const limitation = descriptiveAssumptionLimit(scope);
  const state = limitation ? "incomplete" : scope?.state;
  const [label, detail] = state === "completed" || state === "not_applicable" || state === "incomplete"
    ? SCOPE_COPY[state] : SCOPE_COPY.not_recorded;
  return <p className="signal-review-section__intro signal-statistical-scope"><strong>{label}</strong>{" "}<span>{detail}</span>{limitation && <> <span>{limitation}</span></>}</p>;
}
