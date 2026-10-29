"""Pure source-image selection and native rasterization (F1); no model or DB imports."""

import hashlib
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

from app.config import Settings
from app.errors import GoneError
from app.ingest.docx_source import read_docx_image
from app.ingest.native_cache import release_native_cache
from app.ingest.patterns import HeadingPatterns
from app.ingest.schemas import ExtractionResult, FigureReading, ImageBlock, SectionNode


def _selected_indices(
    result: ExtractionResult, patterns: HeadingPatterns, settings: Settings
) -> list[int]:
    sized = [
        index
        for index, img in enumerate(result.images)
        if result.anchor_kind == "page"
        and img.page is not None
        and img.bbox is not None
        and _area(img.bbox) >= settings.ingest_vision_min_area_pts
    ]
    priority_pages = _priority_page_ranges(result.section_tree.nodes, patterns)

    def is_priority(img: ImageBlock) -> bool:
        return img.page is not None and any(a <= img.page <= b for a, b in priority_pages)

    ordered = sorted(sized, key=lambda index: not is_priority(result.images[index]))
    return ordered[: settings.ingest_vision_max_images]


def _area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _priority_page_ranges(
    nodes: list[SectionNode], patterns: HeadingPatterns
) -> list[tuple[int, int]]:
    """Page spans of level-1 sections whose titles mention a priority
    keyword (results/analysis/...). Span ends where the next level-1
    section starts; the last one runs to the end of the document."""
    top = [n for n in nodes if n.page is not None]
    ranges: list[tuple[int, int]] = []
    for i, node in enumerate(top):
        title = node.title.casefold()
        if any(k in title for k in patterns.vision_priority_sections):
            end = top[i + 1].page - 1 if i + 1 < len(top) else 10**9
            ranges.append((node.page, max(node.page, end)))
    return ranges


def _crop(file_path: Path, image: ImageBlock, settings: Settings) -> bytes:
    try:
        with pymupdf.open(str(file_path)) as doc:
            page = doc[image.page - 1]
            pix = page.get_pixmap(
                clip=pymupdf.Rect(*image.bbox), dpi=settings.ingest_vision_crop_dpi
            )
            return pix.tobytes("png")
    finally:
        release_native_cache()


def _caption_index(result):
    """Images per page/paragraph and blocks by page/paragraph, built once.

    A long manuscript can hold tens of thousands of image fragments (charts
    drawn as tiles); looking each one's neighbours up by rescanning every
    image made figure preparation quadratic and stalled ingestion."""
    by_page, by_paragraph = defaultdict(list), defaultdict(list)
    for block in result.blocks:
        by_page[block.page].append(block)
        if block.paragraph is not None:
            by_paragraph[block.paragraph].append(block)
    return (
        Counter(item.page for item in result.images),
        Counter(item.paragraph for item in result.images),
        sorted({item.paragraph for item in result.images if item.paragraph is not None}),
        by_page,
        by_paragraph,
    )


def _caption(result, image, patterns, settings, index=None):
    per_page, per_paragraph, image_paragraphs, by_page, by_paragraph = index or _caption_index(
        result
    )
    if image.paragraph is not None:
        if per_paragraph[image.paragraph] != 1:
            return None, None
        position = bisect_right(image_paragraphs, image.paragraph)
        last = image.paragraph + settings.ingest_figure_caption_lookahead
        if position < len(image_paragraphs):
            last = min(last, image_paragraphs[position] - 1)
        blocks = [
            block
            for paragraph in range(image.paragraph, last + 1)
            for block in by_paragraph.get(paragraph, ())
        ]
    else:
        if per_page[image.page] != 1:
            return None, None
        blocks = by_page.get(image.page, [])
    captions = [b for b in blocks if patterns.caption.match(b.text.strip())]
    # Page proximity alone cannot attach one of several captions to a crop.
    if len(captions) != 1:
        return None, None
    block = captions[0]
    anchor = f"paragraph {block.paragraph}" if block.paragraph is not None else f"page {block.page}"
    return block.text, anchor


def prepare_figures(result, patterns, settings, *, enabled=True):
    eligible = []
    priority = _priority_page_ranges(result.section_tree.nodes, patterns)
    caption_index = _caption_index(result)
    for index, image in enumerate(result.images):
        caption, anchor = _caption(result, image, patterns, settings, caption_index)
        supported = image.docx_source is not None or (
            image.page is not None
            and image.bbox is not None
            and _area(image.bbox) >= settings.ingest_vision_min_area_pts
        )
        useful = caption is not None or (
            image.page is not None and any(a <= image.page <= b for a, b in priority)
        )
        if enabled and settings.ingest_figure_reading_enabled and supported and useful:
            eligible.append(index)
    selected = eligible[: settings.ingest_figure_max_images]
    selected_set, eligible_set = set(selected), set(eligible)
    result.figure_readings = []
    for index, image in enumerate(result.images):
        caption, anchor = _caption(result, image, patterns, settings, caption_index)
        result.figure_readings.append(
            FigureReading(
                image_index=index,
                source_anchor=f"paragraph {image.paragraph}"
                if image.paragraph is not None
                else f"page {image.page}"
                if image.page is not None
                else None,
                status="not_selected",
                reason="not_attempted"
                if index in selected_set
                else "selection_limit"
                if index in eligible_set
                else "not_requested"
                if not enabled or not settings.ingest_figure_reading_enabled
                else "source_or_relevance_unavailable",
                source_caption=caption,
                caption_anchor=anchor,
            )
        )
    return selected


def _raster(path, result, index, source_hash, settings):
    if not isinstance(source_hash, str) or not source_hash:
        raise GoneError("The original document hash was not recorded.")
    image = result.images[index]
    if image.docx_source:
        content, media_type = read_docx_image(path, result, index, source_hash, settings)
        # The existing transport accepts PNG. Decode verified raster locally;
        # it neither fetches external image links nor substitutes another part.
        if media_type != "image/png":
            import pymupdf

            with pymupdf.open(stream=content, filetype="jpeg") as doc:
                content = doc[0].get_pixmap().tobytes("png")
        locator = {"paragraph": image.paragraph, "docx_source": image.docx_source.model_dump()}
    else:
        content = _crop(path, image, settings)
        locator = {"page": image.page, "bbox": image.bbox, "dpi": settings.ingest_vision_crop_dpi}
    with path.open("rb") as original:
        if hashlib.file_digest(original, "sha256").hexdigest() != source_hash:
            raise GoneError("The figure source changed during reading.")
    return content, {
        "document_sha256": source_hash,
        "image_sha256": hashlib.sha256(content).hexdigest(),
        "image_index": index,
        "locator": locator,
        "schema_version": "v1",
    }
