import type { CheckRun, Criterion, EscalatedItemOut, FlagOut, LibraryItemOut, ManuscriptListItem, ReportOut, Rubric } from "../api/types";
import { evidenceFor } from "./sourceEvidence";

export const WORKSPACE_SAMPLE = {
  prefix: "/demo",
  storageKey: "veridical-document-review-v2",
  title: "Stock & Inventory Management System",
  filename: "Capstone_G2_Final.pdf",
  pdfAsset: "/sample-assets/stock-inventory-capstone.pdf",
  pageCount: 109,
  inventory: { images: 39, tables: 22, textChars: 113893 },
  source: "https://zenodo.org/records/17101449",
  license: "https://creativecommons.org/licenses/by/4.0/",
  authors: ["Md Amir Hamja", "Nafisha Anjum", "Sabbir Wahed Maruf"],
  attribution: "Hamja, M. A., Anjum, N., & Maruf, S. W. (2025). Stock & Inventory Management System. Daffodil International University. https://doi.org/10.5281/zenodo.17101449. CC BY 4.0. Original PDF unchanged; prepared review records are separate.",
  sha256: "ef662fb9cb5619945c69183a8576f3f945109adc51bff408a6cd6fc21626c5fd",
  preparedAt: "2026-10-26T01:00:00+08:00",
} as const;

export const isWorkspaceSample = () => typeof window !== "undefined" && (window.location.pathname === WORKSPACE_SAMPLE.prefix || window.location.pathname.startsWith(`${WORKSPACE_SAMPLE.prefix}/`));

export const sampleInstructor = { id: 90001, email: "sample-instructor@example.invalid", display_name: "Capstone Instructor", onboarding_dismissed_at: WORKSPACE_SAMPLE.preparedAt };

const levelNames = ["Needs Improvement", "Developing", "Sufficient", "Above Average"];
export const sampleCriteria: Criterion[] = [
  { id: 1, type: "semantic", text: "Clarity", weight: 2, position: 0, evidence: "Original course rubric, manuscript page 1", levels: levelNames.map((name, i) => ({ level: i + 1, name, descriptor: `Original rubric level: ${name}`, points: (i + 1) * .5 })) },
  { id: 2, type: "semantic", text: "Content Quality", weight: 4, position: 1, evidence: "Original course rubric, manuscript page 1", levels: levelNames.map((name, i) => ({ level: i + 1, name, descriptor: `Original rubric level: ${name}`, points: i + 1 })) },
  { id: 3, type: "semantic", text: "Spelling & Grammar", weight: 2, position: 2, evidence: "Original course rubric, manuscript page 1", levels: levelNames.map((name, i) => ({ level: i + 1, name, descriptor: `Original rubric level: ${name}`, points: (i + 1) * .5 })) },
  { id: 4, type: "semantic", text: "Organization and Formatting", weight: 2, position: 3, evidence: "Original course rubric, manuscript page 1", levels: levelNames.map((name, i) => ({ level: i + 1, name, descriptor: `Original rubric level: ${name}`, points: (i + 1) * .5 })) },
];
export const sampleRubric: Rubric = { id: 1, rubric_family_id: "published-capstone-rubric", version: 1, title: "Published capstone course rubric (page 1)", parse_status: "parsed", parse_issues: null, is_active: true, is_latest_version: true, criteria: sampleCriteria };

export const sampleRun: CheckRun = { execution_basis: "prepared_document_review", id: 1, manuscript_id: 1, rubric_id: 1, status: "done", stage_status: { stages: { ingesting: { status: "done", note: "Complete original PDF retained with its published checksum." }, structural: { status: "done", note: "109-page source index available." }, semantic: { status: "skipped", note: "Model grading was not run. The instructor records the four course-rubric judgments." }, integrity: { status: "done", note: "Source observations linked to their original pages." }, aggregating: { status: "done", note: "Review record available for instructor judgment and decision." } } }, cancel_requested_at: null, queue_position: null, started_at: WORKSPACE_SAMPLE.preparedAt, finished_at: WORKSPACE_SAMPLE.preparedAt, created_at: WORKSPACE_SAMPLE.preparedAt, manuscript_group_label: WORKSPACE_SAMPLE.title, manuscript_original_filename: WORKSPACE_SAMPLE.filename, manuscript_uploaded_at: WORKSPACE_SAMPLE.preparedAt, rubric_title: sampleRubric.title };

