"""V086: bind the licensed sample's complete quotations to original page pixels.

This is an offline public-fixture preparation tool, not manuscript analysis.
Only whitespace is collapsed. Every non-space character in an exact match must
have a native glyph box; repeated/absent quotations get no exact highlight.
The existing native export renderer supplies page pixels and cleans annotations.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.config import Settings  # noqa: E402
from app.report.sample_page_images import _render_page_png  # noqa: E402
import pymupdf  # noqa: E402

PUBLIC = ROOT / "frontend/public/sample-assets"
SOURCE = PUBLIC / "stock-inventory-capstone.pdf"
EXPECTED_SHA = "ef662fb9cb5619945c69183a8576f3f945109adc51bff408a6cd6fc21626c5fd"
MANIFEST = ROOT / "frontend/src/demo/sourceEvidence.json"
IMAGE_DIRECTORY = PUBLIC / "evidence"
MAX_IMAGES = 32
MAX_PIXELS = 4_000_000


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def native_characters(page: pymupdf.Page):
    characters = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                characters.extend(
                    (char["c"], tuple(char["bbox"])) for char in span["chars"]
                )
            characters.append(("\n", None))
    text, mapping = [], []
    for character, box in characters:
        if character.isspace():
            if text and text[-1] != " ":
                text.append(" ")
                mapping.append(None)
        else:
            # A glyph may contain multiple Unicode characters; retain each.
            for unit in character:
                text.append(unit)
                mapping.append(box)
    return "".join(text).strip(), mapping


def source_boxes(page: pymupdf.Page, quote: str):
    text, mapping = native_characters(page)
    needle = normalized(quote)
    hits = [match.start() for match in re.finditer(f"(?={re.escape(needle)})", text)]
    if len(hits) != 1:
        return [], f"Complete quotation has {len(hits)} native occurrences; page only."
    start = hits[0]
    glyphs = [
        mapping[start + index]
        for index, char in enumerate(needle)
        if not char.isspace()
    ]
    if not glyphs or any(box is None for box in glyphs):
        return [], "Complete glyph coverage unavailable; page only."
    lines: dict[tuple[float, float], list[tuple]] = {}
    for box in glyphs:
        lines.setdefault((round(box[1], 2), round(box[3], 2)), []).append(box)
    height = page.rect.height
    boxes = []
    for values in lines.values():
        x0, y0 = min(v[0] for v in values), min(v[1] for v in values)
        x1, y1 = max(v[2] for v in values), max(v[3] for v in values)
        boxes.append([x0, height - y1, x1, height - y0])
    return boxes, "Complete quotation uniquely bound to original native glyphs."


def prepare():
    original = SOURCE.read_bytes()
    if hashlib.sha256(original).hexdigest() != EXPECTED_SHA:
        raise ValueError(
            "Original sample hash mismatch; no image preparation permitted"
        )
    pages = json.loads((PUBLIC / "manuscript-text.json").read_text(encoding="utf-8"))
    page_text = {item["page"]: normalized(item["text"]) for item in pages}

    def sentence(page: int, start: str, end: str) -> str:
        text = page_text[page]
        first = text.index(start)
        last = text.index(end, first) + len(end)
        return text[first:last]

    records = []

    def add(kind, subject, page, quote, label, role="single"):
        records.append(
            dict(
                subject={"kind": kind, "id": subject},
                page=page,
                quote=quote,
                anchor=f"page {page}",
                label=label,
                role=role,
            )
        )

    # Preserve each originally saved criterion entry, then add actual saved
    # quotations needed to inspect the existing narrative review notes.
    add(
        "criterion",
        1,
        109,
        "efficient database integration",
        "Conclusion claim",
        "criterion_evidence",
    )
    add(
        "criterion",
        1,
        5,
        sentence(5, "This project develops", "financial transactions."),
        "Abstract: implemented console system",
        "criterion_evidence",
    )
    add(
        "criterion",
        1,
        29,
        sentence(29, "File-Based Storage:", "concurrent access."),
        "Limitations: no database",
        "criterion_evidence",
    )
    add(
        "criterion",
        1,
        37,
        'FILE *file = fopen("items.txt", "w");',
        "Source listing: text-file persistence",
        "criterion_evidence",
    )
    add(
        "criterion",
        1,
        103,
        sentence(
            103,
            "Relational Database Implementation:",
            "(MySQL, PostgreSQL, or SQLite).",
        ),
        "Future scope: database migration",
        "criterion_evidence",
    )
    add(
        "criterion",
        2,
        23,
        "User Testing: Real users (admin & staff) will test the full workflow",
        "Evaluation: planned user testing",
        "criterion_evidence",
    )
    add(
        "criterion",
        2,
        32,
        "Total Components: 32",
        "Summary: declared component total",
        "criterion_evidence",
    )
    add(
        "criterion",
        2,
        33,
        "Business Rule Validation Partial Basic Fair",
        "Summary continuation: component ratings",
        "criterion_evidence",
    )
    add(
        "criterion",
        3,
        3,
        "The result embodied in this project report have not been submitted top any other",
        "Declaration: proofreading example",
        "criterion_evidence",
    )
    add(
        "criterion",
        4,
        2,
        "TABLE OF CONTENT",
        "Document organization",
        "criterion_evidence",
    )
    # Additional source passages explicitly referenced by the completed review.
    # Append within each criterion so existing evidence ordinals remain stable.
    add("criterion", 1, 20, sentence(20, "Develop a web-based", "employees."), "Methodology: proposed web interface", "criterion_evidence")
    add("criterion", 1, 20, sentence(20, "Build a relational database", "transaction data."), "Methodology: proposed database", "criterion_evidence")
    add("criterion", 2, 7, sentence(7, "Research and case studies show", "operational efficiency."), "Research claim requiring identified studies", "criterion_evidence")
    add("criterion", 2, 109, "The proper guidelines from Our instructor Md. Sakib Ali Mazumder sir.", "Published reference section", "criterion_evidence")
    add("criterion", 3, 4, sentence(4, "I would also like", "Software Engineering."), "Acknowledgement: mixed pronouns", "criterion_evidence")
    add("criterion", 3, 22, "Software develpoers: 15000-22000/= BDT", "Budget: spelling example", "criterion_evidence")
    add("criterion", 4, 6, "To generate alerts and reports based on inventory thresholds.", "Objectives: inspect the next empty list item", "criterion_evidence")
    add("criterion", 4, 4, "LOGO", "Acknowledgement: logo heading", "criterion_evidence")
    add("criterion", 4, 109, "The proper guidelines from Our instructor Md. Sakib Ali Mazumder sir.", "Reference section organization", "criterion_evidence")
    add(
        "flag",
        1,
        29,
        sentence(29, "File-Based Storage:", "concurrent access."),
        "Statement A: implementation limitation",
        "statement_a",
    )
    add(
        "flag",
        1,
        109,
        sentence(109, "With secure transaction handling,", "high loads."),
        "Statement B: conclusion claim",
        "statement_b",
    )
    add(
        "flag",
        2,
        20,
        "Develop a web-based inventory system with role-based access control for admin and",
        "Statement A: proposed interface",
        "statement_a",
    )
    add(
        "flag",
        2,
        5,
        sentence(5, "This project develops", "financial transactions."),
        "Statement B: described implementation",
        "statement_b",
    )
    add("flag", 3, 32, "Total Components: 32", "Summary total", "statement_a")
    add(
        "flag",
        3,
        33,
        "Business Rule Validation Partial Basic Fair",
        "Table continuation (count context)",
        "statement_b",
    )
    add(
        "flag",
        4,
        7,
        "Research and case studies show that integrating inventory systems with cloud platforms and",
        "Unidentified supporting-study claim",
    )
    add(
        "flag",
        4,
        109,
        "The proper guidelines from Our instructor Md. Sakib Ali Mazumder sir.",
        "Reference section as published",
        "context",
    )
    if len(records) > MAX_IMAGES:
        raise ValueError("Sample evidence image cap exceeded")
    IMAGE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    base_settings = Settings(_env_file=None)
    settings = SimpleNamespace(
        export_page_image_dpi=base_settings.export_page_image_dpi,
        export_page_image_max_pixels=MAX_PIXELS,
        export_box_line_merge_tolerance=base_settings.export_box_line_merge_tolerance,
        export_page_image_max_height=base_settings.export_page_image_max_height,
        export_page_disclosure_font_pt=base_settings.export_page_disclosure_font_pt,
    )
    counts = {}
    with pymupdf.open(stream=original, filetype="pdf") as doc:
        for record in records:
            subject = record["subject"]
            identity = f"{subject['kind']}:{subject['id']}"
            index = counts.get(identity, 0)
            counts[identity] = index + 1
            record.update(
                check_run_id=1,
                manuscript_id=1,
                source_sha256=EXPECTED_SHA,
                evidence_index=index,
                paragraph=None,
                quote_sha256=hashlib.sha256(record["quote"].encode()).hexdigest(),
            )
            record["evidence_id"] = (
                "e-"
                + hashlib.sha256(
                    json.dumps(record, sort_keys=True).encode()
                ).hexdigest()[:24]
            )
            page = doc[record["page"] - 1]
            if normalized(record["quote"]) not in normalized(page.get_text()):
                raise ValueError(
                    f"Complete quote missing from source: {identity} page {record['page']}"
                )
            boxes, note = source_boxes(page, record["quote"])
            record.update(
                precision="page_bbox" if boxes else "page_only",
                boxes=boxes,
                precision_note=note,
            )
            dpi = settings.export_page_image_dpi
            if page.rect.width * page.rect.height * (dpi / 72) ** 2 > MAX_PIXELS:
                raise ValueError("Native page render exceeds sample pixel cap")
            png = _render_page_png(
                page,
                tuple(tuple(box) for box in boxes),
                llm_disclosure=(
                    "AI review prepared in this session.",
                    "caution",
                ),
                settings=settings,
            )
            image_id = "e-" + hashlib.sha256(png).hexdigest()[:24]
            (IMAGE_DIRECTORY / f"{image_id}.png").write_bytes(png)
            record.update(
                image_id=image_id,
                image_width=int.from_bytes(png[16:20], "big"),
                image_height=int.from_bytes(png[20:24], "big"),
                image_sha256=hashlib.sha256(png).hexdigest(),
                image_path=f"/sample-assets/evidence/{image_id}.png",
                public_sample=True,
            )
    manifest = dict(
        schema_version=1,
        source_sha256=EXPECTED_SHA,
        check_run_id=1,
        manuscript_id=1,
        normalization="Whitespace collapse only; complete unique native glyph occurrence.",
        renderer_sha256=hashlib.sha256(
            (ROOT / "backend/app/report/sample_page_images.py").read_bytes()
        ).hexdigest(),
        entries=records,
    )
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if SOURCE.read_bytes() != original:
        raise ValueError("Original bytes changed")
    print(
        json.dumps(
            dict(
                images=len(records),
                exact=sum(bool(item["boxes"]) for item in records),
                original_unchanged=True,
            )
        )
    )


if __name__ == "__main__":
    prepare()
