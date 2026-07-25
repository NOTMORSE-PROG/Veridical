// API response types — defined once, imported everywhere (CODING.md §3).
// Mirrors the backend pydantic schemas; keep in sync by hand (no codegen
// yet — small enough surface that a mismatch shows up immediately in the
// gallery/Playwright checks).

export interface Instructor {
  id: number;
  email: string;
  display_name: string;
}

export type CriterionType = "structural" | "semantic";

export interface Criterion {
  id: number;
  type: CriterionType;
  text: string;
  evidence: string | null;
  weight: number;
  position: number;
}

export interface Rubric {
  id: number;
  rubric_family_id: string;
  version: number;
  title: string;
  parse_status: "parsed" | "needs_review";
  parse_issues: string[] | null;
  is_active: boolean;
  is_latest_version: boolean;
  criteria: Criterion[];
}

export interface RubricListItem {
  id: number;
  rubric_family_id: string;
  version: number;
  title: string;
  is_active: boolean;
  created_at: string;
  criteria_count: number;
  report_count: number;
}

export interface QuotaStatus {
  mode: "fake" | "live";
  quota_day: string;
  calls_used: number;
  daily_limit: number;
  calls_remaining: number;
  cache_hits_today: number;
  cache_hit_rate: number;
  reset_at: string;
  rpm_limit: number;
}