export const sampleManuscript: ManuscriptListItem = { id: 1, group_label: WORKSPACE_SAMPLE.title, original_filename: WORKSPACE_SAMPLE.filename, ingest_status: "done", ingest_failure_reason: null, ingest_failure_detail: null, created_at: WORKSPACE_SAMPLE.preparedAt, latest_check_run_id: 1, latest_check_run_status: "done", latest_done_check_run_id: 1, latest_decision: null, latest_readiness: "needs_review", latest_done_rubric_family_id: sampleRubric.rubric_family_id, escalations_awaiting_review: 4, program: "Software Engineering" };

export const reviewTasks = [
  { criterionId: 1, page: 109, quote: "efficient database integration", reason: "The abstract (p.5) and implemented C file I/O (pp.37-50) describe a console program with text files. The conclusion (p.109) nevertheless claims database integration, while p.29 says there is no database and pp.103-108 place a database and web interface in future work. Judge whether the delivered scope is explained clearly." },
  { criterionId: 2, page: 23, quote: "User Testing: Real users (admin & staff) will test the full workflow", reason: "The evaluation on p.23 lists intended testing activities. The summary on pp.32-33 rates performance Good or Excellent but provides no timing measurements, user sample, or reported test outcomes. The source code and user-manual screenshots demonstrate a console prototype; they do not establish those performance ratings. Review the supporting evaluation evidence before choosing a rubric level." },
  { criterionId: 3, page: 3, quote: "The result embodied in this project report have not been submitted top any other", reason: "The declaration on p.3 contains a subject-verb agreement error and 'top any other'; p.4 shifts between 'We', 'I', and 'our'; and p.22 spells developers as 'develpoers'. These are specific proofreading examples, not an automatic grade for the whole manuscript. Apply the course's standard after reading the surrounding prose." },
  { criterionId: 4, page: 2, quote: "TABLE OF CONTENT", reason: "The contents page (p.2) locates requirements, use cases, diagrams, methodology, evaluation, limitations, source code and a user manual. The diagrams and screenshots are present. Review consistency of headings, section ordering and cross-references; the course rubric on p.1 names performance levels but provides no descriptors that justify assigning a default level." },
] as const;

export const sampleReport: ReportOut = {
  check_run_id: 1, manuscript_id: 1, manuscript_group_label: WORKSPACE_SAMPLE.title, manuscript_original_filename: WORKSPACE_SAMPLE.filename, rubric_title: sampleRubric.title,
  status: "needs_review", composite_score: null, thresholds: { ready_min_score: 85, not_ready_max_score: 60 },
  reason: "A Windows console inventory prototype written in C, with administrator, employee and customer roles and flat-file persistence. Review the mismatch between delivered and proposed scope, the summary's component totals, and the support for evaluation and literature claims. The four course-rubric judgments remain with the instructor.",
  flag_deduction: 0, unresolved_high_flag_count: 0, decision: null, decided_at: null, decision_note: null, rubric_is_current: true, llm_mode: "manual", rubric_needs_review: false, rubric_parse_issues: null, pending_review_count: 4, previous_status: null, previous_composite_score: null, integrity_check_status: [], originality_scope: null,
  results: sampleCriteria.map(criterion => {
    const task = reviewTasks.find(item => item.criterionId === criterion.id)!;
    return { criterion_id: criterion.id, text: criterion.text, type: criterion.type, weight: criterion.weight, weight_importance: criterion.weight === 4 ? "high" : "med", kind: criterion.type, outcome: "escalated", score: null, basis: "document_review", anchor: `page ${task.page}`, reasoning: task.reason, reason: task.reason, evidence: evidenceFor("criterion", criterion.id).map(entry => ({ quote: entry.quote, anchor: entry.anchor })), resolution: null, level: null };
  }),
};

export const sampleEscalatedItems: EscalatedItemOut[] = sampleCriteria.map(criterion => ({
  review_basis: "document_review",
  evidence: evidenceFor("criterion", criterion.id).map(entry => ({ quote: entry.quote, anchor: entry.anchor })),
  check_result_id: 100 + criterion.id, criterion_id: criterion.id, criterion_text: criterion.text, weight: criterion.weight, agreement: null, votes: [], ai_majority_verdict: null,
  reason: reviewTasks.find(item => item.criterionId === criterion.id)!.reason, review_reason: "not_graded", unverified_evidence: null, levels: criterion.levels,
}));
export const sampleEscalated = sampleEscalatedItems[1];

