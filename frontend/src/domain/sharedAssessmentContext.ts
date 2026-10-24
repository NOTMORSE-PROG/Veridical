import type { PublicReportOut } from "../api/types";

/** F8.7, screen 4l: context derived only from the public report contract. */
export function sharedAssessmentNotices(report: PublicReportOut) {
  const notices: { id: string; title: string; text: string }[] = [];
  const prefix = `shared-context-${report.check_run_id}`;
  if (report.llm_mode !== "real") notices.push(report.llm_mode === "fake"
    ? { id: `${prefix}-mode`, title: "Test-mode AI responses", text: "These fixture responses are not a real manuscript assessment." }
    : { id: `${prefix}-mode`, title: "AI mode could not be verified", text: "Treat AI-derived outcomes cautiously." });
  if (report.rubric_needs_review) notices.push({ id: `${prefix}-format`, title: "Required format review", text: "The instructor activated this format with unresolved parser uncertainty. Its criteria may not fully reflect the source document." });
  if (report.assessment_incomplete) notices.push({ id: `${prefix}-limits`, title: "Assessment limits", text: "Some integrity checks were not fully assessed. Unchecked content is not treated as passed." });
  if (report.semantic_support_unverified) notices.push({ id: `${prefix}-support`, title: "AI evidence support needs review", text: "Evidence checks are not confirmed for every AI criterion result. Review each result's available explanation and evidence before relying on AI content." });
  if (report.historical_native_math_unverified) notices.push({ id: `${prefix}-native`, title: "Saved native Word coverage is unverified", text: "Equation presence and count are unknown for this saved assessment; it has not been recomputed." });
  if (report.historical_citation_title_unverified) notices.push({ id: `${prefix}-citation`, title: "Historical title comparison needs review", text: "Some saved comparisons may include publication details outside the quoted title. Verify affected titles before relying on those differences; this report has not been recomputed." });
  return notices;
}
