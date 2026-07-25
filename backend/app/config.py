"""Application settings, loaded from environment variables / .env.

Every variable here must also be documented in .env.example (repo root).
The app must boot with NO .env present: every field has a safe default,
and nothing secret is required unless a live integration is actually used.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Checked in order; in Docker/CI variables come from the process env.
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    veridical_env: str = "dev"
    # VERIDICAL_FAKE_LLM=1 swaps the Gemini client for the fixture-backed
    # stub so the whole pipeline runs with zero API keys and zero quota burn.
    veridical_fake_llm: bool = False
    # 5433 = the docker-compose published port (5432 is often taken by a
    # native Postgres on dev machines — see docker-compose.yml).
    database_url: str = "postgresql://veridical:veridical@localhost:5433/veridical"
    # Only needed when fake-LLM mode is OFF and a real call is made (V-009).
    gemini_api_key: str | None = None
    # Per-attempt timeout for the /health DB probe. Generous because Neon
    # free tier autosuspends and needs time to wake (ENGINEERING.md §7).
    db_health_timeout: float = 5.0
    # Dimensionality of manuscript_archive.embedding. Provisional until the
    # embedding model is chosen (V-036); must stay ≤ 2000 or the HNSW index
    # can't be built (pgvector limit, verified 2026-07-17). The value is
    # baked into the DB column at migration time — changing it later means
    # a new migration, not just an env edit.
    embedding_dim: int = 768

    # --- ingestion (V-004) ---------------------------------------------------
    # Local store for raw extraction results (page-anchored blocks, images,
    # geometry). The upload itself is referenced by manuscript.file_ref;
    # this dir holds the derived <manuscript_id>.extraction.json.
    data_dir: Path = Path("./data")
    # Override the packaged heading-patterns data file (section-name
    # synonyms, numbering regexes) without a code change; empty = packaged
    # default (app/ingest/data/heading_patterns.json).
    ingest_patterns_file: Path | None = None
    # A line longer than this is prose, not a heading.
    ingest_heading_max_chars: int = 120
    # Share of a line's characters that must be bold for the line to count
    # as bold (styled runs inside prose stay below it).
    ingest_bold_ratio_min: float = 0.8
    # Points above body size for a line to count as oversized. Bold is the
    # primary signal — the demo document's headings are body-sized bold.
    ingest_heading_size_delta: float = 1.0
    # Embedded-TOC outlines with fewer entries than this aren't evidence.
    ingest_toc_min_entries: int = 4
    # Minimum share of embedded-TOC entries that must be findable on their
    # destination page, or the outline is discarded as stale/foreign.
    ingest_toc_match_min_ratio: float = 0.5
    # A page continues a TOC region while at least this share of its lines
    # end in page numbers / dot leaders.
    ingest_toc_line_ratio: float = 0.25
    # Top/bottom fraction of the page height scanned for running
    # headers/footers, and how widely a line must repeat to be furniture.
    ingest_furniture_band_ratio: float = 0.12
    ingest_furniture_page_ratio: float = 0.4
    ingest_furniture_min_pages: int = 3
    # Below this many selectable chars per page the file is treated as a
    # scan: image_only state, limited checks — NOT an error (F1.7).
    ingest_image_only_chars_per_page: int = 50
    # --- vision pass over embedded images (F1.3 / V-007) --------------------
    # Hard cap on Gemini vision calls per manuscript (quota is the currency,
    # D-001: cap × ~20 groups/day must stay well inside the free-tier RPD).
    ingest_vision_max_images: int = 12
    # Embedded images smaller than this (PDF bbox area, square points) are
    # decorative — icons, bullets, logos — and never worth a call.
    # 5000 pt² ≈ a 1in × 1in figure.
    ingest_vision_min_area_pts: float = 5000.0
    # Rendering DPI for the cropped image sent to the model.
    ingest_vision_crop_dpi: int = 150
    # Upload size ceiling, enforced while streaming (reject early — a
    # 200MB "manuscript" should never reach the parser). Real capstone
    # PDFs run 5–25 MB.
    max_upload_mb: int = 40

    # --- LLM queue (V-009, ENGINEERING §3) -----------------------------------
    # Pinned model id (not the "-latest" alias): golden-set comparisons
    # (TESTING §2) are meaningless across silently-changing models. Verified
    # 2026-07-25 live against this project's key: gemini-2.5-flash and
    # gemini-2.5-flash-lite 404 ("no longer available to new users");
    # gemini-3.5-flash responds.
    gemini_model: str = "gemini-3.5-flash"
    gemini_temperature: float = 0.0
    # Per-attempt network timeout for a Gemini call.
    gemini_request_timeout_seconds: float = 60.0
    # RESEARCH.md §1 (2026-07-16, re-verified 2026-07-25): Gemini Flash free
    # tier is ~10-15 req/min, ~1,500 req/day, reset at midnight Pacific.
    # Governor stays under both with headroom for the burst-safety margin.
    llm_rpm: int = 12
    llm_daily_quota: int = 1400
    # Timezone Gemini resets against — NOT local time (ticket V-009 edge case).
    llm_quota_reset_timezone: str = "America/Los_Angeles"
    llm_max_retries: int = 3
    llm_retry_base_seconds: float = 1.0

    # --- Structural check engine (V-016, F3.2) -------------------------------
    # Override the packaged criterion-matching keyword lists / bound-phrase
    # patterns without a code change; empty = packaged default.
    structural_keywords_file: Path | None = None
    structural_bound_patterns_file: Path | None = None
    # A PDF/DOCX-declared margin within this many points of the criterion's
    # stated value still counts as meeting it (real documents are never
    # pixel-exact; ~1/12 inch).
    structural_margin_tolerance_pts: float = 6.0
    # Share of reference-list entries that must show the APA-ish
    # year-in-parentheses pattern before the citation-style rule passes.
    structural_citation_style_min_ratio: float = 0.7
    # Share of tables that must carry a caption before the table-
    # formatting rule passes.
    structural_table_caption_min_ratio: float = 0.8

    # --- Tier-0 signal layer (V-016, D-011 shadow mode) ----------------------
    # Flesch Reading Ease band a graduate-level capstone chapter is expected
    # to fall within. SHADOW ONLY (D-012): recorded on every semantic
    # check_result for future promotion evaluation (V-025); never decides an
    # outcome until a criterion class is explicitly promoted.
    signal_readability_flesch_min: float = 0.0
    signal_readability_flesch_max: float = 70.0
    # Type-token ratio (unique words / total words) floor — very low
    # diversity often means repetitive/boilerplate prose.
    signal_vocab_diversity_min: float = 0.25

    # --- Rubric decomposition validation gate (V-011, F2.2) ------------------
    # Total attempts = this + 1 (the first attempt isn't a "retry"). Each
    # retry appends the previous failure's reasons to the prompt.
    rubric_parse_max_retries: int = 2
    # Fraction of substantive source lines that must be traceable (by word
    # overlap) into some parsed criterion, or the gate fails — catches an
    # answer that quietly ignores half the rubric (ticket edge case).
    rubric_coverage_min_ratio: float = 0.6
    # A source line shorter than this is a fragment/label, not a
    # requirement statement, and is excluded from the coverage count.
    rubric_coverage_min_line_chars: int = 20
    # Share of a line's significant (4+ letter) words that must appear in
    # a criterion's text/evidence for that line to count as "covered".
    rubric_coverage_word_overlap_ratio: float = 0.3

    # --- Aggregation & scoring (V-019, F8.1, ENGINEERING §5) -----------------
    # Three-way status thresholds — VISIBLE in the report payload, never a
    # black box (ticket AC). Defaults per ENGINEERING.md §5.
    scoring_ready_min_score: float = 85.0
    scoring_not_ready_max_score: float = 60.0
    # Capped severity deductions applied for each UNRESOLVED (not
    # overridden) flag against the composite score, and the total cap so
    # a pile of low-severity flags can't sink a score unboundedly. Not
    # exercised by any real check yet (integrity checks arrive V4/V5) —
    # the mechanism is built and tested now so those tickets only need to
    # start producing flags, not invent the scoring interaction.
    scoring_flag_deduction_high: float = 15.0
    scoring_flag_deduction_med: float = 8.0
    scoring_flag_deduction_low: float = 3.0
    scoring_flag_deduction_cap: float = 40.0

    # --- Check-run orchestration (V-018, ENGINEERING §4) ---------------------
    # How often the background worker polls for the next runnable
    # check_run when it isn't actively advancing one (simplest job runner
    # per the ticket's own research note — no paid queue infrastructure).
    pipeline_worker_poll_seconds: float = 5.0
    # An api_down stage doesn't have a precise reset time the way quota
    # does (D-001) — retry after a fixed backoff instead.
    pipeline_api_down_retry_seconds: float = 300.0
    # Off by default so importing the FastAPI app (every TestClient-based
    # test) never starts a real polling loop against whatever DATABASE_URL
    # happens to be configured; production (Render) turns this on.
    pipeline_worker_autostart: bool = False

    # --- Dashboard accuracy self-reporting (V-021, D-012) --------------------
    # Above this share of criteria escalated, the run/dashboard is flagged
    # "system underperforming" — escalation spam is OUR failure to fix
    # (better thresholds, more Tier-2), never workload dumped on the
    # instructor (D-012 mechanism #2). Override-rate alerting (mechanism
    # #3) needs V-026's override feature to exist first — not computable
    # yet, an honest gap, not implemented here.
    escalation_budget: float = 0.20

    # --- Auth (V-014, F9.1) ---------------------------------------------------
    session_cookie_name: str = "veridical_session"
    session_ttl_hours: int = 12
    # False in dev (plain http://localhost — browsers drop Secure cookies
    # over http); production sets this true (Render terminates TLS on
    # every service automatically, verified 2026-07-25, render.com/docs/tls).
    session_cookie_secure: bool = False
    # Failed-login throttle, keyed by email (in-process — resets on
    # restart; acceptable for a single-institution v1, revisit if this
    # becomes multi-tenant, ENGINEERING §7-style honest limitation).
    login_rate_limit_max_attempts: int = 5
    login_rate_limit_window_seconds: float = 300.0

    # --- CORS (V-048) --------------------------------------------------------
    # Comma-separated origins allowed to call the API from a browser. Empty
    # in dev (no browser cross-origin caller yet); production sets it to
    # exactly the Vercel production origin (dashboard env var, never hardcoded
    # per rule 7 — a wildcard would defeat the purpose of an allowlist). Kept
    # as a raw string (not list[str]): pydantic-settings JSON-decodes complex
    # env fields before any validator runs, which breaks on a plain CSV value.
    cors_allowed_origins: str = ""

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @field_validator(
        "ingest_patterns_file",
        "structural_keywords_file",
        "structural_bound_patterns_file",
        mode="before",
    )
    @classmethod
    def _blank_path_is_none(cls, value: object) -> object:
        # `INGEST_PATTERNS_FILE=` (blank, as .env.example ships it) must
        # mean "unset": Path("") normalizes to Path(".") which is truthy,
        # so without this the loader would try to read a directory.
        return None if value == "" else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
