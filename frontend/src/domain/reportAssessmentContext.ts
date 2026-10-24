// F3/F8, screens 4h/4w: report qualifications accompany judgment actions.
import type { ReportOut } from "../api/types";

export interface AssessmentNotice { id: string; title: string; text: string; actionText: string }
export function reportAssessmentNotices(report: Pick<ReportOut, "check_run_id" | "llm_mode" | "historical_semantic_support_unverified"> | undefined, surface: "report" | "manuscript"): AssessmentNotice[] {
  if (!report) return [];
  const prefix = `assessment-context-${surface}-${report.check_run_id}`;
  const notices: AssessmentNotice[] = [];
  if (report.llm_mode !== "real") notices.push(report.llm_mode === "fake"
    ? { id: `${prefix}-mode`, title: "Test-mode AI responses", actionText: "Test AI responses. Not a real AI assessment.", text: "These fixture responses are not a real manuscript assessment." }
    : { id: `${prefix}-mode`, title: "AI mode could not be verified", actionText: "AI response mode is unverified.", text: "This run predates reliable AI-mode tracking. Treat AI-derived outcomes cautiously." });
  if (report.historical_semantic_support_unverified) notices.push({ id: `${prefix}-support`, title: "AI evidence support needs review", actionText: "AI evidence checks are not all confirmed.", text: "Evidence checks are not confirmed for every criterion. Review each result before relying on it. Saved outcomes are unchanged." });
  return notices;
}
