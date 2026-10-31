"""Builds the `RuleContext` every structural rule runs against, from the
DB (manuscript + citations) and the ingestion raw store (V-004's
ExtractionResult) — the one place a rule's inputs are assembled, so the
rules themselves stay pure functions with no DB/filesystem access."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.rules import RuleContext
from app.checks.rules.reference_inventory import measure_reference_inventory
from app.config import Settings, get_settings
from app.ingest.analytical_source import (
    analytical_source,
    bind_analytical_source,
    section_order_source_complete,
)
from app.ingest.patterns import load_patterns
from app.ingest.service import load_raw_store_async
from app.ingest.vision import images_read_for_tables
from app.models.citation import Citation
from app.models.manuscript import Manuscript


async def build_rule_context(
    session: AsyncSession, manuscript: Manuscript, settings: Settings | None = None
) -> RuleContext:
    settings = settings or get_settings()
    extraction = await load_raw_store_async(settings, manuscript.id)
    extraction = bind_analytical_source(extraction, manuscript.content_hash)
    native_source = analytical_source(extraction)
    citations = (
        (
            await session.execute(
                select(Citation)
                .where(Citation.manuscript_id == manuscript.id)
                .order_by(Citation.order_index)
            )
        )
        .scalars()
        .all()
    )
    return RuleContext(
        manuscript_id=manuscript.id,
        anchor_kind=extraction.anchor_kind,
        page_count=extraction.page_count,
        section_tree=extraction.section_tree,
        geometry=extraction.geometry,
        tables=extraction.tables,
        vision_status=extraction.vision_status,
        has_images=bool(extraction.images),
        images_read_for_tables=images_read_for_tables(extraction),
        citations=list(citations),
        margin_tolerance_pts=settings.structural_margin_tolerance_pts,
        citation_style_min_ratio=settings.structural_citation_style_min_ratio,
        table_caption_min_ratio=settings.structural_table_caption_min_ratio,
        reference_inventory=measure_reference_inventory(
            extraction, list(citations), load_patterns(settings.ingest_patterns_file)
        ),
        section_order_source_complete=section_order_source_complete(extraction),
        native_math_source_detail=analytical_source(extraction).detail(),
        source_identity_verified=(
            extraction.anchor_kind != "paragraph"
            or bool(
                manuscript.content_hash and extraction.docx_source_sha256 == manuscript.content_hash
            )
        ),
        section_order_witnesses_verified=(
            native_source.state in {"complete", "not_applicable"}
            and not native_source.unlocated_occurrences
        ),
        blocks=tuple(extraction.blocks),
        unreliable_pages=frozenset(
            entry.page for entry in extraction.reading_order or () if entry.status != "ordered"
        ),
        word_count_tolerance_words=settings.structural_word_count_tolerance_words,
        word_count_max_span_pages=settings.structural_word_count_max_span_pages,
        word_count_heading_max_lines=settings.structural_word_count_heading_max_lines,
        heading_max_chars=settings.ingest_heading_max_chars,
        heading_bold_ratio_min=settings.ingest_bold_ratio_min,
        heading_size_delta=settings.ingest_heading_size_delta,
    )
