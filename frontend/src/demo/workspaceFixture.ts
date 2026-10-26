import type { CheckRun, Criterion, EscalatedItemOut, FlagOut, LibraryItemOut, ManuscriptListItem, ReportOut, Rubric } from "../api/types";

export const WORKSPACE_SAMPLE = {
  prefix: "/demo",
  storageKey: "veridical-public-workspace-sample-v1",
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

export const sampleInstructor = { id: 90001, email: "sample-instructor@example.invalid", display_name: "Sample Instructor", onboarding_dismissed_at: WORKSPACE_SAMPLE.preparedAt };

const levelNames = ["Needs Improvement", "Developing", "Sufficient", "Above Average"];
export const sampleCriteria: Criterion[] = [
  { id: 1, type: "semantic", text: "Clarity", weight: 2, position: 0, evidence: "Original course rubric, manuscript page 1", levels: levelNames.map((name, i) => ({ level: i + 1, name, descriptor: `Original rubric level: ${name}`, points: (i + 1) * .5 })) },
  { id: 2, type: "semantic", text: "Content Quality", weight: 4, position: 1, evidence: "Original course rubric, manuscript page 1", levels: levelNames.map((name, i) => ({ level: i + 1, name, descriptor: `Original rubric level: ${name}`, points: i + 1 })) },
  { id: 3, type: "semantic", text: "Spelling & Grammar", weight: 2, position: 2, evidence: "Original course rubric, manuscript page 1", levels: levelNames.map((name, i) => ({ level: i + 1, name, descriptor: `Original rubric level: ${name}`, points: (i + 1) * .5 })) },
  { id: 4, type: "structural", text: "Organization and Formatting", weight: 2, position: 3, evidence: "Original course rubric, manuscript page 1", levels: levelNames.map((name, i) => ({ level: i + 1, name, descriptor: `Original rubric level: ${name}`, points: (i + 1) * .5 })) },
];
export const sampleRubric: Rubric = { id: 1, rubric_family_id: "published-capstone-rubric", version: 1, title: "Published capstone course rubric (page 1)", parse_status: "parsed", parse_issues: null, is_active: true, is_latest_version: true, criteria: sampleCriteria };

export const sampleRun: CheckRun = { id: 1, manuscript_id: 1, rubric_id: 1, status: "done", stage_status: { stages: Object.fromEntries(["ingesting", "structural", "semantic", "integrity", "aggregating"].map(stage => [stage, { status: "done", note: "Prepared sample record; no live analysis executed." }])) }, cancel_requested_at: null, queue_position: null, started_at: WORKSPACE_SAMPLE.preparedAt, finished_at: WORKSPACE_SAMPLE.preparedAt, created_at: WORKSPACE_SAMPLE.preparedAt, manuscript_group_label: WORKSPACE_SAMPLE.title, manuscript_original_filename: WORKSPACE_SAMPLE.filename, manuscript_uploaded_at: WORKSPACE_SAMPLE.preparedAt, rubric_title: sampleRubric.title };

export const sampleManuscript: ManuscriptListItem = { id: 1, group_label: WORKSPACE_SAMPLE.title, original_filename: WORKSPACE_SAMPLE.filename, ingest_status: "done", ingest_failure_reason: null, ingest_failure_detail: null, created_at: WORKSPACE_SAMPLE.preparedAt, latest_check_run_id: 1, latest_check_run_status: "done", latest_done_check_run_id: 1, latest_decision: null, latest_readiness: "needs_review", latest_done_rubric_family_id: sampleRubric.rubric_family_id, escalations_awaiting_review: 1, program: "Software Engineering" };

const quotations = [
  { quote: "Problem Statement: Manual stock tracking leads to frequent errors, time delays,", anchor: "page 6" },
  { quote: "Use encryption and secure session management for safe login and data handling.", anchor: "page 20" },
  { quote: "The result embodied in this project report have not been submitted top any other", anchor: "page 3" },
  { quote: "TABLE OF CONTENT", anchor: "page 2" },
];
export const sampleReport: ReportOut = {
  check_run_id: 1, manuscript_id: 1, manuscript_group_label: WORKSPACE_SAMPLE.title, manuscript_original_filename: WORKSPACE_SAMPLE.filename, rubric_title: sampleRubric.title,
  status: "needs_review", composite_score: null, thresholds: { ready_min_score: 85, not_ready_max_score: 60 }, reason: "Prepared review example on an unchanged 109-page published capstone. Content quality requires an instructor judgment. These records are not a live engine evaluation or the authors' course grade.", flag_deduction: 0, unresolved_high_flag_count: 0, decision: null, decided_at: null, decision_note: null, rubric_is_current: true, llm_mode: "fake", rubric_needs_review: false, rubric_parse_issues: null, pending_review_count: 1, previous_status: null, previous_composite_score: null, integrity_check_status: [], originality_scope: { compared_count: 1, corpus: "library" },
  results: sampleCriteria.map((criterion, index) => ({ criterion_id: criterion.id, text: criterion.text, type: criterion.type, weight: criterion.weight, weight_importance: criterion.weight === 4 ? "high" : "med", kind: criterion.type, outcome: index === 1 ? "escalated" : "passed", score: index === 1 ? null : 75, basis: index === 1 ? null : "manual", anchor: quotations[index].anchor, reasoning: index === 1 ? null : "Prepared instructor-review example. Verify the cited original passage; this is not an AI-generated grade.", reason: index === 1 ? "Review whether the methodology describes a planned system or the completed console implementation. See pages 5, 20, and 29." : null, evidence: [quotations[index]], resolution: null, level: index === 1 ? null : { name: "Sufficient", ordinal: 3, points: 1.5, max_points: 2 } })),
};

export const sampleEscalated: EscalatedItemOut = { check_result_id: 102, criterion_id: 2, criterion_text: "Content Quality", weight: 4, agreement: null, votes: [null, null], ai_majority_verdict: null, reason: "Prepared manual-review task: the methodology proposes encryption and a relational database on page 20, while page 29 explicitly identifies plain-text passwords and no database integration as limitations. Determine whether the report distinguishes intended work from implemented work.", review_reason: "not_graded", unverified_evidence: null, levels: sampleCriteria[1].levels };

const flagBase = { check_result_id: 201, check_run_id: 1, manuscript_group_label: WORKSPACE_SAMPLE.title, criterion_text: null, confidence: null, annotation: null, overridden: false, override_reason: null, ai_verdict_summary: "Prepared source-based review prompt", llm_mode: "fake" as const, is_passage_level: false, passage_pair: null, first_upload_context: false, evidence_unavailable: false, citation_source_key: null, confirmed_citation_source: false };
export const sampleFlags: FlagOut[] = [
  { ...flagBase, id: 1, check_kind: "internal_agreement", severity: "med", evidence_excerpt: "Use encryption and secure session management for safe login and data handling.", page_anchor: "page 20", ai_reasoning: "Page 20 proposes encryption and secure sessions. Page 29 says: 'Password Storage: Passwords stored in plain text without encryption.' This can reflect a proposal-versus-implementation distinction. Check context before requesting clarification; no misconduct is inferred." },
  { ...flagBase, id: 2, check_kind: "internal_agreement", severity: "low", evidence_excerpt: "Build a relational database to store product, supplier, customer, and transaction data.", page_anchor: "page 20", ai_reasoning: "Page 29 explicitly says 'File-Based Storage: No database integration limiting data integrity and concurrent access.' Ask whether the methodology describes a plan rather than completed implementation." },
  { ...flagBase, id: 3, check_kind: "citation_integrity", severity: "low", evidence_excerpt: "The proper guidelines from Our instructor Md. Sakib Ali Mazumder sir.", page_anchor: "page 109", ai_reasoning: "The REFERENCE section on page 109 contains this acknowledgement rather than conventional bibliographic entries. This prepared review prompt asks the instructor to check source documentation. No reference is called fake and no live external verification has run." },
  { ...flagBase, id: 4, check_kind: "originality_reuse", severity: "low", evidence_excerpt: "This project develops a robust, console-based application using", page_anchor: "page 5", is_passage_level: true, matched_ref: 201, passage_pair: { own_excerpt: "This project develops a robust, console-based application using", own_context_before: null, own_context_after: "the C programming language", matched_ref: 201, matched_excerpt: "This project develops a robust, console-based application using", matched_context_before: null, matched_context_after: "the C programming language", context_words_each_side: 0, similarity: 1, level: "exact_duplicate" }, ai_reasoning: "The comparison record is another catalog entry of this exact same openly licensed source. Matching text is expected. This prepared same-source example demonstrates the comparison interface, not independent originality validation or plagiarism detection." },
];

export const sampleLibrary: LibraryItemOut[] = [201, 202].map((id, index) => ({ manuscript_id: id, is_own: true, label: index ? "Stock & Inventory - second reading copy" : WORKSPACE_SAMPLE.title, title: index ? "Stock & Inventory Management System - reading copy" : WORKSPACE_SAMPLE.title, team_name: "Published source: Daffodil International University", authors: [...WORKSPACE_SAMPLE.authors], program: "Software Engineering", school_year: "2025", original_filename: WORKSPACE_SAMPLE.filename, status: "active", source: "library_submission", added_at: WORKSPACE_SAMPLE.preparedAt, created_at: WORKSPACE_SAMPLE.preparedAt, proposed_fields: [], possible_duplicate_of: null, failure_detail: null }));
