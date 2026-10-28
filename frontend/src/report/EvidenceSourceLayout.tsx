import type { ReactNode } from "react";
import { useReviewWorkspaceCapacity } from "../document/useReviewWorkspaceCapacity";

/** Keep the original source and its saved explanation in the same reading view. */
export function EvidenceSourceLayout({ source, context }: { source: ReactNode; context: ReactNode }) {
  const capacity = useReviewWorkspaceCapacity();
  return <div ref={capacity.frame} className="signal-source-context" data-layout={capacity.split ? "split" : "stacked"}>
    <span ref={capacity.threshold} className="signal-review-workspace-threshold" aria-hidden="true" />
    <div className="signal-source-context__source">{source}</div>
    <div className="signal-source-context__explanation">{context}</div>
  </div>;
}
