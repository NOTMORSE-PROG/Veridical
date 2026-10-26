"""F8/4h/4l/4w: bounded native evidence work on recorded references.

These primitives do not authorize a request. The service caller must retain
source access until the shielded native work has drained, then recheck access.
"""

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.errors import ConflictError, RateLimitedError
from app.ingest.schemas import SectionTree
from app.report.evidence_placement import (
    EvidencePlacementOut,
    ReferencePageImage,
    pin_pdf_source,
    render_pdf_evidence,
)
from app.report.evidence_references import EvidenceSourceReference
from app.report.source_files import manuscript_source_file

_admission_lock = threading.Lock()
_active_readers = 0


@contextmanager
def evidence_image_admission(settings: Settings):
    """Reject before opening source bytes; never queue unbounded native work."""
    global _active_readers
    with _admission_lock:
        if _active_readers >= settings.evidence_source_slots:
            raise RateLimitedError("Source images are busy. Try again shortly.")
        _active_readers += 1
    try:
        yield
    finally:
        with _admission_lock:
            _active_readers -= 1


@dataclass(frozen=True)
class EvidenceSourceFile:
    """Only storage attributes, copied on the event loop under read_source."""

    file_ref: str
    purged_at: object


def read_bounded_source_bytes(path: Path, settings: Settings) -> bytes:
    limit = settings.max_upload_mb * 1024 * 1024
    if path.stat().st_size > limit:
        raise ConflictError("The source exceeds the evidence-reading limit.")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ConflictError("The source exceeds the evidence-reading limit.")
    return data


def unavailable_image(reference: EvidenceSourceReference, reason: str) -> ReferencePageImage:
    return ReferencePageImage(
        EvidencePlacementOut(
            reference_id=reference.reference_id, state="unavailable", reason=reason
        ),
        None,
    )


def render_recorded_evidence_image(
    source_file: EvidenceSourceFile,
    reference: EvidenceSourceReference,
    section_tree: SectionTree,
    settings: Settings,
    *,
    llm_mode: str,
) -> ReferencePageImage:
    """Caller owns evidence_image_admission through shielded work completion."""
    if reference.source_state != "not_checked":
        return unavailable_image(reference, reference.source_state)
    path, source_format = manuscript_source_file(source_file, settings)
    if source_format != "pdf":
        # A DOCX paragraph never acquires a fabricated page image.
        return unavailable_image(reference, "location_unavailable")
    data = read_bounded_source_bytes(path, settings)
    with pin_pdf_source(data, reference.source_sha256, settings) as source:
        return render_pdf_evidence(source, reference, section_tree, settings, llm_mode=llm_mode)
