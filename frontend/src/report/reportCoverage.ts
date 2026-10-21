import type { IntegrityCheckStatusOut, ReportOut } from "../api/types";
import type { CoverageStatementItem } from "../ui/CoverageStatement";
import { imageCoverageItem } from "./imageCoverage";
import { CITATION_HISTORY_REPORT_LIMIT, CITATION_HISTORY_TITLE } from "../domain/citationHistory";

const INTEGRITY_LABEL: Record<IntegrityCheckStatusOut["check_kind"], string> = {
  internal_agreement: "Internal agreement",
  citation_integrity: "Citation integrity",
  statistical_forensics: "Statistical forensics",
  originality_reuse: "Originality/reuse",
};

// BUG-127/BUG-128: one shared coverage projection now serves both the
// full report and the manuscript-first review workspace. Keeping the
// wording and recovery destinations here prevents the two surfaces from
// making different claims about the same incomplete check.
function integrityCoverageItem(status: IntegrityCheckStatusOut, manuscriptId: number, checkRunId: number): CoverageStatementItem {
  const label = INTEGRITY_LABEL[status.check_kind];
  const reason = status.coverage_reason;
  if (reason === "image_content_unassessed" && status.assessment_scope === "available_text_and_tables") {
    return imageCoverageItem(status, checkRunId);
  }
  if (status.assessment_scope === "reference_metadata_only") {
    const partial = status.analysis_coverage === "partial";
    return {
      key: status.check_kind,
      label: `${label} · ${partial ? "Partially assessed" : "Not assessed"}`,
      detail: partial
        ? "Reference details were checked where possible. Citation matching across the manuscript was not assessed."
        : "Reference entries were retained, but their details could not be checked. Citation matching across the manuscript was not assessed.",
      subIssues: [
        `${status.n_reference_metadata_attempted ?? 0} reference checks attempted. This does not mean every source was verified.`,
        ...(status.n_metadata_skipped_insufficient ? [`${status.n_metadata_skipped_insufficient} references could not be verified reliably.`] : []),
        ...(status.n_metadata_api_down ? [`${status.n_metadata_api_down} source checks could not reach an external service.`] : []),
        "Claim support across the manuscript was not assessed. Unchecked content is not treated as passed.",
      ],
      action: { to: `/report/${checkRunId}/document?view=decision`, label: "Open the paper" },
    };
  }
  const coverageCopy: Record<string, string> = {
    metadata_comparison_policy_unavailable: "Reference metadata comparison could not run because its comparison policy was unavailable. No lookup or claim-support assessment was made for those DOI references.",
    reference_metadata_policy_unavailable: "Reference metadata could not be parsed because its parsing policy was unavailable. Reference lookups and claim support remain unassessed.",
    reference_source_projection_unbound: "The saved reference entries could not be matched to the current source inventory. Reference lookups and claim support remain unassessed.",
    citation_metadata_comparison_unassessed: "Some reference titles could not be compared reliably because of ambiguous formatting, case or missing metadata. Those comparisons and their claim support remain unassessed.",
    source_reading_order_unresolved: "Some pages could not be read reliably. This check was not assessed; this is not a finding against the paper.",
    reference_metadata_insufficient: "Some references are visible in the paper, but their author, title, or date could not be read reliably. They have not been verified.",
    reference_list_unavailable: "The reference list could not be separated reliably. This does not mean references are missing from the paper.",
    author_year_citation_parse_unassessed: "Some citations could not be linked reliably to their references. No conclusion is made about those links.",
    numeric_citation_style_unassessed: "Some numbered citations could not be linked reliably. Open the paper to check their source numbers.",
    numeric_citation_scan_limit: "The numbered-citation scan reached its safety limit. Unread citations have not been assessed.",
    claim_support_unavailable: "Source details were checked where possible, but some cited claims could not be checked against source content.",
    statement_roles_unverified: "The check found possible objectives and results, but could not reliably establish that they describe this study. Comparison leads do not change readiness.",
  };
  if (reason && coverageCopy[reason]) {
    const imageDetails = status.check_kind === "statistical_forensics" &&
      (status.n_images_unassessed || status.n_image_occurrences_omitted || status.n_equations_unassessed ||
        status.n_recovery_figures_unassessed || status.n_recovery_tables_unassessed)
      ? imageCoverageItem(status, checkRunId) : null;
    const subIssues: string[] = [];
    if (status.n_skipped_api_down) subIssues.push(`${status.n_skipped_api_down} items skipped because a service was unavailable.`);
    if (status.n_skipped_quota) subIssues.push(`${status.n_skipped_quota} items skipped because AI capacity was unavailable.`);
    if (status.n_skipped_parse_failure) subIssues.push(`${status.n_skipped_parse_failure} items skipped because the model response could not be validated.`);
    if (status.n_source_pages_unassessed) subIssues.push(`${status.n_source_pages_unassessed} pages need a source-reading check.`);
    if (status.n_metadata_skipped_insufficient) subIssues.push(`${status.n_metadata_skipped_insufficient} references remain unverified.`);
    if (status.n_linkage_unassessed) subIssues.push(`${status.n_linkage_unassessed} reference links remain unassessed.`);
    if (status.n_claim_support_unassessed) subIssues.push(`${status.n_claim_support_unassessed} cited sources have unassessed claim support.`);
    return {
      key: status.check_kind, label, detail: coverageCopy[reason],
      subIssues: [...subIssues, ...(imageDetails?.subIssues ?? [])],
      sources: imageDetails?.sources,
      detailsLabel: imageDetails ? "source and image assessment details" : undefined,
      action: { to: `/report/${checkRunId}/document?view=decision`, label: "Open the paper" },
    };
  }
  const unavailable = status.n_skipped_api_down;
  const capacity = status.n_skipped_quota;
  const parse = status.n_skipped_parse_failure;
  const outcomeText =
    status.outcome === "api_down"
      ? "a service interruption"
      : status.outcome === "quota_exhausted"
        ? "a free-capacity limit"
        : "an unverifiable result";
  const subIssues: string[] = [];
  if (unavailable > 0) subIssues.push(`${unavailable} item${unavailable === 1 ? "" : "s"} skipped because a service was unavailable.`);
  if (capacity > 0) subIssues.push(`${capacity} item${capacity === 1 ? "" : "s"} skipped because daily AI capacity was spent.`);
  if (parse > 0) subIssues.push(`${parse} item${parse === 1 ? "" : "s"} skipped because the source could not be parsed reliably.`);
  return {
    key: status.check_kind,
    label,
    detail: `The check recorded ${outcomeText}. Nothing skipped is presented as passed.`,
    subIssues,
    action: {
      to: `/dashboard?rerun=${manuscriptId}`,
      label: "Run again",
      ariaLabel: `Run again: ${label.toLowerCase()} check for this manuscript`,
    },
  };
}

export function coverageItems(report: ReportOut): CoverageStatementItem[] {
  const items: CoverageStatementItem[] = [];
  if (report.historical_citation_title_unverified) {
    items.push({ key: "historical_citation_title_unverified", label: CITATION_HISTORY_TITLE, detail: CITATION_HISTORY_REPORT_LIMIT });
  }
  if (report.rubric_needs_review) {
    items.push({
      key: "rubric_needs_review",
      label: "Required format review",
      detail: "The required format was activated with unresolved parser uncertainty. Check the criterion record against the original document.",
      subIssues: report.rubric_parse_issues ?? undefined,
      action: { to: "/rubric", label: "Review required format" },
    });
  }
  for (const status of report.integrity_check_status ?? []) {
    items.push(integrityCoverageItem(status, report.manuscript_id, report.check_run_id));
  }
  return items;
}
