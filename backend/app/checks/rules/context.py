"""Builds the `RuleContext` every structural rule runs against, from the
DB (manuscript + citations) and the ingestion raw store (V-004's
ExtractionResult) — the one place a rule's inputs are assembled, so the
rules themselves stay pure functions with no DB/filesystem access."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checks.rules import RuleContext
from app.config import Settings, get_settings
from app.ingest.service import load_raw_store_async
from app.models.citation import Citation
from app.models.manuscript import Manuscript


async def build_rule_context(
    session: AsyncSession, manuscript: Manuscript, settings: Settings | None = None
) -> RuleContext:
    settings = settings or get_settings()
    extraction = await load_raw_store_async(settings, manuscript.id)
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
        citations=list(citations),
        margin_tolerance_pts=settings.structural_margin_tolerance_pts,
        citation_style_min_ratio=settings.structural_citation_style_min_ratio,
        table_caption_min_ratio=settings.structural_table_caption_min_ratio,
    )
