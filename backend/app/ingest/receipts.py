"""One ingestion completion receipt for multipart and durable direct uploads."""

from app import messages
from app.config import get_settings
from app.ingest.patterns import load_patterns
from app.ingest.quality import (
    SOURCE_REFERENCE_BOUNDARY_INGEST_NOTE,
    SOURCE_REFERENCE_PARTIAL_INGEST_NOTE,
    reading_order_ingest_note,
)
from app.ingest.references import select_references
from app.ingest.schemas import ExistingUploadOut, IngestSummary, TitlePageProposalOut


def ingestion_receipt(manuscript, result, citations, proposal, existing):
    notes = [messages.IMAGE_ONLY_NOTE] if result.image_only else []
    if note := reading_order_ingest_note(result, citations):
        notes.append(note)
    selection = select_references(result, load_patterns(get_settings().ingest_patterns_file))
    if citations == 0 and selection.issues:
        notes.append(SOURCE_REFERENCE_BOUNDARY_INGEST_NOTE)
    if citations > 0 and selection.membership_issues:
        notes.append(SOURCE_REFERENCE_PARTIAL_INGEST_NOTE)
    return IngestSummary(
        manuscript_id=manuscript.id,
        group_label=manuscript.group_label,
        ingest_status=manuscript.ingest_status,
        page_count=result.page_count,
        anchor_kind=result.anchor_kind,
        image_only=result.image_only,
        text_chars=result.text_chars,
        images=len(result.images),
        tables=len(result.tables),
        equations=len(result.equations),
        vision_status=result.vision_status,
        citations=citations,
        section_tree=result.section_tree,
        notes=notes,
        group_proposal=TitlePageProposalOut.model_validate(proposal, from_attributes=True),
        existing_upload=(
            ExistingUploadOut(
                manuscript_id=existing.id,
                created_at=existing.created_at,
                original_filename=existing.original_filename,
                purged_at=existing.purged_at,
            )
            if existing
            else None
        ),
    )
