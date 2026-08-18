"""Application settings, loaded from environment variables / .env.

Every variable here must also be documented in .env.example (repo root).
The app must boot with NO .env present: every field has a safe default,
and nothing secret is required unless a live integration is actually used.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
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
    # V-052 (BYOK): master key encrypting each instructor's own pasted
    # Gemini key at rest (Fernet, `cryptography.fernet.Fernet.generate_key()`
    # — a 32-byte urlsafe-base64 value). Unset = the BYOK feature is simply
    # unavailable on this deployment (settings hides the input rather than
    # storing a key it can't safely protect); never defaulted, set once via
    # the host's env var dashboard (ENGINEERING §8: secrets are never
    # defaulted in code). Rotation is NOT solved by Fernet alone -- would
    # need a decrypt-old/re-encrypt-new migration of every stored key; not
    # attempted here, documented as a known limitation (ticket edge case).
    byok_encryption_key: str | None = None
    # Timeout for the one-shot validation probe (GET /models, no generation
    # spent) run before an instructor's pasted key is saved.
    byok_probe_timeout_seconds: float = 15.0
    # Per-attempt timeout for the /health DB probe. Generous because Neon
    # free tier autosuspends and needs time to wake (ENGINEERING.md §7).
    db_health_timeout: float = 5.0
    # Dimensionality of manuscript_archive.embedding. Model chosen at V-036
    # pickup: potion-base-8M outputs 256 dims (confirmed programmatically,
    # `StaticModel.dim`, not assumed — DECISIONS.md D-011 addendum); must
    # stay ≤ 2000 or the HNSW index can't be built (pgvector limit, verified
    # 2026-07-17). The value is baked into the DB column at migration time —
    # changing it later means a new migration (0014), not just an env edit.
    embedding_dim: int = 256
    # Tier-1 local embedding model (D-011, all similarity work: intent<->
    # outcome pairing V-035, originality archive V-036/037). potion-base-8M
    # measured fresh 2026-08-08 at ~102MB alone (DECISIONS.md D-011
    # addendum) — well inside the free-tier ceiling on its own; local NLI
    # (the judgment half) did not clear the same gate and stays Tier 2.
    embedding_model_id: str = "minishlab/potion-base-8M"

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
    # DOCX is a zip archive — `max_upload_mb` only caps the COMPRESSED size
    # on disk. A crafted archive can expand far beyond that in memory
    # during parsing (zip-bomb class risk, BUG-005/D-020). This caps total
    # UNCOMPRESSED member size before python-docx ever opens the archive;
    # 500MB is generous for a real manuscript's embedded images while still
    # bounding worst-case memory against Render's 512MB free-tier ceiling.
    max_docx_uncompressed_mb: int = 500

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
    # Override the packaged model-pool file (app/llm/data/model_pool.json)
    # without a code change; empty = packaged default. The pool is the
    # ordered list of models the queue may spend, each with its OWN measured
    # per-day/per-minute limits — see app/llm/pool.py for why per-model.
    llm_model_pool_file: Path | None = None
    # Fallback limits, used ONLY for the single-model form (a caller that
    # pins one model explicitly). The pool file carries the real per-model
    # numbers. MEASURED 2026-07-26 against this project's key (V-049,
    # tools/probe_gemini_quota.py) — the previously documented ~1,500/day
    # was wrong for this model by 70x: gemini-3.5-flash allows exactly 20
    # requests/day on the free tier.
    llm_rpm: int = 10
    llm_daily_quota: int = 20
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
    # --- Weight importance display (D-023, BUG-051/052/098) ------------------
    # Criterion weight is a RELATIVE value (no required total), so it's
    # bucketed into a Low/Medium/High scale rather than shown as a bare
    # percentage. Expressed as a ratio against the EQUAL-SPLIT average for
    # the rubric (1.0 = "as important as an equal share would be") so the
    # bucketing adapts to any rubric size instead of needing rubric-specific
    # absolute thresholds — a weight below this ratio is Low, at/above
    # `weight_importance_high_min_ratio` is High, everything between is
    # Medium.
    weight_importance_low_max_ratio: float = 0.5
    weight_importance_high_min_ratio: float = 1.5

    # --- Resolution reason (BUG-096) ------------------------------------------
    # The escalation-resolution "Reason (required)" field previously
    # enforced only `min_length=1`, so a single character ("x") satisfied
    # it and was then published verbatim to the report, the exported PDF,
    # and the public share link ("Marked Pass. x"). A length floor is
    # crude but honest, and cheaper to get right than trying to detect a
    # "real" justification -- chosen low enough that a genuine short
    # reason ("Verified in Chapter 2.") still clears it, high enough that
    # a single keystroke never does.
    resolution_reason_min_length: int = 10

    # --- Check-run orchestration (V-018, ENGINEERING §4) ---------------------
    # How often the background worker polls for the next runnable
    # check_run when it isn't actively advancing one (simplest job runner
    # per the ticket's own research note — no paid queue infrastructure).
    pipeline_worker_poll_seconds: float = 5.0
    # An api_down stage doesn't have a precise reset time the way quota
    # does (D-001) — retry after a fixed backoff instead.
    pipeline_api_down_retry_seconds: float = 300.0
    # AVAILABILITY FLOOR (V-050, D-015). When the day's AI budget is spent,
    # FINISH the run — deterministic checks stand, and the criteria the AI
    # never reached go to the instructor as an honest `quota_exhausted`
    # state — instead of parking it until midnight Pacific, which can fall
    # after the defense. Set false to restore the old park-and-resume
    # behaviour (the run then produces nothing until quota returns).
    pipeline_degrade_on_quota: bool = True
    # Instructor-facing wording for those criteria. Honest and non-accusatory
    # per charter rule 3: it describes OUR limit, never the manuscript.
    pipeline_quota_degraded_reason: str = (
        "Not graded by AI. Today's free AI capacity was reached. "
        "Needs your review, or re-run this check after the daily reset."
    )
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

    # --- Self-consistency voting & confidence gate (V-022/V-023, D-006/D-011, F3.4-3.5) ---
    # The ONLY confidence mechanism (D-006 — never a second ad-hoc score).
    # Agreement below this fraction escalates to the instructor instead of
    # auto-scoring, even when a 2-of-3 majority verdict exists. Default 1.0
    # means ANY disagreement escalates (ticket: "start strict, loosen with
    # golden evidence") — V-025's golden-set agreement numbers are what
    # justify lowering it later, never a guess.
    escalation_agreement_threshold: float = 1.0

    # --- Accuracy gates: tier promotion + override alarm (V-025, D-012) ------
    # A shadow Tier 0/1 signal class only starts auto-deciding once its
    # agreement against ground truth clears this bar over at least this many
    # shadow samples (D-012 mechanism #1) — the promotion table records WHY,
    # dated, per criterion class; nothing is promoted on thin data.
    # D-012 promotion bar. Since V-053 this is compared against the LOWER
    # BOUND of the 95% Wilson interval, not the observed rate: autonomy is a
    # one-way door (a promoted class stops asking the instructor, so its
    # errors become invisible), and it should be granted on the pessimistic
    # estimate, not the flattering one.
    tier_promotion_agreement: float = 0.90
    # Raised 20 -> 35 (V-053). Not a preference — arithmetic: a 0.90 lower
    # bound is unreachable below n=35 even at 100% observed agreement
    # (n=20 perfect gives only 0.839), so leaving it at 20 would advertise a
    # bar that cannot be cleared. 35 also lands inside the 30-50 "minimum
    # viable golden set" range the LLM-judge literature recommends
    # independently — see RESEARCH.md §9.
    tier_promotion_min_n: int = 35
    # Below this, the harness reports its numbers as indicative only and
    # refuses to call them a baseline (same 30-50 guidance).
    golden_min_viable_n: int = 30
    # Instructor override rate per check type above this share triggers a
    # dashboard accuracy alarm (D-012 mechanism #3) — overrides are a
    # labeled ground-truth signal, not just a UI action.
    override_alert_rate: float = 0.15

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
    # Per-instructor throttle on quota-spending endpoints (check-run
    # creation, manuscript ingest, rubric upload — BUG-004/D-020): defense-
    # in-depth against one actor exhausting the shared 300-calls/day Gemini
    # pool (D-001/D-014, ~17 manuscripts/day sizing). 20/hour is generous
    # for real usage and bounds a runaway loop or hostile actor; same class
    # of judgment call as the login limiter's 5/300s above, not a measured
    # external number.
    action_rate_limit_max_attempts: int = 20
    action_rate_limit_window_seconds: float = 3600.0

    # --- External citation-verification APIs (V-028, F5.6) -------------------
    # Contact info sent to CrossRef/Open Library so requests land in each
    # provider's higher "polite"/"identified" rate tier — required by both
    # providers' own usage policies (RESEARCH.md §2), not a secret.
    external_contact_email: str = "veridical@example.org"
    external_http_timeout_seconds: float = 15.0
    external_max_retries: int = 2
    external_retry_base_seconds: float = 1.0
    # CrossRef polite-pool rates changed 2025-12-01 (RESEARCH.md §2) — two
    # different endpoints, two different limits: single-DOI lookup vs.
    # metadata search.
    crossref_single_rps: float = 10.0
    crossref_list_rps: float = 3.0
    # Semantic Scholar (RESEARCH.md §2): the unauthenticated shared pool is
    # generously documented (5,000 req/5min) but genuinely shared/unreliable
    # in practice, so the no-key path self-throttles to 1 rps regardless —
    # matching what an authenticated key's search-endpoint limit would give
    # anyway. Empty key = unauthenticated path (works today, no owner action
    # needed to build against); set the env var once a key is issued
    # (V-028's own pending action item) for a higher ceiling, no code change.
    semantic_scholar_api_key: str | None = None
    semantic_scholar_rps: float = 1.0
    # Open Library: 3 rps with an identifying User-Agent (RESEARCH.md §2).
    openlibrary_rps: float = 3.0
    # Google Books: no key required for low-volume use. Current documented
    # daily quotas conflict across Google's own pages (RESEARCH.md §2) — this
    # is a LOCAL budget this app enforces on itself, not a number trusted
    # from Google's inconsistent docs; degrades to Open-Library-only once
    # the local daily counter hits it (ticket edge case).
    google_books_api_key: str | None = None
    google_books_daily_quota: int = 1000
    # A cached verification older than this is re-checked even on a cache
    # hit (retractions land late — ticket edge case: "don't trust an old
    # 'not retracted' forever").
    citation_cache_stale_days: int = 30
    # Claim-support check (V-030, F5.4): batched into as few Gemini calls
    # as possible per manuscript (D-001/D-011). Local NLI was measured and
    # ruled out (DECISIONS.md D-011 follow-up: 487MB peak RSS, doesn't fit
    # Render's 512MB) — this is the Tier-2 fallback D-011 itself named.
    claim_support_max_pairs_per_call: int = 20

    # --- Statistical forensics: extraction (V-031, F6.1) ----------------------
    # Override the packaged table-column-header synonym list without a code
    # change; empty = packaged default (same convention as structural_keywords_file).
    forensics_keywords_file: Path | None = None

    # --- Internal agreement: intent/outcome extraction (V-034, F4.1/F4.2) ----
    # Override the packaged cue lexicon without a code change; empty = packaged
    # default (same convention as forensics_keywords_file).
    agreement_cues_file: Path | None = None
    # A heading-like line (short, title-case/upper-case/numbered) longer than
    # this many words is treated as ordinary prose instead — real headings in
    # the owner's own proposal top out at 6 words ("General Objective of the
    # Study"); generous headroom above that.
    agreement_heading_max_words: int = 12
    # difflib.SequenceMatcher ratio above which two statements are treated as
    # the same restated objective/finding (ticket edge case: Ch1 vs Ch3
    # wording drift) rather than two distinct ones.
    agreement_dedup_similarity_threshold: float = 0.82
    # A modal-phrase cue ("aims to", "results showed") only counts within
    # this many leading words of its sentence — real objective/finding
    # statements name their subject early; a cue buried deeper is usually a
    # nested clause TALKING ABOUT intent, not stating one (real false
    # positive found live: the owner's own proposal defines "Internal
    # Agreement" in its glossary using the words "it intends to do").
    agreement_cue_max_lead_words: int = 8

    # --- Internal agreement: intent<->outcome pairing (V-035, F4.3/F4.4) -----
    # Cosine-similarity floor (potion-base-8M) an outcome must clear to be a
    # PAIRING CANDIDATE at all — below this, no Gemini call is spent on it
    # (quota discipline, D-011): an intent with zero candidates is an
    # unmatched-intent flag without ever reaching the judgment tier.
    agreement_pairing_similarity_floor: float = 0.35
    # Bounds Gemini judgment calls per intent (quota discipline) — only the
    # top-K most similar outcome candidates get judged.
    agreement_pairing_max_candidates_per_intent: int = 3
    # Batched into as few Gemini calls as possible per manuscript, same
    # discipline as V-030's claim-support batching.
    agreement_pairing_max_pairs_per_call: int = 20

    # --- Originality/reuse: embedding pipeline (V-036, F7.1) ------------------
    # Chunk size (words) for chunk-and-average on long text (ticket edge
    # case: 100+ page docs) — bounds how much a single bag-of-tokens vector
    # gets diluted, not a hard model input limit (measured: potion-base-8M
    # encodes a 200,000-word string without erroring).
    reuse_embedding_chunk_words: int = 800

    # --- Originality/reuse: similarity query + write-back (V-037, F7.2/F7.3) --
    # Calibrated empirically against real paired text (ticket file, not
    # guessed): identical text -> 1.0, a lightly-edited near-duplicate
    # (resubmission-shaped edits) -> ~0.98, same-topic-but-genuinely-
    # different-wording -> ~0.63, unrelated -> ~0.21. 0.95 sits comfortably
    # below every real near-duplicate measured and well above the safe
    # same-topic zone.
    reuse_exact_duplicate_threshold: float = 0.95
    # Same calibration: 0.85 sits well above the ~0.63 same-topic ceiling
    # measured, so a same-topic-but-original manuscript never crosses it
    # (false-accusation guard, charter judgment #1).
    reuse_high_similarity_threshold: float = 0.85

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
        "llm_model_pool_file",
        "forensics_keywords_file",
        mode="before",
    )
    @classmethod
    def _blank_path_is_none(cls, value: object) -> object:
        # `INGEST_PATTERNS_FILE=` (blank, as .env.example ships it) must
        # mean "unset": Path("") normalizes to Path(".") which is truthy,
        # so without this the loader would try to read a directory.
        return None if value == "" else value

    @model_validator(mode="after")
    def _weight_importance_ratios_are_ordered(self) -> "Settings":
        # backend-critic finding (BUG-052 review): `weight_importance.py`
        # checks `low_max_ratio` first, then `high_min_ratio`, else `med`
        # -- an operator swapping these in `.env` (or setting them equal)
        # would make `med` unreachable and silently relabel genuinely-high
        # weight criteria as "low". Caught at startup, not discovered live.
        if self.weight_importance_low_max_ratio >= self.weight_importance_high_min_ratio:
            raise ValueError(
                "weight_importance_low_max_ratio must be less than weight_importance_high_min_ratio"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
