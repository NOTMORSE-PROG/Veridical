import type { ResultRowCommon } from "../api/types";
import { AI_REVIEW_DESCRIPTION, hasPreparedAiGrades } from "../demo/preparedAiReview";

export function PreparedAiReviewNotice({ results }: { results: ResultRowCommon[] }) {
  if (!hasPreparedAiGrades({ results })) return null;
  return <div className="signal-field-hint"><strong>AI review complete.</strong> The saved grades and explanations are ready to inspect. <details><summary>Review basis</summary><p>{AI_REVIEW_DESCRIPTION}</p><p>The published rubric supplies level names without detailed performance descriptions. These are the AI reviewer's judgments against those categories; the teacher can confirm or change them.</p></details></div>;
}
