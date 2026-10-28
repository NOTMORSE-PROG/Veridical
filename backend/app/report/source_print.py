"""F8 print-adapted evidence blocks; long quotations split, image identity stays."""

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Spacer, Table, TableStyle

from app.report.evidence_captions import (
    PAGE_ONLY_CAPTION,
    SCOPE_FIRST_PAGE_CAPTION,
    SOURCE_ROLE_LABELS,
    TEXT_LAYER_CAPTION,
)
from app.report.page_image_disclosure import PageImageUnavailable
from app.report.text import Paragraph


def source_identity(criterion_id, flag_id, role, index):
    owner = f"Criterion #{criterion_id}" if criterion_id is not None else f"Record #{flag_id}"
    label = SOURCE_ROLE_LABELS.get(role, "Recorded source")
    return (
        f"{owner} · {label} {index + 1}" if role == "criterion_evidence" else f"{owner} · {label}"
    )


def source_caption(identity, anchor, qualification, *, page=None):
    location = anchor.strip() if anchor and anchor.strip() else "Not recorded"
    origin = f"Rendered from the submitted PDF, page {page}. " if page is not None else ""
    return f"{identity}. {origin}Saved location: {location}. {qualification}"


def source_image_height(reference, frame_width, frame_height, styles, layout, settings):
    identity = source_identity(
        reference.criterion_id,
        reference.owner_id if reference.owner_kind == "finding" else None,
        reference.role,
        reference.index,
    )
    # Reserve the complete adjacent qualification and actual continuation header
    # before fitting a fake/unknown-mode notice into its final printed height.
    captions = (
        source_caption(identity, reference.anchor, qualification, page=settings.ingest_max_pages)
        for qualification in (TEXT_LAYER_CAPTION, PAGE_ONLY_CAPTION, SCOPE_FIRST_PAGE_CAPTION)
    )
    caption_height = max(
        Paragraph(escape(caption), styles["caption"]).wrap(frame_width, frame_height)[1]
        for caption in captions
    )
    header_height = Paragraph(escape(identity + " (continued)"), styles["caption_bold"]).wrap(
        frame_width, frame_height
    )[1]
    remaining = (
        frame_height
        - caption_height
        - header_height
        - layout["source_entry_gap"]
        - layout["image_caption_gap"]
    )
    if remaining <= 0:
        raise PageImageUnavailable(
            "Source image could not fit beside its required caption; "
            "the saved passage remains shown."
        )
    return min(settings.export_page_image_max_height, remaining)


def _atomic_source_group(parts, width):
    group = Table([[parts]], colWidths=[width], splitByRow=1, splitInRow=0, hAlign="LEFT")
    group.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return group


def source_evidence_flowables(
    entry, width, styles, dpi, layout, *, include_quote=True, frame_height=None
):
    identity = source_identity(entry.criterion_id, entry.flag_id, entry.role, entry.index)
    title = Paragraph(escape(identity), styles["body_bold"])
    title.keepWithNext = True
    parts = [title]
    if entry.role == "reuse_match" and entry.image.image_png is None:
        note = Paragraph(escape(entry.image.reason or ""), styles["caption"])
        note.keepWithNext = bool(include_quote and entry.quote)
        parts.append(note)
        if include_quote and entry.quote:
            parts.append(Paragraph(escape(entry.quote), styles["body"]))
        parts.append(Spacer(1, layout["source_entry_gap"]))
        return parts
    if include_quote and entry.quote:
        parts.append(Paragraph(escape(entry.quote), styles["body"]))
    if entry.image.image_png is None:
        parts.append(
            Paragraph(
                escape(
                    source_caption(
                        identity, entry.anchor, entry.image.reason or "No page image is included."
                    )
                ),
                styles["caption"],
            )
        )
        parts.append(Spacer(1, layout["source_entry_gap"]))
        return parts

    native_width, native_height = ImageReader(BytesIO(entry.image.image_png)).getSize()
    native_width *= 72 / dpi
    native_height *= 72 / dpi
    maximum_height = entry.max_print_height
    if maximum_height is None:
        raise ValueError("A source image needs its measured final print-height budget")
    scale = min(width / native_width, maximum_height / native_height, 1.0)
    caption = source_caption(
        identity, entry.anchor, entry.image.reason or "", page=entry.image.page
    )
    # KeepTogether reports a sentinel height and unwraps on split. Inside a PTO
    # container that can detach the caption or introduce an empty page. A single
    # unsplittable, zero-padding row reports its true size to the outer container.
    group = _atomic_source_group(
        [
            Image(
                BytesIO(entry.image.image_png),
                width=native_width * scale,
                height=native_height * scale,
            ),
            Spacer(1, layout["image_caption_gap"]),
            Paragraph(escape(caption), styles["caption"]),
        ],
        width,
    )
    parts.append(group)
    parts.append(Spacer(1, layout["source_entry_gap"]))
    if frame_height is not None:
        complete = _atomic_source_group(parts, width)
        header_height = Paragraph(escape(identity + " (continued)"), styles["caption_bold"]).wrap(
            width, frame_height
        )[1]
        if complete.wrap(width, frame_height)[1] + header_height <= frame_height:
            return [complete]
    return parts
