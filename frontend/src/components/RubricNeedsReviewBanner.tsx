// BUG-052: confirming a rubric used to silently wipe `parse_status` back
// to "parsed", erasing the one signal that the parser's own coverage gate
// had flagged it before the instructor confirmed past it. `parse_status`
// now survives activation, so the report can keep disclosing this instead
// of showing no trace at all -- an instructor and an adviser both need to
// know the measuring instrument was flagged.
export function RubricNeedsReviewBanner({
  needsReview,
  issues,
}: {
  needsReview: boolean;
  issues: string[] | null;
}) {
  if (!needsReview) return null;
  return (
    <p
      role="status"
      className="rounded-lg border border-status-caution-text/25 bg-status-caution-bg px-4 py-2.5 text-sm font-medium text-status-caution-text"
    >
      This rubric was activated while the parser's own coverage check still flagged it as needing
      manual completion.
      {issues && issues.length > 0 ? ` ${issues.join(" ")}` : ""}
    </p>
  );
}
