import type { FlagOut } from "../api/types";
import { CITATION_HISTORY_GUIDANCE, CITATION_HISTORY_TITLE } from "../domain/citationHistory";
import { Alert } from "../ui/Alert";

export function CitationHistoryWarning({ flag }: { flag: FlagOut }) {
  if (flag.citation_metadata_review_warning !== "quoted_title_projection_unverified") return null;
  return <Alert title={CITATION_HISTORY_TITLE} tone="warning"><p>{CITATION_HISTORY_GUIDANCE}</p><p>An override records your judgment. It does not verify the historical title comparison.</p></Alert>;
}
