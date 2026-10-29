"""F8/4h/4l/4w: bounded native evidence work on recorded references.

These primitives do not authorize a request. The service caller must retain
source access until the shielded native work has drained, then recheck access.
"""

import struct
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.errors import ConflictError, GoneError, RateLimitedError
from app.ingest.schemas import SectionTree
from app.report.evidence_captions import DOCX_CAPTION, placement_caption
from app.report.evidence_placement import (
    EvidencePlacementOut,
    ReferencePageImage,
    locate_pdf_evidence,
    pin_pdf_source_file,
    render_pdf_evidence,
)
from app.report.evidence_references import EvidenceReferenceSnapshot, EvidenceSourceReference
from app.report.page_image_disclosure import PageImageUnavailable
from app.report.schemas import PageEvidenceImageOut, SourceEvidencePrintOut
from app.report.source_files import manuscript_source_file
from app.report.source_snapshot import SourceSnapshot

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
    limit = settings.source_reader_max_mb * 1024 * 1024
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
    with (
        SourceSnapshot(path, reference.source_sha256, settings) as snapshot,
        pin_pdf_source_file(snapshot, settings) as source,
    ):
        return render_pdf_evidence(source, reference, section_tree, settings, llm_mode=llm_mode)


def print_evidence_entry(reference, result, *, note=None, max_print_height=None):
    placement = result.placement
    reason = note or placement_caption(placement.state, placement.reason)
    return SourceEvidencePrintOut(
        reference_id=reference.reference_id,
        criterion_id=reference.criterion_id,
        flag_id=reference.owner_id if reference.owner_kind == "finding" else None,
        role=reference.role,
        index=reference.index,
        quote=reference.quote,
        anchor=reference.anchor,
        placement_state=placement.state,
        image=PageEvidenceImageOut(image_png=result.image_png, page=placement.page, reason=reason),
        max_print_height=max_print_height,
    )


def build_recorded_evidence_images(
    source_file: EvidenceSourceFile,
    snapshot: EvidenceReferenceSnapshot,
    section_tree: SectionTree,
    settings: Settings,
    *,
    llm_mode: str,
    settings_for_reference: Callable[[EvidenceSourceReference], Settings] | None = None,
    print_content_width: float | None = None,
) -> list[SourceEvidencePrintOut]:
    """One pinned source, independent marks, bounded attempted variants/bytes/pixels.

    Caller reserves admission before executor scheduling and drains cancellation
    under the source retention fence. Every reference gets an explicit result.
    """
    snapshot = EvidenceReferenceSnapshot.model_validate(snapshot.model_dump())
    references = snapshot.references

    def missing_all(note):
        return [
            print_evidence_entry(ref, unavailable_image(ref, ref.source_state), note=None)
            if ref.source_state != "not_checked"
            else print_evidence_entry(
                ref, unavailable_image(ref, "location_unavailable"), note=note
            )
            for ref in references
        ]

    if not references:
        return []
    if not any(ref.source_state == "not_checked" for ref in references):
        return missing_all(None)
    try:
        path, source_format = manuscript_source_file(source_file, settings)
        if source_format != "pdf":
            return missing_all(DOCX_CAPTION)
        with (
            SourceSnapshot(path, snapshot.source_sha256, settings) as source_copy,
            pin_pdf_source_file(source_copy, settings) as source,
        ):
            results = []
            attempted = used_bytes = used_pixels = 0
            for reference in references:
                if reference.source_state != "not_checked":
                    results.append(
                        print_evidence_entry(
                            reference, unavailable_image(reference, reference.source_state)
                        )
                    )
                    continue
                if (
                    attempted >= settings.export_max_page_images
                    or used_bytes >= settings.evidence_source_max_rendered_bytes
                    or used_pixels >= settings.evidence_source_max_rendered_pixels
                ):
                    results.append(
                        print_evidence_entry(
                            reference, unavailable_image(reference, "resource_limit")
                        )
                    )
                    continue
                placement = locate_pdf_evidence(source, reference, section_tree, settings)
                if placement.state == "unavailable" or placement.page is None:
                    results.append(
                        print_evidence_entry(reference, ReferencePageImage(placement, None))
                    )
                    continue
                attempted += 1
                try:
                    render_settings = (
                        settings_for_reference(reference) if settings_for_reference else settings
                    )
                except PageImageUnavailable as error:
                    results.append(
                        print_evidence_entry(
                            reference,
                            unavailable_image(reference, "resource_limit"),
                            note=str(error),
                        )
                    )
                    continue
                render_settings = render_settings.model_copy(
                    update={
                        "evidence_source_max_rendered_bytes": min(
                            render_settings.evidence_source_max_rendered_bytes,
                            settings.evidence_source_max_rendered_bytes - used_bytes,
                        ),
                        "evidence_source_max_rendered_pixels": min(
                            render_settings.evidence_source_max_rendered_pixels,
                            settings.evidence_source_max_rendered_pixels - used_pixels,
                        ),
                    }
                )
                result = render_pdf_evidence(
                    source,
                    reference,
                    section_tree,
                    render_settings,
                    llm_mode=llm_mode,
                    print_content_width=print_content_width,
                )
                # Discarding an encoded image does not refund its raster work.
                used_pixels += result.charged_pixels
                if result.image_png is not None:
                    used_bytes += len(result.image_png)
                    width, height = struct.unpack(">II", result.image_png[16:24])
                    if result.charged_pixels == 0:
                        used_pixels += width * height
                results.append(
                    print_evidence_entry(
                        reference,
                        result,
                        max_print_height=render_settings.export_page_image_max_height,
                    )
                )
            return results
    except GoneError as error:
        return missing_all(str(error))
    except ConflictError as error:
        return missing_all(str(error))
