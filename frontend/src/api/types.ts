// API response types — defined once, imported everywhere (CODING.md §3).
// Mirrors the backend pydantic schemas; keep in sync by hand (no codegen
// yet — small enough surface that a mismatch shows up immediately in the
// gallery/Playwright checks).

export interface Instructor {
  id: number;
  email: string;
  display_name: string;
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