const flagBase = { check_result_id: 201, check_run_id: 1, manuscript_group_label: WORKSPACE_SAMPLE.title, criterion_text: null, confidence: null, annotation: null, overridden: false, override_reason: null, ai_verdict_summary: "document_review", llm_mode: "manual" as const, is_passage_level: false, passage_pair: null, first_upload_context: false, evidence_unavailable: false, citation_source_key: null, confirmed_citation_source: false };
export const sampleFlags: FlagOut[] = ([
  { ...flagBase, id: 1, check_kind: "internal_agreement", severity: "med", evidence_excerpt: "efficient database integration", page_anchor: "page 109", ai_verdict_summary: "implementation_scope_mismatch", ai_reasoning: "The conclusion on p.109 describes database integration as delivered. In contrast, the limitations on p.29 explicitly state 'No database integration', the source code on pp.37-50 uses fopen/fprintf to save text files, and p.103 proposes database migration as future work. Ask the authors to align the conclusion with the completed implementation. This is a documentation inconsistency, not a claim about misconduct." },
  { ...flagBase, id: 2, check_kind: "internal_agreement", severity: "low", evidence_excerpt: "Develop a web-based inventory system with role-based access control for admin and", page_anchor: "page 20", ai_verdict_summary: "implementation_scope_mismatch", ai_reasoning: "The methodology on p.20 describes a web-based system. The abstract on p.5, the C listing on pp.33-80 and the console screenshots in the user manual support a Windows console implementation. A web interface is explicitly future work on pp.103-104. Clarify whether p.20 describes the original plan, and distinguish that plan from the delivered prototype." },
  { ...flagBase, id: 3, check_kind: "internal_agreement", severity: "med", evidence_excerpt: "Total Components: 32", page_anchor: "page 32", ai_verdict_summary: "component_count_mismatch", ai_reasoning: "The summary says 32 components, 29 complete and 3 partial. The displayed table contains 23 rows on p.32 and 11 more on p.33: 34 rows, comprising 31 Complete and 3 Partial. Header text and summary bullets are excluded from that count. The printed 90.6% is consistent with 29/32; the issue is which component set the summary describes. Ask the authors to identify exclusions or reconcile the list and totals." },
  { ...flagBase, id: 4, check_kind: "citation_integrity", severity: "med", evidence_excerpt: "Research and case studies show that integrating inventory systems with cloud platforms and", page_anchor: "page 7", ai_verdict_summary: "source_support_missing", ai_reasoning: "The literature review makes this research claim on p.7 without identifying a study in the accompanying sentence. The REFERENCE section on p.109 contains an acknowledgement of instructor guidance rather than bibliographic records. Request the sources supporting the literature claims and their complete entries. No publication has been labelled fake, and no external database lookup is being claimed." },
] satisfies FlagOut[]).map(flag => ({ ...flag, source_evidence: evidenceFor("flag", flag.id) }));

export const companionSource = {
  id: 202, title: "e-AIMSS: Electronic Asset Inventory and Management System in School", authors: ["Antonio C. Ahmad"], year: "2023", program: "Information Systems", filename: "e-AIMSS-Ahmad-2023.pdf", pdfAsset: "/sample-assets/e-aimss.pdf", pageCount: 19,
  source: "https://zenodo.org/records/8326056", sha256: "496b6c3be6685e6fe5fa0ed446b11a37f088e9a54a1dde9ed3c40d7cddad4f28",
  attribution: "Ahmad, A. C. (2023). e-AIMSS (Electronic Asset Inventory and Management System in School) for Resource Optimization and Organizational Productivity. International Journal of Multidisciplinary Educational Research and Innovation, 1(3), 121-139. https://doi.org/10.5281/zenodo.8326056. CC BY 4.0. Original PDF unchanged.",
};

export const sampleLibrary: LibraryItemOut[] = [
  { manuscript_id: 201, is_own: true, label: WORKSPACE_SAMPLE.title, title: WORKSPACE_SAMPLE.title, team_name: "Capstone Group 02", authors: [...WORKSPACE_SAMPLE.authors], program: "Software Engineering", school_year: "2025", original_filename: WORKSPACE_SAMPLE.filename, status: "active", source: "library_submission", added_at: WORKSPACE_SAMPLE.preparedAt, created_at: WORKSPACE_SAMPLE.preparedAt, proposed_fields: [], possible_duplicate_of: null, failure_detail: null },
  { manuscript_id: companionSource.id, is_own: true, label: companionSource.title, title: companionSource.title, team_name: null, authors: [...companionSource.authors], program: companionSource.program, school_year: companionSource.year, original_filename: companionSource.filename, status: "active", source: "library_submission", added_at: WORKSPACE_SAMPLE.preparedAt, created_at: WORKSPACE_SAMPLE.preparedAt, proposed_fields: [], possible_duplicate_of: null, failure_detail: null },
];
