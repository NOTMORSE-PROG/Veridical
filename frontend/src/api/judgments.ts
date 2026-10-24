// F3.5/F8.3, screens 4h/4w: authenticated judgment records only.
import type { CriterionLevel, CriterionRuleFacts, CriterionSourceCoverage, Decision, EscalationResolution, EvidenceItem, ReportOut } from "./types";

export type RevisionResolution = Exclude<EscalationResolution, "accept_majority">;
export interface JudgmentSnapshot {
  judgment_revision: number;
  resolution_type: EscalationResolution;
  outcome: "passed" | "failed" | "not_applicable";
  score: number | null;
  level: CriterionLevel | null;
  reason: string;
  actor_id: number | null;
  recorded_at: string | null;
  provenance: "recorded" | "legacy_partial";
}
export interface JudgmentContext {
  check_run_id: number;
  criterion_id: number;
  check_result_id: number;
  criterion_text: string;
  assessment_revision: number;
  decision: Decision | null;
  can_revise: boolean;
  unavailable_reason: string | null;
  current: JudgmentSnapshot | null;
  choices: {
    kind: "levelled" | "pass_fail" | "unavailable";
    levels: { level: number; name: string; descriptor: string; points: number }[];
    exclusion_allowed: boolean;
    unavailable_reason: string | null;
  };
  original_assessment: {
    rule_facts?: CriterionRuleFacts | null;
    source_coverage?: CriterionSourceCoverage | null;
    kind: string; basis: string | null; reason: string | null; reasoning: string | null;
    ai_majority_verdict: string | null; evidence: EvidenceItem[];
    unverified_evidence: string[]; support_withheld: boolean; support_status: string | null;
    evidence_unavailable: boolean; original_outcome: string | null; original_score: number | null;
    original_level: CriterionLevel | null;
  };
}
export interface JudgmentHistoryEntry {
  schema_version: 1; entry_id: number; check_run_id: number; criterion_id: number;
  check_result_id: number; action: "initial_resolution" | "judgment_revised";
  assessment_revision_before: number; assessment_revision_after: number;
  before: JudgmentSnapshot | null; after: JudgmentSnapshot;
  performed_by: number; performed_at: string;
}
export interface JudgmentHistory {
  check_run_id: number; criterion_id: number; check_result_id: number;
  assessment_revision: number; judgment_revision: number; current: JudgmentSnapshot;
  entries: JudgmentHistoryEntry[]; history_incomplete: boolean; next_cursor: string | null;
}
export interface JudgmentRevisionInput {
  expected_revision: number; expected_judgment_revision: number;
  resolution: RevisionResolution; level?: number; reason: string;
}
export interface JudgmentRevisionOutput {
  context: JudgmentContext; entry: JudgmentHistoryEntry; report: ReportOut;
}
