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
