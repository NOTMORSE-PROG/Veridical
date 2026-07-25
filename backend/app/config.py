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

    @field_validator("ingest_patterns_file", mode="before")
    @classmethod
    def _blank_path_is_none(cls, value: object) -> object:
        # `INGEST_PATTERNS_FILE=` (blank, as .env.example ships it) must
        # mean "unset": Path("") normalizes to Path(".") which is truthy,
        # so without this the loader would try to read a directory.
        return None if value == "" else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
