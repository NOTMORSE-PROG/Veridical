"""Ingestion orchestration: file → extraction → DB row + raw store.

The ingest stage of the pipeline (ENGINEERING.md §4). Extraction itself is
CPU-bound and runs in the default threadpool; this module owns the status
transitions on the manuscript row and the raw-store write.
"""

import asyncio
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app import messages
from app.config import Settings, get_settings
from app.errors import FileMalformedError
from app.ingest import docx, pdf, references, vision
from app.ingest.patterns import load_patterns
from app.ingest.schemas import ExtractionResult
from app.llm import LLMNotConfiguredError, get_llm_client
from app.models.citation import Citation
from app.models.enums import IngestStatus
from app.models.manuscript import Manuscript

# One extractor per supported suffix. Each is a sync callable executed off
# the event loop. Legacy .doc is deliberately absent: rejected with a clear
# user-fixable message (V-005 edge case).
EXTRACTORS: dict[str, Callable[[str, Settings], ExtractionResult]] = {
    ".pdf": pdf.extract_document,
    ".docx": docx.extract_document,
}


def raw_store_path(settings: Settings, manuscript_id: int) -> Path:
    return settings.data_dir / f"{manuscript_id}.extraction.json"


def select_extractor(suffix: str) -> Callable[[str, Settings], ExtractionResult]:
    extractor = EXTRACTORS.get(suffix.lower())
    if extractor is None:
        raise FileMalformedError(
            messages.UNSUPPORTED_FILE_TYPE.format(
                suffix=suffix or "(none)", supported=", ".join(sorted(EXTRACTORS))
            )
        )
    return extractor


async def ingest_manuscript(
    session: AsyncSession,
    manuscript: Manuscript,
    file_path: Path,
    settings: Settings | None = None,
) -> ExtractionResult:
    settings = settings or get_settings()
    extractor = select_extractor(file_path.suffix)

    manuscript.ingest_status = IngestStatus.processing
    await session.commit()
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, extractor, str(file_path), settings)
        patterns = load_patterns(settings.ingest_patterns_file)
        drafts = await loop.run_in_executor(None, references.extract_references, result, patterns)
        try:
            # Vision pass (F1.3) merges image tables/equations into result
            # BEFORE the raw store is written, so the store is complete.
            await vision.read_images(
                result, file_path, get_llm_client(settings), patterns, settings
            )
        except LLMNotConfiguredError:
            # No client (real mode before V-009): image content stays
            # unread — an honest state, never an ingestion failure.
            result.vision_status = "unavailable"
        await loop.run_in_executor(
            None, _write_raw_store, result, raw_store_path(settings, manuscript.id)
        )
    except Exception:
        # Stage boundary (CODING.md §2): the failure is recorded on the row,
        # then propagates — run-level stage bookkeeping arrives with V-018.
        manuscript.ingest_status = IngestStatus.failed
        await session.commit()
        raise

    # Re-ingest replaces the previous citation set (idempotent).
    await session.execute(delete(Citation).where(Citation.manuscript_id == manuscript.id))
    session.add_all(Citation(manuscript_id=manuscript.id, **asdict(draft)) for draft in drafts)
    manuscript.section_tree = result.section_tree.model_dump()
    manuscript.ingest_status = IngestStatus.done
    await session.commit()
    return result


def _write_raw_store(result: ExtractionResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(), encoding="utf-8")
