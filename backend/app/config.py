"""Application settings, loaded from environment variables / .env.

Every variable here must also be documented in .env.example (repo root).
The app must boot with NO .env present: every field has a safe default,
and nothing secret is required unless a live integration is actually used.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Checked in order; in Docker/CI variables come from the process env.
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    veridical_env: str = "dev"
    # BUG-418: the commit this process was built from, reported on /health so a
    # stale production backend is visible (CI deploy and the production smoke
    # test compare it with main). Render sets RENDER_GIT_COMMIT on every build;
    # DEPLOYED_COMMIT overrides it elsewhere. Empty when unknown (local dev).
    deployed_commit: str = Field(
        default="", validation_alias=AliasChoices("DEPLOYED_COMMIT", "RENDER_GIT_COMMIT")
    )
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
    # Replace pooled database connections older than this (Neon closes idle
    # ones server-side); -1 keeps them indefinitely.
    db_pool_recycle_seconds: int = Field(default=300, ge=-1)
    # Reuse a /health database check this many seconds (0 = check on every
    # request). Platform probes on a small host otherwise pay a new TLS
    # connection each time.
    health_db_check_cache_seconds: float = Field(default=0, ge=0, le=300)
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
    # Maximum PDF hyperlinks admitted for metadata/clip processing (all kinds),
    # not a native parser memory cap. Destinations are never fetched.
    ingest_uri_max_links: int = Field(default=2000, gt=0)
    ingest_uri_max_chars: int = Field(default=4096, gt=0)
    # Guarded source-preserving ordering. New unresolved pages fail closed;
    # legacy extractions are unknown, never retrospectively labelled ordered.
    ingest_reading_order_strategy: Literal["legacy", "native_regions_v1"] = "native_regions_v1"
    # Optional local-cache PDF reading aid; these bound Python read/output work,
    # not native PDF decoding memory or execution time.
    pdf_page_text_slots: int = Field(default=1, gt=0)
    evidence_source_slots: int = Field(default=1, gt=0)
    evidence_source_max_highlight_boxes: int = Field(default=128, gt=0)
    # Retained encoded PNG output, not the sum of discarded compression work.
    evidence_source_max_rendered_bytes: int = Field(default=16_777_216, gt=0)
    # Charge raster reservations even when a later byte cap withholds the image.
    evidence_source_max_rendered_pixels: int = Field(default=48_000_000, gt=0)
    evidence_source_max_extraction_bytes: int = Field(default=33_554_432, gt=0)
    evidence_source_rate_limit_max_attempts: int = Field(default=120, gt=0)
    pdf_page_text_raw_max_bytes: int = Field(default=33554432, gt=0)
    pdf_page_text_max_blocks: int = Field(default=256, gt=0)
    pdf_page_text_max_chars: int = Field(default=40000, gt=0)
    pdf_page_text_max_bytes: int = Field(default=262144, ge=1024)
    pdf_page_text_rate_limit_max_attempts: int = Field(default=120, gt=0)
    ingest_reading_order_max_lines_per_page: int = Field(default=4000, gt=0)
    ingest_reading_order_max_nodes_per_page: int = Field(default=8000, gt=0)
    ingest_reading_order_max_depth: int = Field(default=32, gt=0)
    ingest_reading_order_max_pair_comparisons: int = Field(default=100000, gt=0)
    # Adapter work limits, not OS bounds on native parsing/table detection.
    ingest_native_table_order_enabled: bool = True
    ingest_native_table_max_tables_per_page: int = Field(default=16, gt=0)
    ingest_native_table_max_cells_per_page: int = Field(default=4096, gt=0)
    ingest_native_table_max_characters_per_page: int = Field(default=100000, gt=0)
    # Geometry comparisons use unrotated, crop-relative PDF points. Small
    # typographic offsets are allowed within a row, not between text flows.
    ingest_reading_order_geometry_tolerance_pts: float = Field(default=0.01, ge=0)
    ingest_reading_order_row_tolerance_pts: float = Field(default=2.0, ge=0)
    ingest_reading_order_direction_tolerance: float = Field(default=0.01, ge=0, lt=1)
    # Font boxes overlap during ordinary leading. Reject coincident flows
    # closer than this many smaller-font ems, not every box intersection.
    ingest_reading_order_min_baseline_separation_em: float = Field(default=1.0, gt=0)
    # Optional CPU-only PDF table/figure worker, installed separately. Disabled
    # on small hosted APIs; no model download or provider request on ingestion.
    ingest_recovery_python: Path | None = None
    ingest_recovery_models_dir: Path | None = None
    ingest_recovery_manifest_file: Path | None = None
    ingest_recovery_max_pages: int = Field(default=8, gt=0)
    ingest_recovery_timeout_seconds: float = Field(default=120.0, gt=0)
    ingest_recovery_cleanup_timeout_seconds: float = Field(default=5.0, gt=0)
    ingest_recovery_memory_mb: int = Field(default=2048, gt=0)
    ingest_recovery_poll_seconds: float = Field(default=0.1, gt=0)
    ingest_recovery_threads: int = Field(default=2, gt=0)
    ingest_recovery_max_output_mb: int = Field(default=16, gt=0)
    ingest_recovery_max_regions_per_page: int = Field(default=64, gt=0)
    ingest_recovery_max_cells_per_table: int = Field(default=2000, gt=0)
    ingest_recovery_max_fragments_per_cell: int = Field(default=12, gt=0)
    ingest_recovery_max_mapping_comparisons: int = Field(default=100000, gt=0)
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
    ingest_figure_reading_enabled: bool = True
    ingest_figure_max_images: int = Field(default=2, ge=0)
    ingest_figure_caption_lookahead: int = Field(default=4, ge=0, le=50)
    # Embedded images smaller than this (PDF bbox area, square points) are
    # decorative — icons, bullets, logos — and never worth a call.
    # 5000 pt² ≈ a 1in × 1in figure.
    ingest_vision_min_area_pts: float = 5000.0
    # Rendering DPI for the cropped image sent to the model.
    ingest_vision_crop_dpi: int = 150
    # Legacy multipart ceiling. Large manuscripts use the separate direct
    # storage limit and isolated native-compute path.
    max_upload_mb: int = 40
    # SHA-256 reads are chunked so exact-file identity never requires the
    # complete manuscript in hashing memory. The legacy recovery command
    # processes at most this many rows per explicit invocation; it never
    # turns an upload request into a corpus scan (BUG-235).
    content_hash_read_chunk_bytes: int = 1_048_576
    content_hash_read_max_chunk_bytes: int = 8_388_608
    content_hash_backfill_batch_size: int = 25
    content_hash_backfill_max_batch_size: int = 100
    content_hash_backfill_process_ceiling: int = 1_000
    # Manuscript list paging is consumed by both the Review Desk and New
    # Check picker. The picker still requests the upper bound in one call;
    # keep both values configurable so that contract can be tightened when
    # server-side picker pagination ships without changing application code.
    manuscript_list_default_page_size: int = 50
    manuscript_list_max_page_size: int = 200
    # Audit history has its own paging policy because it can grow far beyond
    # the manuscript list and exposes the full traceability record.
    audit_list_default_page_size: int = 50
    audit_list_max_page_size: int = 200
    audit_list_max_page: int = 10_000
    # DOCX is a zip archive — `max_upload_mb` only caps the COMPRESSED size
    # on disk. A crafted archive can expand far beyond that in memory
    # during parsing (zip-bomb class risk, BUG-005/D-020). This caps total
    # UNCOMPRESSED member size before python-docx ever opens the archive.
    # BUG-159: the previous value (500MB) did NOT bound anything against
    # Render's 512MB free-tier ceiling as its own comment claimed -- that
    # ceiling is for the WHOLE CONTAINER, not the DOCX, and this file's own
    # measurements 300+ lines below record only ~160-196MB of real headroom
    # once the process's own baseline (rendering, code) is accounted for. A
    # 500MB-permitted expansion would OOM the dyno long before this guard
    # ever tripped. 150MB sits inside that measured headroom with margin to
    # spare, while remaining far above any real manuscript's embedded
    # images (this repo's own on-record capstones run single-digit MB
    # uncompressed). `_check_uncompressed_size` (`ingest/docx.py`) also now
    # verifies the REAL decompressed byte count, not just this declared
    # ceiling -- `info.file_size` is attacker-controlled central-directory
    # metadata a crafted archive can understate.
    max_docx_uncompressed_mb: int = 150
    # Bound directory allocation separately: empty ZIP members evade a
    # decompressed-content budget. Enforced before ZipFile construction.
    docx_zip_directory_max_bytes: int = Field(default=1_048_576, ge=1)
    # Native DOCX raster display only, never an opt-in to model assessment.
    # Per-source bounds complement the archive's actual-decompression guard.
    docx_image_max_bytes: int = Field(default=16_777_216, ge=1)
    docx_image_max_pixels: int = Field(default=25_000_000, ge=1)
    docx_image_max_count: int = Field(default=200, ge=1)
    docx_image_max_metadata_segments: int = Field(default=2048, ge=1)
    docx_image_total_max_bytes: int = Field(default=67_108_864, ge=1)
    # Image source navigation stops at the requested drawing. Bound both the
    # decompressed XML prefix and element count independently of upload limits.
    docx_image_source_xml_max_bytes: int = Field(default=16_777_216, ge=1)
    docx_image_source_xml_max_nodes: int = Field(default=200_000, ge=1)
    docx_source_image_rate_limit_max_attempts: int = Field(default=400, ge=1)
    # BUG-180: `max_upload_mb` bounds the wrong dimension for a PDF -- a
    # 50,000-page, 15.9MB file (well inside the 40MB cap) took 6.1s to
    # parse and produced 1.29M chars; measured linear scaling projects
    # ~304M chars and ~337,500 reuse-corpus passage rows at the 40MB
    # ceiling from a single deliberately-constructed upload (a real 40MB
    # PDF is usually images, not this many pages of dense text). 500 is
    # generous headroom, not a tight fit: the real capstone manuscripts
    # already on record in this repo's own tickets run 47 (BUG-043/049,
    # the owner's own capstone), 80 (V-017's substitute manuscript), and
    # 150 pages (BUG-155's extrapolation base) -- 3.3x-10.6x under this
    # cap. Checked BEFORE any page is parsed, so the adversarial cost is
    # paid for zero pages, not the whole document.
    ingest_max_pages: int = 500
    # Wall-clock ceiling on the extraction call (`pdf.py`/`docx.py`, run in
    # the default threadpool with no timeout before this ticket). Honest
    # limitation, disclosed rather than overclaimed: `asyncio.wait_for`
    # bounds how long the REQUEST waits, not how long the underlying
    # synchronous CPU work in the threadpool actually runs -- Python
    # cannot forcibly cancel a running thread. `ingest_max_pages` above is
    # the fix that actually bounds the work; this is a second, independent
    # backstop for the case where per-page cost is unexpectedly high even
    # under that cap (e.g. deeply nested structures PyMuPDF still has to
    # walk). Matches `gemini_request_timeout_seconds`'s own value as a
    # reasonable per-call ceiling.
    ingest_extraction_timeout_seconds: float = 60.0
    # One supported preparation owner per process while native PDF parsing
    # uses threads. This is admission control, not a measured memory guarantee.
    # A cancelled awaiter retains admission until its native work finishes.
    document_preparation_slots: int = Field(default=1, ge=1, le=1)
    # BUG-208 / D-028: current Library preparation and persistence use the
    # same nonnegative prefix budget, with measured indexing scope saved at
    # activation. Checked-manuscript passages are never cut by this setting.
    # Inventory counting and document/chapter work have separate costs.
    # Historical pre-D-028 BUG-180 rationale follows:
    # `store_passage_embeddings` (F7.4) had no cap on how many
    # rows one manuscript writes into the GLOBALLY SHARED reuse corpus --
    # at `reuse_passage_chunk_words: 150`, an oversized upload becomes
    # hundreds of thousands of embedded rows degrading match quality and
    # query time for every other account, permanently. Measured (real
    # `extract_document` + real chunker, four data points, cleanly
    # linear): 1500 pages -> 8,798 passages, so 5000 is generous headroom
    # above what `ingest_max_pages` (500) should ordinarily produce for a
    # PDF (~2,900 passages at the measured ~5.9/page). Truncates (keeps
    # the first N in document order), never rejects the check -- unlike
    # the ingestion-time caps above, this fires deep into an already-
    # passing check run, and throwing away real structural/semantic
    # results over an internal corpus-writing limit would be a worse
    # instructor experience than a manuscript's own later passages
    # quietly not becoming part of what FUTURE manuscripts get compared
    # against (this run's OWN flags are unaffected -- the query against
    # the existing corpus already ran on every passage, untruncated,
    # before this cap is ever applied; recorded honestly either way via
    # `passages_found`/`passages_archived` in the check's own detail,
    # never silently -- BUG-096 already fixed exactly this failure class
    # once, a partial result rendered indistinguishable from a complete
    # one).
    #
    # Disclosed gap, not fixed here (backend review finding, BUG-180
    # review): this cap bounds what gets WRITTEN, not what gets COMPUTED
    # -- `compute_passage_embeddings` still runs on the full, untruncated
    # passage list before this cap is ever applied, so the CPU cost this
    # was partly meant to bound is not actually capped by this setting.
    # For a PDF, `ingest_max_pages` keeps this narrow (a 500-page PDF
    # produces far fewer passages than this cap). The real residual risk
    # is the DOCX path, which has no page-count-equivalent gate at all --
    # `max_docx_uncompressed_mb` (BUG-159, fixed) now correctly bounds the
    # UNPACKED archive size against this dyno's real measured memory
    # headroom, but that guards decompression memory, not extracted TEXT
    # volume or passage count; a dense, text-heavy DOCX well under that
    # ceiling could still drive this same unbounded-compute cost. Follow-up:
    # BUG-208.
    reuse_max_passages_per_manuscript: int = Field(default=5000, ge=0)

    # --- LLM queue (V-009, ENGINEERING §3) -----------------------------------
    # Legacy compatibility setting; the application factory routes through the
    # configured model pool. A requested ID does not prove serving identity.
    # July observations are historical; see the pool's dated lifecycle review.
    gemini_model: str = "gemini-3.5-flash"
    gemini_temperature: float = 0.0
    # Per-attempt network timeout for a Gemini call.
    gemini_request_timeout_seconds: float = 60.0
    # Override requested membership, order and conservative local budgets.
    # Blank uses app/llm/data/model_pool.json, including historical aliases.
    # Configured totals do not establish current provider allowance.
    llm_model_pool_file: Path | None = None
    # Legacy single-model local limits, retained from July 26 observations.
    # The previous ~1,500/day assumption was retracted then; these defaults
    # are not a fresh measurement or provider entitlement.
    llm_rpm: int = 10
    llm_daily_quota: int = 20
    # Timezone Gemini resets against — NOT local time (ticket V-009 edge case).
    llm_quota_reset_timezone: str = "America/Los_Angeles"
    llm_max_retries: int = 3
    llm_cache_contract_version: str = "evidence-v2"
    semantic_evidence_scope_patterns_file: Path = (
        Path(__file__).parent / "checks" / "data" / "semantic_evidence_scopes.json"
    )
    llm_retry_base_seconds: float = 1.0
    # BUG-162: the response cache (D-011) is keyed on prompt content, not
    # check_run_id, by design -- so a normal re-run of the same manuscript
    # against the same rubric version is a cache REPLAY, not an independent
    # regrading. That's correct for saving quota, but it means there was no
    # supported way to actually MEASURE run-to-run grading stability (V3's
    # own exit checkpoint). Off by default (every existing deployment keeps
    # today's cache-saving behavior unchanged); set only for a deliberate
    # stability probe (backend/scripts/stability_probe.py) -- only cache READS are
    # skipped (forcing a real call every time), writes still land in
    # `llm_response_cache` as normal, so a probe run leaves the cache in a
    # normal, usable state afterward rather than disturbing it.
    llm_cache_bypass: bool = False
    # Input-token estimate used only to pace calls under a model's per-minute
    # token budget (model_pool.json "tpm"); conservative, not a measurement.
    llm_chars_per_token: float = Field(default=4.0, gt=0)
    llm_tokens_per_image: int = Field(default=258, ge=0)
    # Full-source semantic review is never silently truncated. Larger inputs
    # remain explicitly unassessed until a source-bound retrieval path exists.
    # 450k characters covers a ~250-page capstone (a real 187-page capstone
    # measured 254k on 2026-10-29); large prompts are paced by the model
    # pool's per-minute token budget.
    semantic_context_max_chars: int = Field(default=450_000, ge=1)
    # Pages whose reading order is unresolved (complex tables, overlapping
    # layouts) are left out of rubric grading and the integrity checks, and
    # disclosed on every result, while they are at most this share of the
    # manuscript; above it those analyses do not run.
    analysis_unread_pages_max_ratio: float = Field(default=0.25, ge=0, le=1)
    semantic_batch_max_criteria: int = Field(default=20, ge=1)
    semantic_batch_validation_attempts: int = Field(default=2, ge=1, le=2)
    semantic_item_recovery_max_criteria: int = Field(default=20, ge=0)
    # An evidence proposal that only adds a leading/trailing omission mark or
    # differs from the source only by rendering (ligatures, case, line-break
    # spacing) is stored as the source's own text when the remaining core is
    # at least this long and has one supported location; shorter cores stay
    # rejected.
    semantic_omission_core_min_chars: int = Field(default=40, ge=1)
    # A grading quotation found verbatim at several locations (an abstract
    # restated in Chapter 1, a journal-article copy in an appendix) is anchored
    # to its first location when it is at least this long; a shorter phrase at
    # several locations stays ambiguous and is not stored as evidence.
    semantic_repeated_quote_min_chars: int = Field(default=80, ge=1)
    semantic_grading_response_contract: str = Field(
        default="indexed-item-recovery-v1", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )

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
    # A section word count that misses its stated limit by no more than this
    # many words is escalated, not failed: word-count tools disagree on
    # hyphenated terms, numerals and keyword lines.
    structural_word_count_tolerance_words: int = Field(default=5, ge=0)
    # A counted section spanning more pages than this means its end could
    # not be confirmed; the rule escalates rather than count into the next.
    structural_word_count_max_span_pages: int = Field(default=3, ge=1)
    # A section title may wrap over at most this many extracted lines when the
    # word-count rule locates its heading on the page.
    structural_word_count_heading_max_lines: int = Field(default=3, ge=1)

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

    # Draft-scale/checklist safety gates, unchanged from the evaluated v4 candidate.
    rubric_feedback_criterion_max_chars: int = Field(default=60, ge=10)
    rubric_atomicity_min_items: int = Field(default=10, ge=1)
    rubric_atomicity_min_ratio: float = Field(default=0.35, ge=0.0, le=1.0)
    rubric_list_item_pattern: str = (
        r"^\s*(?:[\u2022\u25e6\u25aa\u2023\u2219\u25cb\u25cf\u25a0\u25a1\u2610"
        r"\u2713\u2714\ue000-\uf8ff]|\d{1,2}[.)](?!\d))(?:\s|$)"
    )

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
    judgment_revision_reason_max_length: int = Field(default=1000, gt=0)
    judgment_history_page_size: int = Field(default=20, gt=0)
    judgment_history_max_page_size: int = Field(default=100, gt=0)
    judgment_history_cursor_max_length: int = Field(default=1024, gt=0)

    # --- Check-run orchestration (V-018, ENGINEERING §4) ---------------------
    # How often the background worker polls for the next runnable
    # check_run when it isn't actively advancing one (simplest job runner
    # per the ticket's own research note — no paid queue infrastructure).
    pipeline_worker_poll_seconds: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    # An api_down stage doesn't have a precise reset time the way quota
    # does (D-001) — retry after a fixed backoff instead.
    pipeline_api_down_retry_seconds: float = 300.0
    # BUG-144: a `check_run.claimed_at` older than this is treated as
    # abandoned (the process holding it crashed mid-run) and can be
    # re-claimed by either driver -- without this, one crash mid-run would
    # strand that check_run claimed forever, unpickable by anything.
    # A separate short-session renewal task retains ownership during long
    # modules; stage boundaries additionally check the current fencing token.
    pipeline_claim_stale_seconds: float = Field(default=600.0, gt=0, allow_inf_nan=False)
    pipeline_claim_renew_seconds: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    pipeline_coordination_timeout_seconds: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    # Two-int32 transaction advisory key space, separate from manuscript locks.
    pipeline_admission_lock_namespace: int = Field(default=427, ge=-(2**31), lt=2**31)
    pipeline_admission_lock_key: int = Field(default=1, ge=-(2**31), lt=2**31)
    # BUG-181: per-CALL bounds exist (`gemini_request_timeout_seconds`,
    # `external_http_timeout_seconds`, `ingest_extraction_timeout_seconds`)
    # but nothing bounded the WHOLE run -- a run makes many calls across
    # many criteria/checks, and with no run-level deadline a single
    # stuck-but-technically-progressing run could occupy the single-worker
    # queue (D-001, one run at a time on the free dyno) far longer than any
    # real manuscript needs, starving every other instructor queued behind
    # it. 45 minutes is deliberately generous, not a tight fit: even a
    # pathological case (dozens of criteria, every self-consistency vote
    # needing its own tie-break, every call taking the full
    # `gemini_request_timeout_seconds` ceiling) sits well under this, and a
    # REAL run's actual call latencies are a small fraction of their own
    # timeout ceilings in practice. Checked at the same stage-boundary
    # checkpoint cancellation already uses (`_transition_after_boundary`)
    # -- a run that exceeds it fails honestly (charter rule 9) via the
    # existing `TerminalFailure` machinery, the same path every other
    # genuine failure already uses, rather than hanging with no deadline
    # to break the queue.
    pipeline_run_deadline_seconds: float = Field(default=2700.0, ge=0, allow_inf_nan=False)
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
    # Service outages are distinct from quota exhaustion and never imply that
    # an AI judgment was recorded. This reason can persist before later stages.
    pipeline_api_down_degraded_reason: str = (
        "Not graded by AI. The AI service was unavailable, so no completed grade "
        "was recorded for this criterion. AI grading of this criterion will not "
        "retry automatically in this run. If a report is produced, review the "
        "manuscript and record your own judgment there. You can also start a new check later."
    )
    pipeline_ai_not_configured_degraded_reason: str = (
        "Not graded by AI. No usable AI configuration was available for this check, "
        "so no completed AI grade was recorded for this criterion. AI grading of "
        "this criterion will not retry automatically in this run. If a report is "
        "produced, review the manuscript and record your own judgment there. You "
        "can also start a new check after AI configuration is available."
    )
    pipeline_ai_key_rejected_degraded_reason: str = (
        "Not graded by AI. The configured AI key was rejected during an AI request, "
        "and no applicable alternative completed that request. No completed AI "
        "grade was recorded for this criterion. AI grading of this criterion will "
        "not retry automatically in this run. If a report is produced, review the "
        "manuscript and record your own judgment there. You can also start a new "
        "check after AI access is restored."
    )
    # The scheduler always waits for accepted-request wakeups. Production
    # independently polls durable queued work (BUG-136); this flag enables
    # that periodic recovery outside production as an explicit local opt-in.
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
    # Owner decision 2026-10-29: "decide" grades every criterion the AI can
    # ground in the manuscript (majority of passes; a three-way split takes
    # the middle level; a single verified pass or an unverified tie-break
    # takes the lower level; an unconfirmed support check stays a disclosed
    # caution) and the instructor changes any grade they disagree with.
    # "escalate" keeps the earlier policy: any disagreement or unconfirmed
    # support waits for the instructor. Nothing is decided without at least
    # one pass whose quotes are verified in the manuscript.
    grading_decision_policy: Literal["escalate", "decide"] = "escalate"

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
    # in-depth against one actor exhausting the configured shared local
    # budget. This throttle does not establish provider capacity. 20/hour is generous
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
    citation_doi_providers: list[Literal["crossref", "datacite"]] = Field(
        default=["crossref", "datacite"], min_length=1
    )
    datacite_base_url: str = "https://api.datacite.org"
    datacite_rpm: int = Field(default=60, ge=1)
    # Semantic Scholar (RESEARCH.md §2): the unauthenticated shared pool is
    # generously documented (5,000 req/5min) but genuinely shared/unreliable
    # in practice, so the no-key path self-throttles to 1 rps regardless —
    # matching what an authenticated key's search-endpoint limit would give
    # anyway. Empty key = unauthenticated path (works today, no owner action
    # needed to build against); set the env var once a key is issued
    # (V-028's own pending action item) for a higher ceiling, no code change.
    semantic_scholar_api_key: str | None = None
    semantic_scholar_rps: float = 1.0
    # Open Library: configured ceiling, with conservative unidentified use
    # unless an operator opts in with a nonplaceholder contact address.
    openlibrary_rps: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    openlibrary_unidentified_rps: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    openlibrary_identified_requests: bool = False
    openlibrary_placeholder_contact_domains: list[str] = [
        "example.com",
        "example.net",
        "example.org",
        "invalid",
        "localhost",
        "test",
    ]
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
    numeric_citation_context_file: Path | None = None
    numeric_citation_max_range_span: int = Field(default=20, gt=0)
    reference_metadata_max_chars: int = Field(default=8192, ge=256, le=32768)
    reference_doi_max_geometry_comparisons: int = Field(default=100000, gt=0)
    reference_metadata_patterns_file: Path | None = None
    # Longer unseparated natural names may contain merged authors. Retain raw
    # display text but do not assert an author count or complete metadata.
    reference_unseparated_author_max_tokens: int = Field(default=3, ge=1, le=20)
    citation_author_max_chars: int = Field(default=160, ge=40, le=1000)
    citation_author_max_tokens: int = Field(default=8, ge=2, le=40)
    citation_unseparated_author_max_tokens: int = Field(default=3, ge=1, le=20)
    citation_parenthetical_group_max_chars: int = Field(default=4000, ge=200, le=20000)
    citation_parenthetical_entry_max_chars: int = Field(default=320, ge=40, le=2000)
    citation_claim_excerpt_max_chars: int = Field(default=480, ge=80, le=4000)
    citation_in_text_max_matches: int = Field(default=2000, ge=1, le=10000)
    citation_wrap_font_size_tolerance_pt: float = Field(default=0.5, ge=0.0, le=4.0)

    # Exact-DOI metadata title policy. Packaged default if unset; changing a
    # custom policy requires a process restart. Each comparison saves its hash.
    citation_metadata_title_policy_file: Path | None = None
    citation_doi_source_url_template: str = "https://doi.org/{doi}"
    citation_registry_record_url_templates: dict[str, str] = {
        "crossref": "https://api.crossref.org/works/{doi}",
        "datacite": "https://api.datacite.org/dois/{doi}",
    }
    citation_cache_stale_days: int = 30
    # Claim-support check (V-030, F5.4): batched into as few Gemini calls
    # as possible per manuscript (D-001/D-011). Local NLI was measured and
    # ruled out (DECISIONS.md D-011 follow-up: 487MB peak RSS, doesn't fit
    # Render's 512MB) — this is the Tier-2 fallback D-011 itself named.
    claim_support_max_pairs_per_call: int = 20
    # Total eligible occurrence/reference pairs admitted before batching. Overflow
    # remains explicitly unassessed; this is not a representative sample.
    claim_support_max_pairs_per_run: int = Field(default=20, ge=1)

    # --- Statistical forensics: extraction (V-031, F6.1) ----------------------
    # Override the packaged table-column-header synonym list without a code
    # change; empty = packaged default (same convention as structural_keywords_file).
    forensics_keywords_file: Path | None = None
    forensics_sprite_policy_file: Path | None = None
    forensics_sprite_max_rows: int = Field(default=3, ge=1)
    forensics_sprite_max_input_rows: int = Field(default=50, ge=1)
    forensics_sprite_max_receipt_rows: int = Field(default=50, ge=1)
    forensics_sprite_max_observations: int = Field(default=1000, ge=2)
    forensics_sprite_max_scale_points: int = Field(default=1000, ge=2)
    forensics_sprite_max_iterations: int = Field(default=2000, ge=1)
    forensics_sprite_row_timeout_seconds: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    forensics_sprite_check_timeout_seconds: float = Field(default=9.0, gt=0, allow_inf_nan=False)
    forensics_sprite_math_threads: int = Field(default=1, ge=1)
    forensics_sprite_random_seed: int = Field(default=0, ge=0, le=4294967295)
    forensics_sprite_init_method: Literal["minvar", "maxvar"] = "minvar"

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

    # --- Originality/reuse: similarity query (V-037, F7.2; corpus = Library, V-077) --
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

    # --- Anchor-to-region recovery (V-065 Q2 / shared with V-070) -------------
    # Candidate excerpt lengths tried, longest first, against PyMuPDF's
    # page.search_for() to recover a bounding box from a flag's stored
    # evidence_excerpt. Measured 2026-08-19 against 34 real flags on a real
    # 47-page manuscript: verbatim-quote flags hit at full length; a
    # progressively shorter prefix is tried because real excerpts are often
    # truncated with an ellipsis or wrap mid-line in ways search_for won't
    # match verbatim. Below 8 chars a "match" is too likely to be
    # coincidental to trust (`region_search_min_candidate_chars` below).
    # Comma-separated raw string, same convention as `cors_allowed_origins`:
    # pydantic-settings JSON-decodes complex env fields before any validator
    # runs, which breaks on a plain CSV value from .env.
    region_search_candidate_lengths: str = "400,120,80,40"
    region_search_min_candidate_chars: int = 8

    @property
    def region_search_candidate_lengths_list(self) -> list[int]:
        return [
            int(n.strip()) for n in self.region_search_candidate_lengths.split(",") if n.strip()
        ]

    # --- Report export: rendered page images (V-070) ---------------------------
    # DPI for PyMuPDF's page.get_pixmap() when embedding a flagged manuscript
    # page in the exported PDF. The "506.4MB, 5.6MB headroom" figure D-011/
    # V-065 cite as this codebase's in-process memory baseline is STALE --
    # it measured an onnxruntime/NLI cross-encoder stack that D-011's own
    # text says was measured, rejected, and REMOVED (`onnxruntime` is
    # confirmed absent from pyproject.toml/uv.lock today; backend review
    # independently re-verified this claim, 2026-08-25).
    #
    # TWO real, dated, in-process (app + potion-base-8M loaded) measurements
    # on 2026-08-25, reported separately rather than blended into one range
    # (backend review finding: a prior draft of this comment blended them
    # into a single inconsistent number that disagreed with the CHANGELOG
    # entry describing the same work):
    #   1. Random 28-page sample of the owner's real 47-page manuscript,
    #      150dpi: baseline (app+model loaded) ~294-315MB working set,
    #      +~36-57MB rendering, peaking ~342-351MB (~160-170MB headroom).
    #   2. The 17 REAL flags of a real, live check_run against that same
    #      manuscript, run through the actual `render_flag_page_images_for`
    #      code path: peaked at 316MB working set (~196MB headroom).
    # Both real-manuscript measurements land well under half the 512MB
    # ceiling. They predate V-085, which moved export rendering to
    # `evidence_images.build_recorded_evidence_images`; re-measure that path
    # before relying on them. `tests/test_report_export_perf.py::
    # test_page_image_rendering_stays_within_a_measured_native_memory_bound`
    # is the REPRODUCIBLE backstop for this claim going forward -- run it
    # rather than trusting this comment's numbers as they age.
    # 150dpi is legible print quality without spending that headroom faster
    # than needed.
    export_page_image_dpi: int = Field(default=150, gt=0)
    # After each evidence page an export renders (evidence_images.py), pause
    # for this multiple of the time it took (0 = no pause). On a host with a
    # fraction of one CPU the renders otherwise starve request handling.
    export_render_pause_ratio: float = Field(default=0.0, ge=0, le=5)
    export_numeric_detail_decimal_places: int = Field(default=1, ge=0, le=10)
    # Bound each complete source-plus-disclosure RGB raster before allocation.
    # Independent of the distinct-page cap: a single unusually large PDF page
    # must not allocate an unbounded canvas. Unavailable images retain a reason.
    export_page_image_max_pixels: int = Field(default=25_000_000, ge=1)
    # Safety valve, not expected to bind on a real manuscript: caps the
    # number of DISTINCT PAGES one export will rasterize -- every flag
    # anchored to an already-rendered page is free, REGARDLESS of whether
    # its own excerpt/box differs from another flag's on that same page
    # (`app/report/page_images.py`'s cache is keyed by page number alone,
    # not by page+excerpt -- backend review finding, 2026-08-25: keying
    # by (page, boxes) let a single heavily-flagged page consume the whole
    # cap by itself, so a 2-page document could report "rendered-page limit
    # reached" on its second, otherwise-untouched page). At the measured
    # ~0.5-2MB incremental peak per page (150dpi, depending on page
    # complexity), 80 pages stays well inside the measured headroom above,
    # with real margin left for a second concurrent request. A flag whose
    # page is skipped past the cap gets an honest "skipped" reason (AC3),
    # never a silent drop.
    export_max_page_images: int = 80
    # F8.6: one disposable prepared PDF, independent of proxy request deadlines.
    export_job_ttl_seconds: float = Field(default=600, gt=0)
    export_job_max_bytes: int = Field(default=33_554_432, gt=0)
    export_job_poll_seconds: float = Field(default=3, ge=1, le=30)
    export_job_wait_seconds: float = Field(default=900, ge=30, le=3600)
    # Tolerance (PDF points) for merging same-line word-run boxes that
    # `page.search_for()` returns as separate rects into one continuous
    # highlight per line. Measured live (2026-08-25) against a real quoted
    # excerpt: a multi-word phrase search returns one rect PER WORD-RUN on
    # the same line, not one rect for the whole phrase -- drawing each
    # separately is technically correct but reads as a broken/partial mark
    # on the printed page. Two boxes are "the same line" when their y0
    # (page-top-origin space) differs by less than this.
    export_box_line_merge_tolerance: float = 2.0
    # Max height (points) reserved for one embedded page image, independent
    # of its width-fit scale -- A4's own usable content height (topMargin
    # 98 + bottomMargin 56 subtracted from A4's ~842pt) is ~688pt, and an
    # image scaled to the full content WIDTH alone can exceed that on its
    # own (a near-A4-aspect page scaled to ~487pt content width comes out
    # ~689pt tall). Capping height independently guarantees room is always
    # left for the flag's own header/excerpt above the image on the same
    # page, never a reportlab layout error from a single flowable too tall
    # to place.
    export_page_image_max_height: float = Field(default=420.0, gt=0, allow_inf_nan=False)
    # Desired EFFECTIVE (embedded/on-page) size of the fake/unknown-`llm_mode`
    # disclosure band's text, in points -- `app/report/page_images.py`
    # derives the RASTER's own native fontsize from this and the page's
    # native height so the text reads at roughly this size regardless of
    # how tall the source page is (design review finding, 2026-08-25: a
    # flat native fontsize shrinks proportionally with the page and
    # measured ~4.8pt -- smaller than every other text in the document --
    # on a real Letter-height source page scaled to fit
    # `export_page_image_max_height`). 8pt matches this document's own
    # smallest normal caption size (`export.py`'s `caption` style).
    export_page_disclosure_font_pt: float = Field(default=8.0, gt=0, allow_inf_nan=False)

    # --- Originality/reuse: passage-level similarity (V-072, F7.4) ------------
    # Passage size for the F7.4 chunker -- deliberately smaller than
    # `reuse_embedding_chunk_words` (800, F7.1's dilution bound for
    # whole-doc/chapter averaging). V-065's own research (Q4, 2026-08-19)
    # measured ~120-word real passages through potion-base-8M separating
    # nearly as cleanly as the chapter-level calibration; 150 gives a little
    # headroom above that measured floor without approaching chapter size.
    reuse_passage_chunk_words: int = 150
    # Bound each local encoder call without truncating the manuscript's
    # passage inventory. Total output storage still grows with passage count.
    reuse_passage_embedding_batch_size: int = Field(default=512, ge=1)
    # Bounded context window (words each side of the exact matched passage)
    # persisted alongside it at archive-build time -- the bounded-excerpt
    # rule (owner ruling, carried from V-058/BUG-050 Branch B) requires a
    # match to show "a bounded, configurable window of context, never the
    # full archived document." Computed and stored once, not read live from
    # the matched manuscript's file (V-065 Q1's RAM ruling applies equally
    # here -- no cross-account file access at all for the matched side).
    reuse_passage_context_words: int = 60
    # Same calibration V-065 measured (Q4): passage-level cosine separation
    # spans nearly the chapter-level dynamic range, so these start at the
    # SAME values as `reuse_exact_duplicate_threshold`/
    # `reuse_high_similarity_threshold` -- kept as distinct settings (not
    # reused directly) because passage-level is a genuinely different
    # granularity and may need independent tuning later without touching
    # the chapter/whole-doc thresholds it was copied from.
    reuse_passage_exact_duplicate_threshold: float = 0.95
    reuse_passage_high_similarity_threshold: float = 0.85

    # --- Library (V-066, F7.1/F7.2 surfaces) -----------------------------------
    # How many chapters' worth of passage excerpt the library shows for a
    # manuscript the viewing instructor does NOT own -- the bounded,
    # CONFIGURABLE excerpt Q2's ruling requires (owner, 2026-08-11, carried
    # from V-058/BUG-050 Branch B): identity plus a bounded window, never
    # the full document. One representative passage per chapter, capped at
    # this many chapters, so the excerpt scales with how many chapters
    # exist without ever approaching a full read of a long manuscript.
    library_excerpt_max_chapters: int = 5
    # V-077 (D-028): Library submissions spend zero Gemini quota (image
    # reading is skipped; text and embeddings are local), but each one is real
    # CPU and storage work on the single free-tier worker. Their own budget,
    # separate from `action_rate_limit_max_attempts`, because seeding a past
    # term can legitimately mean dozens of files in one sitting. Same window
    # as the shared action limiter. Judgment value, not a measured number.
    # 60/hour covers a past term in one sitting while bounding worst-case
    # storage at 60 x `max_upload_mb` per account per hour (security review,
    # 2026-10-13: 200/hour allowed ~8 GB/hour against a 10 GB free tier).
    library_submission_rate_limit_max_attempts: int = 60
    # A Library entry still "processing" after this long is treated as
    # stalled (its request died mid-way): it no longer blocks re-adding the
    # same file and its owner may remove it. Generous against the extraction
    # timeout so a slow but live upload is never mistaken for a dead one.
    library_processing_stalled_seconds: int = 600
    # Upper bound on authors stored per Library entry (title-page proposals
    # and instructor edits alike).
    library_entry_max_authors: int = 20

    # --- Block-quote detection (V-072, F7.4 exclusion) -------------------------
    # PDF only (no bbox exists for DOCX blocks, `ingest/schemas.py`'s own
    # TextBlock.bbox comment: "PDF only: layout position" -- an honest gap,
    # not built for DOCX this ticket). A block's left edge (x0) sitting this
    # many points right of its chapter's own body-text left margin is
    # classified as a block quote candidate -- same "hanging indent vs body"
    # signal `app/ingest/references.py`'s entry segmentation already uses,
    # just inverted (there, indent continues an entry; here, indent marks a
    # quoted block). 20pt sits comfortably above APA's standard 0.5in (36pt)
    # block-quote indent's own rendering jitter floor while staying well
    # below a full paragraph indent misfire.
    block_quote_indent_threshold_points: float = 20.0
    # Below this length, an indented block is more likely a run-in list
    # item or a short label than a quoted passage worth excluding from
    # F7.4 candidacy at all.
    block_quote_min_words: int = 15

    # --- Durable object storage (BUG-138) -------------------------------------
    # Render's free-tier disk is EPHEMERAL -- wiped on every redeploy and every
    # 15-minute-idle wake, so it can never be the only copy of an uploaded
    # manuscript or its derived extraction JSON. "local" (default) keeps
    # today's dev/CI behavior: `data_dir` IS the store, zero credentials
    # needed. "r2" additionally persists both to Cloudflare R2 -- chosen
    # 2026-09-02 (10 GB-month free storage, free egress at any volume) over
    # Neon-in-DB storage (0.5GB/project, already shared with the reuse
    # archive's embeddings) and Render's paid persistent disk (ground rule 2).
    # `data_dir` stays the local CACHE in both modes -- see `app/storage/`.
    storage_backend: str = "local"
    # Use a distinct object namespace for every nonproduction database. Empty
    # preserves the existing production layout; maintenance access is explicit.
    storage_namespace: str = ""
    storage_allow_unscoped_access: bool = False
    source_reader_max_mb: int = Field(default=256, gt=0)
    source_reader_memory_threshold_bytes: int = Field(default=1_048_576, ge=0)
    # Longest a transaction holding a manuscript's source lock may sit idle
    # while the source is rendered or streamed; 0 keeps the server's limit.
    # Seconds, not the server's milliseconds: the cap rejects that mix-up at
    # startup instead of failing every source read.
    source_read_idle_transaction_timeout_seconds: int = Field(default=1800, ge=0, le=86400)

    @field_validator("storage_namespace")
    @classmethod
    def validate_storage_namespace(cls, value: str) -> str:
        if value and (
            value in (".", "..") or value != value.strip() or any(c in value for c in "/\\:\x00")
        ):
            raise ValueError("Storage namespace must be one directory name or empty")
        return value

    # Large manuscript cache fills must not allocate the complete object in RAM.
    storage_download_chunk_bytes: int = Field(default=1_048_576, ge=1, le=8_388_608)
    storage_transfer_chunk_bytes: int = Field(default=8_388_608, ge=5_242_880, le=16_777_216)
    # Optional isolated native compute. Large files stay in private R2; only
    # signed metadata crosses this HTTP boundary. Empty keeps local operation.
    native_compute_url: str | None = None
    native_compute_secret: str | None = None
    native_compute_timeout_seconds: float = Field(default=240, gt=0, le=270)
    native_compute_job_ttl_seconds: int = Field(default=300, ge=30, le=600)
    native_compute_runtime_limit_seconds: int = Field(default=300, ge=300)
    native_compute_align_clock: bool = False
    native_compute_max_request_bytes: int = Field(default=131_072, gt=0)
    native_compute_max_source_mb: int = Field(default=256, gt=0)
    native_compute_max_result_mb: int = Field(default=32, gt=0)
    native_compute_max_crop_mb: int = Field(default=16, gt=0)
    native_compute_max_crop_pixels: int = Field(default=25_000_000, gt=0)
    native_compute_results_subdir: str = "native_compute"
    direct_manuscript_upload_enabled: bool = False
    direct_upload_transport: Literal["r2", "relay"] = "r2"
    direct_upload_relay_transfers: int = Field(default=1, ge=1)
    # Owner's ceiling for one manuscript (2026-10-29: "make the limit 150mb").
    direct_manuscript_upload_max_mb: int = Field(default=150, gt=0)
    # Long enough for a 150 MB file on a slow home uplink; every presigned URL
    # is still bound to one staging key and one exact byte length.
    direct_upload_url_ttl_seconds: int = Field(default=3600, ge=60, le=3600)
    # Browser-to-R2 multipart: R2 needs at least 5 MiB per part and equal sizes
    # except the last. Parallel parts keep a slow link busy, and a failed part
    # is resent alone instead of restarting the whole file.
    direct_upload_part_mb: int = Field(default=8, ge=5, le=512)
    direct_upload_part_concurrency: int = Field(default=4, ge=1, le=16)
    direct_upload_part_retries: int = Field(default=3, ge=0, le=10)
    # Relay streaming: parts waiting behind the one being stored. Bounds memory
    # to (this + 2) x STORAGE_TRANSFER_CHUNK_BYTES while a client is sending.
    direct_upload_relay_queued_parts: int = Field(default=1, ge=1, le=4)
    # A streamed upload that sends nothing for this long is dropped, so a
    # stalled or closed client cannot keep the relay slot.
    direct_upload_relay_idle_seconds: float = Field(default=60, gt=0)
    # Whole-file SHA-256 results reused while the file is unchanged on disk.
    source_digest_memo_entries: int = Field(default=64, ge=1)
    # V-090 upload tray: finished attempts stay listed this long so a reload
    # can show what happened; the list is capped for one screen.
    direct_upload_tray_window_seconds: int = Field(default=86400, ge=60)
    direct_upload_tray_max_items: int = Field(default=20, ge=1, le=200)
    direct_upload_pending_per_instructor: int = Field(default=3, gt=0)
    direct_upload_pending_global: int = Field(default=12, gt=0)
    direct_upload_lease_seconds: int = Field(default=1800, ge=600)
    direct_upload_poll_seconds: float = Field(default=3, gt=0)
    direct_upload_staging_subdir: str = "upload_staging"
    direct_upload_staging_retention_seconds: int = Field(default=86400, ge=3600)
    direct_upload_storage_budget_mb: int = Field(default=8192, gt=0)
    direct_upload_cleanup_batch_size: int = Field(default=10, gt=0)

    @field_validator("native_compute_results_subdir", "direct_upload_staging_subdir")
    @classmethod
    def validate_compute_subdir(cls, value: str) -> str:
        if not value or value in (".", "..") or any(c in value for c in "/\\:\x00"):
            raise ValueError("Compute storage subdirectories must be one directory name")
        return value

    manuscript_upload_subdir: str = "uploads"
    rubric_upload_subdir: str = "rubric_uploads"

    @field_validator("rubric_upload_subdir")
    @classmethod
    def validate_rubric_upload_subdir(cls, value: str) -> str:
        if not value or value in (".", "..") or any(c in value for c in "/\\:\x00"):
            raise ValueError("RUBRIC_UPLOAD_SUBDIR must be one directory name")
        return value

    @field_validator("manuscript_upload_subdir")
    @classmethod
    def validate_manuscript_upload_subdir(cls, value: str) -> str:
        if not value or value in (".", "..") or any(c in value for c in "/\\:\x00"):
            raise ValueError("MANUSCRIPT_UPLOAD_SUBDIR must be one directory name")
        return value

    r2_bucket: str | None = None
    r2_endpoint_url: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    # V-078: explicit metadata-only availability probes. Keep these separate
    # from upload/download timeouts; a list must never download an object.
    storage_probe_connect_timeout_seconds: float = Field(default=2.0, gt=0)
    storage_probe_read_timeout_seconds: float = Field(default=2.0, gt=0)
    storage_probe_total_attempts: int = Field(default=1, ge=1)
    # Separate per-instructor scope, using action_rate_limit_window_seconds.
    storage_probe_rate_limit_max_attempts: int = Field(default=60, ge=1)

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

    # --- Programs (V-062) -----------------------------------------------------
    # Comma-separated seed for the `program` reference table (ground rule 7:
    # CS/IT are TIP's programs today, not a permanent fact about the product
    # -- another school, or TIP next year, may need a different list, and
    # that must be a config/data change, never a redeploy). Same raw-string
    # convention as `cors_allowed_origins` above, for the same reason.
    # `scripts/seed_programs.py` reads this and upserts; the migration that
    # first created the table seeds the two values known at the time it was
    # written directly (a historical fact about what shipped), not by
    # reading this setting at migration-run time (BUG-042's lesson: a
    # migration must never read live settings for something it's supposed
    # to lock in, or a stray env var makes different environments diverge
    # silently) -- this setting is for ongoing, post-migration additions.
    default_programs: str = "CS,IT"
    group_create_max_attempts: int = Field(default=3, ge=1)

    @property
    def default_programs_list(self) -> list[str]:
        return [name.strip() for name in self.default_programs.split(",") if name.strip()]

    @field_validator(
        "ingest_patterns_file",
        "ingest_recovery_python",
        "ingest_recovery_models_dir",
        "ingest_recovery_manifest_file",
        "structural_keywords_file",
        "structural_bound_patterns_file",
        "llm_model_pool_file",
        "forensics_keywords_file",
        "forensics_sprite_policy_file",
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
        if (
            2 * (self.pipeline_claim_renew_seconds + self.pipeline_coordination_timeout_seconds)
            >= self.pipeline_claim_stale_seconds
        ):
            raise ValueError("pipeline claim lifetime must allow two bounded renewal attempts")
        # backend review finding (BUG-052 review): `weight_importance.py`
        # checks `low_max_ratio` first, then `high_min_ratio`, else `med`
        # -- an operator swapping these in `.env` (or setting them equal)
        # would make `med` unreachable and silently relabel genuinely-high
        # weight criteria as "low". Caught at startup, not discovered live.
        if self.weight_importance_low_max_ratio >= self.weight_importance_high_min_ratio:
            raise ValueError(
                "weight_importance_low_max_ratio must be less than weight_importance_high_min_ratio"
            )
        if (
            self.manuscript_list_default_page_size < 1
            or self.manuscript_list_max_page_size < 1
            or self.manuscript_list_default_page_size > self.manuscript_list_max_page_size
        ):
            raise ValueError(
                "manuscript_list_default_page_size must be between 1 and "
                "manuscript_list_max_page_size"
            )
        if (
            self.audit_list_default_page_size < 1
            or self.audit_list_max_page_size < 1
            or self.audit_list_default_page_size > self.audit_list_max_page_size
        ):
            raise ValueError(
                "audit_list_default_page_size must be between 1 and audit_list_max_page_size"
            )
        if self.audit_list_max_page < 1:
            raise ValueError("audit_list_max_page must be at least 1")
        if self.judgment_history_page_size > self.judgment_history_max_page_size:
            raise ValueError("judgment_history_page_size must not exceed its maximum")
        if self.judgment_revision_reason_max_length < self.resolution_reason_min_length:
            raise ValueError("judgment revision reason maximum must not be below its minimum")
        if self.content_hash_read_chunk_bytes < 1:
            raise ValueError("content_hash_read_chunk_bytes must be at least 1")
        if (
            self.content_hash_read_max_chunk_bytes < 1
            or self.content_hash_read_chunk_bytes > self.content_hash_read_max_chunk_bytes
        ):
            raise ValueError(
                "content_hash_read_chunk_bytes must be between 1 and "
                "content_hash_read_max_chunk_bytes"
            )
        if self.content_hash_backfill_batch_size < 1:
            raise ValueError("content_hash_backfill_batch_size must be at least 1")
        if (
            self.content_hash_backfill_max_batch_size < 1
            or self.content_hash_backfill_batch_size > self.content_hash_backfill_max_batch_size
        ):
            raise ValueError(
                "content_hash_backfill_batch_size must be between 1 and "
                "content_hash_backfill_max_batch_size"
            )
        if (
            self.content_hash_backfill_process_ceiling < 1
            or self.content_hash_backfill_max_batch_size
            > self.content_hash_backfill_process_ceiling
        ):
            raise ValueError(
                "content_hash_backfill_max_batch_size must not exceed "
                "content_hash_backfill_process_ceiling"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
