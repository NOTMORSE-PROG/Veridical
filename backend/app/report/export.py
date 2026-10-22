"""Report PDF export (F8.6, V-039, export action on screen 4h).

reportlab, not WeasyPrint/headless-chromium -- see
tickets/V6-defense-day/V-039.md's own Pre-implementation research for
the memory-footprint reasoning (Render's 512MB free-tier ceiling).

Layout/typography per design review's spec (2026-08-15): A4, Public Sans
(vendored TTF converted from the frontend's own @fontsource woff2 files --
reportlab needs TTF/OTF, not woff), a print-calibrated type scale, direct
reuse of tokens.css's own AA-checked hex colors, and a triple-redundant
DRAFT marking (watermark + inline badge + footer line) whenever
`decision is None` -- the single, server-enforced invariant
`DecisionPanel.tsx`'s own blocked-state gate already relies on.

Every manuscript/user-derived string is XML-escaped before reaching a
reportlab `Paragraph` (its mini-markup parser chokes on a literal `&`/
`<`/`>`, which real citation excerpts contain) -- found live by
design review against real seeded data, not a hypothetical.

backend review note: the embedded Public Sans TTF renders the middot
separator (`·`, U+00B7) correctly -- visually confirmed by rendering
real exports to PNG. PyMuPDF's `get_text()` extracts that SAME glyph as
U+FFFD, a cmap-lookup quirk of this specific subset font, not a real
corruption. Visual rendering is the authority for this font; a bare `�`
in a `get_text()`-based test assertion is not automatically evidence of
a bug -- check the actual rendered page before treating one as real.
"""

import contextlib
import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    CondPageBreak,
    HRFlowable,
    Image,
    KeepTogether,
    Paragraph,
    PTOContainer,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.checks.escalation import NEEDS_REVIEW_OUTCOMES as _NEEDS_REVIEW_OUTCOMES_ENUM
from app.config import get_settings
from app.report.schemas import (
    CriterionResultOut,
    FlagExportDetailOut,
    FlagSummaryOut,
    PageEvidenceImageOut,
    ReportExportData,
)

_FONTS_DIR = Path(__file__).parent / "fonts"
_FONT_FAMILY = "PublicSans"
_FALLBACK_FAMILY = "Helvetica"  # WinAnsiEncoding covers the same Filipino-name diacritics.

_fonts_registered = False


def _register_fonts() -> str:
    """Registers the vendored Public Sans TTFs once per process; returns
    the family name to use (falls back to Helvetica, disclosed, if the
    vendored files are ever missing rather than crashing the export)."""
    global _fonts_registered
    if _fonts_registered:
        return _FONT_FAMILY
    try:
        pdfmetrics.registerFont(TTFont(_FONT_FAMILY, str(_FONTS_DIR / "PublicSans-Regular.ttf")))
        pdfmetrics.registerFont(
            TTFont(f"{_FONT_FAMILY}-Bold", str(_FONTS_DIR / "PublicSans-Bold.ttf"))
        )
        pdfmetrics.registerFont(
            TTFont(f"{_FONT_FAMILY}-Italic", str(_FONTS_DIR / "PublicSans-Italic.ttf"))
        )
        pdfmetrics.registerFont(
            TTFont(f"{_FONT_FAMILY}-BoldItalic", str(_FONTS_DIR / "PublicSans-BoldItalic.ttf"))
        )
        pdfmetrics.registerFontFamily(
            _FONT_FAMILY,
            normal=_FONT_FAMILY,
            bold=f"{_FONT_FAMILY}-Bold",
            italic=f"{_FONT_FAMILY}-Italic",
            boldItalic=f"{_FONT_FAMILY}-BoldItalic",
        )
        _fonts_registered = True
        return _FONT_FAMILY
    except Exception:
        return _FALLBACK_FAMILY


# Direct reuse of tokens.css's own AA-checked hex values (DESIGN.md §1.3)
# -- never re-derived, so the export can't silently disagree with the app.
_INK = colors.HexColor("#14161a")
_INK_SECONDARY = colors.HexColor("#4b5162")
_INK_TERTIARY = colors.HexColor("#5b6272")
_RULE = colors.HexColor("#dbdee5")
_ZEBRA = colors.HexColor("#f7f8fa")
_DRAFT_GRAY = colors.HexColor("#8992a6")

# Central print-layout values for 4k. Footer height is also measured below;
# wrapped disclosures increase the body reservation instead of overlapping it.
_PRINT_LAYOUT = {
    "side_margin": 54,
    "bottom_margin": 84,
    "footer_bottom": 16,
    "footer_row_gap": 1,
    "footer_body_gap": 16,
    "header_identity_gap": 20,
    "minimum_evidence_lines": 2,
}


@lru_cache(maxsize=1)
def _problem_labels() -> dict[str, str]:
    # Paired contract test against the screen's domain labels prevents copy drift.
    return json.loads((Path(__file__).parent / "data" / "problem_labels.json").read_text("utf-8"))


@lru_cache
def _coverage_labels() -> dict[str, str]:
    return json.loads((Path(__file__).parent / "data" / "coverage_labels.json").read_text("utf-8"))


@lru_cache
def _image_coverage_labels() -> dict[str, str]:
    return json.loads(
        (Path(__file__).parent / "data" / "image_coverage_labels.json").read_text("utf-8")
    )


def _issue_title(issue: str | None) -> str:
    if not issue:
        return "Recorded finding"
    return _problem_labels().get(issue, f"Recorded issue: {issue}")


def _fit_running_identity(text: str, font: str, size: float, width: float) -> str:
    """Measured elision is only for repeated running identity, never source evidence."""
    if pdfmetrics.stringWidth(text, font, size) <= width:
        return text
    suffix = "..."
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if pdfmetrics.stringWidth(text[:middle] + suffix, font, size) <= width:
            low = middle
        else:
            high = middle - 1
    return text[:low] + suffix


_TONE_COLORS = {
    "success": (colors.HexColor("#e3f6f4"), colors.HexColor("#0d6e64")),
    "caution": (colors.HexColor("#f6e9f2"), colors.HexColor("#7a3566")),
    "danger": (colors.HexColor("#fbeae9"), colors.HexColor("#b3261e")),
    "neutral": (colors.HexColor("#f1f2f5"), colors.HexColor("#5b6272")),
    "info": (colors.HexColor("#e7eefa"), colors.HexColor("#2456a8")),
}

READINESS_LABEL = {
    "ready": "Ready",
    "conditionally_ready": "Conditionally Ready",
    "not_ready": "Not Ready",
    "needs_review": "Needs Review",
}
READINESS_TONE = {
    "ready": "success",
    "conditionally_ready": "caution",
    "not_ready": "danger",
    "needs_review": "neutral",
}
DECISION_LABEL = {
    "approved": "Approved for defense",
    "returned": "Returned for revision",
    "rejected": "Rejected",
}
DECISION_TONE = {"approved": "success", "returned": "caution", "rejected": "danger"}
RESOLUTION_VERB = {
    "accept_majority": "Accepted the AI's verdict",
    "mark_pass": "Marked Pass",
    "mark_fail": "Marked Fail",
    # V-069: fallback only — `_criterion_flowable` prefers "Marked
    # {level name}" whenever `row.level` is present, which it always is
    # for a real `mark_level` resolution.
    "mark_level": "Marked a level",
}
_CHECK_KIND_ORDER = {
    "internal_agreement": 0,
    "citation_integrity": 1,
    "statistical_forensics": 2,
    "originality_reuse": 3,
}
_CHECK_KIND_LABEL = {
    "internal_agreement": "Internal agreement",
    "citation_integrity": "Citation integrity",
    "statistical_forensics": "Statistical forensics",
    "originality_reuse": "Originality / reuse",
}
# backend review finding (BUG-081/082 review): this used to be a
# hand-copied string tuple, completely separate from the canonical
# enum-based `NEEDS_REVIEW_OUTCOMES` in `app.checks.escalation` --
# exactly the kind of un-tested duplication BUG-082's own ticket names as
# the root cause of "two renderers agree by coincidence." `ResultOutcome`
# is a `StrEnum`, so its members compare equal to the plain strings
# `CriterionResultOut.outcome` actually carries -- this is a real import,
# not a re-declaration that could drift again.
NEEDS_REVIEW_OUTCOMES = tuple(_NEEDS_REVIEW_OUTCOMES_ENUM)

# BUG-049: one source of truth for the disclosure text/tone, used by both
# the badge (near the title) and the footer line on every page -- a
# hand-duplicated string in two places is exactly the kind of drift this
# project's honest-wording discipline exists to prevent. `real` needs no
# disclosure at all; `unknown` (a run that predates migration 0024's
# `llm_mode` column) gets its own honest, distinct wording -- not a
# confident "this is fake" claim, and not silence either, which would
# recreate the exact non-disclosure this ticket exists to close for
# every pre-migration run (backend review finding, live-reproduced
# against report/29 -- the ticket's own reproduction case).
_LLM_MODE_DISCLOSURE = {
    "fake": (
        "Test-mode AI responses. This run is recorded in test AI mode. "
        "AI responses use fixtures instead of real AI grading. "
        "Do not treat AI-derived judgments as a real assessment of the manuscript. "
        "Check the recorded evidence directly.",
        "danger",
    ),
    "unknown": (
        "AI mode unknown: this run predates AI-mode tracking, so whether it used real "
        "or simulated AI results can't be confirmed.",
        "caution",
    ),
}


def _styles(font: str) -> dict[str, ParagraphStyle]:
    bold = f"{font}-Bold"
    return {
        "title": ParagraphStyle("title", fontName=bold, fontSize=18, leading=22, textColor=_INK),
        "framing": ParagraphStyle(
            "framing", fontName=font, fontSize=8, leading=11, textColor=_INK_TERTIARY
        ),
        "meta": ParagraphStyle(
            "meta", fontName=font, fontSize=10, leading=14, textColor=_INK_SECONDARY
        ),
        "verdict": ParagraphStyle("verdict", fontName=bold, fontSize=16, leading=20),
        "h2": ParagraphStyle(
            "h2",
            fontName=bold,
            fontSize=13,
            leading=16,
            textColor=_INK,
            spaceBefore=14,
            spaceAfter=6,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "h3",
            fontName=bold,
            fontSize=10,
            leading=13,
            textColor=_INK_TERTIARY,
            spaceBefore=8,
            spaceAfter=3,
            keepWithNext=True,
        ),
        "body": ParagraphStyle("body", fontName=font, fontSize=10, leading=14, textColor=_INK),
        "body_bold": ParagraphStyle(
            "body_bold", fontName=bold, fontSize=10, leading=14, textColor=_INK
        ),
        "caption": ParagraphStyle(
            "caption", fontName=font, fontSize=8, leading=11, textColor=_INK_TERTIARY
        ),
        "caption_bold": ParagraphStyle(
            "caption_bold", fontName=bold, fontSize=8, leading=11, textColor=_INK_TERTIARY
        ),
        "caption_italic": ParagraphStyle(
            "caption_italic",
            fontName=f"{font}-Italic",
            fontSize=8,
            leading=11,
            textColor=_INK_TERTIARY,
        ),
        "cell": ParagraphStyle("cell", fontName=font, fontSize=9, leading=12, textColor=_INK),
        "cell_bold": ParagraphStyle(
            "cell_bold", fontName=bold, fontSize=9, leading=12, textColor=_INK
        ),
        "cell_caption": ParagraphStyle(
            "cell_caption", fontName=font, fontSize=8, leading=11, textColor=_INK_TERTIARY
        ),
        "draft_badge": ParagraphStyle(
            "draft_badge", fontName=bold, fontSize=9, leading=12, textColor=_DRAFT_GRAY
        ),
    }


# D-023: weight is a relative value (no required total) -- rendered as a
# Low/Medium/High label, never a bare percentage. Short label for the
# narrow "Importance" table column, long label for the pending-escalation
# section's inline prose -- same underlying fact, two column widths,
# same single source (this dict), never re-derived independently (the
# exact divergence `_format_weight`'s own history warns about).
#
# backend review finding (live-reproduced, frontend half): the first
# version of this mapped High/Med/Low onto danger/caution/info, the SAME
# tones a real severity flag uses -- but a heavily-weighted criterion is
# not a risk finding, and this document ALSO prints real severity badges
# (the Flags section above). Reusing danger red for "High importance"
# reintroduced the exact defect D-023 exists to remove, visually instead
# of numerically. Fixed the same way as the frontend's `WeightImportanceTag`:
# a neutral ink-intensity scale (this module's own _INK/_INK_SECONDARY/
# _INK_TERTIARY, already used throughout for typographic hierarchy), a
# magnitude cue never a valence cue, no new tokens.
_WEIGHT_IMPORTANCE_LABEL = {"high": "High", "med": "Medium", "low": "Low"}
_WEIGHT_IMPORTANCE_LABEL_LONG = {
    "high": "High importance",
    "med": "Medium importance",
    "low": "Low importance",
}


def _format_weight_importance(importance: str, *, long: bool = False) -> str:
    """One label rule for a criterion's weight importance, used
    everywhere it's shown in this document -- see this function's own
    prior history as `_format_weight` (UX review P2, live-reproduced) for
    why a single shared formatter matters: the pending-escalation section
    and the Criteria Results table rendering the SAME fact two different
    ways is a real trust problem for a standalone artifact an adviser
    reads cold."""
    table = _WEIGHT_IMPORTANCE_LABEL_LONG if long else _WEIGHT_IMPORTANCE_LABEL
    return table.get(importance, importance)


def _format_date(dt: datetime, *, with_time: bool = False) -> str:
    """`%-d`/`%-I` (no leading zero) are a glibc strftime extension --
    absent on Windows, so this dev environment can't use them even
    though the Linux production target could. Built manually instead of
    depending on a platform-specific flag.

    BUG-059: `with_time` output is explicitly labelled "UTC" (never
    silently converted to an assumed instructor timezone -- this PDF is a
    portable artifact that can be opened by an adviser anywhere, not just
    the instructor who generated it). The caller is responsible for
    passing a UTC instant; this only labels it, since a naive `datetime`
    has no timezone to convert from safely. The bug's own measured
    failure was a UTC-stamped PDF read as if it were local Asia/Manila
    time with no zone marker at all, an 8-hour discrepancy on the record
    whose purpose is to be one."""
    date_part = f"{dt:%b} {dt.day}, {dt:%Y}"
    if not with_time:
        return date_part
    hour12 = dt.hour % 12 or 12
    return f"{date_part}, {hour12}:{dt:%M %p} UTC"


def _explainer_base(report) -> str:
    if report.status == "needs_review":
        return report.reason or "This run needs manual review."
    if report.unresolved_high_flag_count > 0:
        noun = "flag" if report.unresolved_high_flag_count == 1 else "flags"
        return (
            f"Not Ready. {report.unresolved_high_flag_count} unresolved high-severity {noun} "
            "found. Any unresolved high-severity flag forces Not Ready, regardless of score."
        )
    if report.status == "ready":
        return (
            f"Ready. The score is {report.composite_score}% (at or above the "
            f"{report.thresholds['ready_min_score']}% floor) with no unresolved "
            "high-severity flags."
        )
    if report.status == "not_ready":
        return (
            f"Not Ready. The score is {report.composite_score}% "
            f"(below the {report.thresholds['not_ready_max_score']}% floor)."
        )
    return (
        f"Conditionally Ready. The score is {report.composite_score}%, between "
        f"{report.thresholds['not_ready_max_score']}% and {report.thresholds['ready_min_score']}%."
    )


def _explainer(report) -> str:
    base = _explainer_base(report)
    if report.flag_deduction <= 0:
        return base
    points = round(report.flag_deduction * 10) / 10
    # No "See Flags below" link language: there is no clickable anchor in
    # a static PDF, and the Flags section already follows this one.
    return f"{base} This includes a {points}-point deduction from unresolved flags."


def _archive_disclosure(
    archive_size_n: int | None,
    corpus: str | None = None,
    not_compared_reason: str | None = None,
) -> str:
    """V-077 (D-028): names what the count measured. Since D-028 the check
    compares against the Library only; older results counted the former
    automatic archive. Zero is stated as "nothing to compare against", never
    as a clean result."""
    if not_compared_reason == "source_unresolved":
        return (
            "Originality and reuse check did not run because parts of the manuscript "
            "could not be read reliably. No originality conclusion is available."
        )
    if archive_size_n is None:
        return (
            "Originality and reuse check did not run for this manuscript "
            "(no comparable content could be extracted)."
        )
    if corpus == "library":
        if archive_size_n == 0:
            return (
                "Originality and reuse check: no Library records were available to "
                "compare against when this check ran, so no reuse could be found. This is "
                "not evidence of originality."
            )
        noun = "record" if archive_size_n == 1 else "records"
        return (
            f"Originality and reuse check: compared against {archive_size_n} Library "
            f"{noun} when this check ran. Manuscripts not in the Library were not compared."
        )
    if archive_size_n == 0:
        return (
            "Originality and reuse check: compared against 0 previously processed manuscripts. "
            "This may be one of the first checked under this rubric family."
        )
    return (
        f"Originality and reuse check: compared against {archive_size_n} previously "
        "processed manuscripts in VERIDICAL's archive, before the Library became a "
        "separate collection."
    )


def _tone_badge(label: str, tone: str, styles: dict[str, ParagraphStyle]) -> Table:
    bg, fg = _TONE_COLORS.get(tone, _TONE_COLORS["neutral"])
    style = ParagraphStyle("badge", parent=styles["body_bold"], textColor=fg)
    t = Table([[Paragraph(escape(label), style)]], colWidths=None)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return t


def _result_display(row: CriterionResultOut) -> tuple[str, str]:
    # V-069 AC2/AC5: a levelled criterion's judgment is its LEVEL NAME
    # (owner-approved treatment), never Pass/Partial/Fail — the neutral
    # "info" tone matches the on-screen `ResultsTable.tsx` treatment (a
    # level is a rung on a scale, not a pass/fail valence).
    if row.level is not None:
        return row.level.name, "info"
    is_partial = row.outcome == "passed" and row.score is not None and round(row.score) == 50
    if is_partial:
        return "Partial", "caution"
    return {
        "passed": ("Pass", "success"),
        "failed": ("Fail", "danger"),
        "not_applicable": ("Not applicable", "neutral"),
        "unverifiable": ("Unverifiable", "neutral"),
    }.get(row.outcome, (row.outcome, "neutral"))


def _display_anchor(anchor: str) -> str:
    match = re.fullmatch(r"(?:¶|Â¶|paragraph\s+)(\d+)", anchor.strip(), flags=re.I)
    return f"Extracted paragraph {int(match[1]) + 1}" if match else anchor


def _source_caption(row: CriterionResultOut) -> str:
    """BUG-082: this used to read `row.type` (what the rubric DECLARES the
    criterion to be), while the frontend's identical caption
    (`ResultsTable.tsx`'s `sourceCaption`) reads `row.kind` (how the
    criterion was ACTUALLY checked). They agreed only by coincidence --
    the entire point of the Tier-0/1 cascade is a `semantic` criterion
    decided by a deterministic signal, which is exactly when they diverge.
    Now reads `kind`, same as the frontend, so a criterion that WAS
    decided by a rule says "Rule-checked" here even when the rubric calls
    it semantic."""
    if row.resolution:
        return "Resolved by instructor"
    # BUG-092/UX review finding (2026-08-24): same fix as the frontend's
    # `sourceCaption` (`ResultsTable.tsx`) -- a `not_assessable` criterion
    # is deliberately never AI-graded, but its `kind` is a
    # `CheckKind.semantic` audit-log placeholder (`router.py`), not a real
    # signal that grading happened. Checked ahead of `kind` so the PDF
    # export doesn't claim "AI-graded" for a criterion the router never
    # sent to AI grading at all.
    if row.type == "not_assessable":
        return "Not checked"
    return "Rule-checked" if row.kind == "structural" else "AI-graded"


def _pending_review_reason(outcome: str) -> str:
    if outcome == "escalated":
        return "AI reached no verdict for this criterion."
    return (
        "This criterion was not graded by AI (capacity spent or the grading service was "
        "unavailable)."
    )


def _criterion_flowable(row: CriterionResultOut, styles: dict[str, ParagraphStyle]) -> list:
    parts = [Paragraph(escape(row.text), styles["cell_bold"])]
    caption_bits = [_source_caption(row)]
    # V-069 AC5: the decided rung's own points, alongside the level name
    # already shown as the row's Result column — this is the "score" half
    # of AC2's "shows a per-criterion level and score."
    if row.level is not None:
        caption_bits.append(f"{row.level.points:g}/{row.level.max_points:g} pts")
    if row.resolution:
        verb = (
            f"Marked {row.level.name}"
            if row.resolution.type == "mark_level" and row.level is not None
            else RESOLUTION_VERB.get(row.resolution.type, "Resolved by instructor")
        )
        parts.append(
            Paragraph(f"<b>{escape(verb)}.</b> {escape(row.resolution.reason)}", styles["cell"])
        )
        if row.resolution.ai_majority_verdict:
            parts.append(
                Paragraph(
                    "The AI's own vote leaned "
                    f"{escape(row.resolution.ai_majority_verdict)}, but did not reach the "
                    "agreement threshold needed to auto-decide.",
                    styles["cell_caption"],
                )
            )
    elif row.evidence:
        parts.append(Paragraph(escape(row.evidence[0].quote), styles["cell"]))
        parts.append(
            Paragraph(
                f"Anchor: {escape(_display_anchor(row.evidence[0].anchor))}", styles["cell_caption"]
            )
        )
        if row.reasoning or row.reason:
            parts.append(Paragraph(escape(row.reasoning or row.reason or ""), styles["cell"]))
    elif row.reasoning or row.reason:
        parts.append(Paragraph(escape(row.reasoning or row.reason or ""), styles["cell"]))
    parts.append(Paragraph(" · ".join(escape(c) for c in caption_bits), styles["cell_caption"]))
    return parts


def _page_evidence_flowables(
    image: PageEvidenceImageOut | None,
    content_width: float,
    styles: dict[str, ParagraphStyle],
    dpi: int,
) -> list:
    """V-070: the flag's own rendered submitted page, laid out under its
    excerpt. `image` is None only when this flag was never assessed by
    `app.report.page_images` at all (should not happen for a real export,
    since every flag gets an entry -- defensive, not expected); a real
    entry ALWAYS has either `image_png` or `reason` set (AC3: never a
    silent omission for a purged source, a DOCX source, or an
    unrecoverable anchor)."""
    if image is None:
        return []
    if image.image_png is None:
        # design review finding, 2026-08-25: "zero evidence shown for this
        # flag" is a materially different, weaker state than a routine
        # page-number caption, but rendered in the exact same
        # size/color/weight as one -- easy to skim past. Bumped to the
        # document's own existing "needs attention" tone (the same
        # caution color the rubric-needs-review notice above uses), bold,
        # not a new component.
        no_image_style = ParagraphStyle(
            "no_image_reason",
            parent=styles["caption_bold"],
            textColor=_TONE_COLORS["caution"][1],
        )
        return [Spacer(1, 2), Paragraph(escape(image.reason or ""), no_image_style)]

    # `ImageReader` is used ONLY to read the pixel dimensions -- reportlab's
    # `Image` flowable, given an `ImageReader` directly, checks for a
    # `.read` attribute to decide whether its argument is file-like and,
    # finding none, falls through to `os.path.splitext(filename)` on the
    # `ImageReader` OBJECT ITSELF and crashes (`TypeError: expected str,
    # bytes or os.PathLike object, not ImageReader` -- reproduced live,
    # reportlab 5.0.0). A plain `BytesIO` has `.read` and takes the
    # file-like branch instead, so `Image` below gets its own fresh one.
    native_w_px, native_h_px = ImageReader(BytesIO(image.image_png)).getSize()
    # The raster was rendered at `dpi`; converting back to points (72/inch)
    # gives the image's real physical size, which is then scaled DOWN only
    # (never up past its native resolution) to fit the content column and
    # the configured max height.
    native_w = native_w_px * 72 / dpi
    native_h = native_h_px * 72 / dpi
    scale = min(
        content_width / native_w, get_settings().export_page_image_max_height / native_h, 1.0
    )

    caption = (
        f"Rendered from the submitted PDF, page {image.page}"
        if image.page
        else "Rendered from the submitted PDF"
    )
    if image.reason:
        caption = f"{caption} · {image.reason}"

    return [
        Spacer(1, 4),
        Image(BytesIO(image.image_png), width=native_w * scale, height=native_h * scale),
        Spacer(1, 2),
        Paragraph(escape(caption), styles["caption"]),
    ]


def build_report_pdf(data: ReportExportData) -> bytes:
    font = _register_fonts()
    styles = _styles(font)
    report = data.report
    is_draft = report.decision is None
    export_dpi = get_settings().export_page_image_dpi
    generated_at = _format_date(datetime.now(UTC), with_time=True)
    footer_llm_disclosure = _LLM_MODE_DISCLOSURE.get(report.llm_mode)
    side_margin = _PRINT_LAYOUT["side_margin"]
    content_width = A4[0] - 2 * side_margin
    footer_style = ParagraphStyle("footer", parent=styles["caption"], alignment=1)
    footer_mode_style = ParagraphStyle(
        "footer_mode",
        parent=footer_style,
        fontName=f"{font}-Bold",
        textColor=(
            _TONE_COLORS[footer_llm_disclosure[1]][1] if footer_llm_disclosure else _INK_TERTIARY
        ),
    )
    footer_rows = [
        Paragraph(
            escape(footer_llm_disclosure[0]) if footer_llm_disclosure else "",
            footer_mode_style,
        ),
        Paragraph(
            "This report reflects the instructor's own review and decisions. "
            "It is not an automated grade.",
            footer_style,
        ),
        Paragraph(
            "DRAFT - NO DECISION RECORDED" if is_draft else "",
            ParagraphStyle("footer_draft", parent=footer_mode_style, textColor=_DRAFT_GRAY),
        ),
    ]
    footer_height = sum(
        max(row.wrap(content_width, A4[1])[1], footer_style.leading)
        + _PRINT_LAYOUT["footer_row_gap"]
        for row in footer_rows
    )
    footer_timestamp = Paragraph(f"Generated {generated_at}", styles["caption"])
    timestamp_height = footer_timestamp.wrap(content_width / 2, A4[1])[1]
    timestamp_y = _PRINT_LAYOUT["footer_bottom"] + footer_height
    bottom_margin = max(
        _PRINT_LAYOUT["bottom_margin"],
        timestamp_y + timestamp_height + _PRINT_LAYOUT["footer_body_gap"],
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=98,
        bottomMargin=bottom_margin,
        leftMargin=side_margin,
        rightMargin=side_margin,
        title=f"VERIDICAL Readiness Report - {report.manuscript_group_label}",
        author="VERIDICAL",
        subject="Capstone manuscript readiness report",
    )
    content_width = doc.width

    flow: list = []

    # 1. Title block.
    flow.append(Paragraph("VERIDICAL Readiness Report", styles["title"]))
    identity = report.manuscript_group_label
    if report.manuscript_original_filename:
        identity = f"{identity} · {report.manuscript_original_filename}"
    flow.append(Paragraph(escape(identity), styles["meta"]))
    flow.append(Paragraph(escape(report.rubric_title), styles["meta"]))
    flow.append(
        Paragraph(
            f"Generated {generated_at} · Reference: check run #{report.check_run_id}",
            styles["caption"],
        )
    )
    flow.append(Spacer(1, 4))
    flow.append(
        Paragraph(
            "A student capstone project at T.I.P. Manila, not an official T.I.P. system.",
            styles["framing"],
        )
    )
    llm_mode_disclosure = _LLM_MODE_DISCLOSURE.get(report.llm_mode)
    if llm_mode_disclosure is not None:
        disclosure_text, disclosure_tone = llm_mode_disclosure
        disclosure_color = _TONE_COLORS[disclosure_tone][1]
        flow.append(Spacer(1, 6))
        test_mode_tag = Table(
            [
                [
                    Paragraph(
                        disclosure_text,
                        ParagraphStyle(
                            "test_mode_badge",
                            parent=styles["draft_badge"],
                            textColor=disclosure_color,
                        ),
                    )
                ]
            ]
        )
        test_mode_tag.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.75, disclosure_color),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        flow.append(test_mode_tag)
    if report.rubric_needs_review:
        flow.append(Spacer(1, 4))
        flow.append(
            Paragraph(
                escape(
                    "This rubric was activated while the parser's own coverage check "
                    "still flagged it as needing manual completion. Its criteria may not "
                    "fully reflect the required format's source text."
                ),
                ParagraphStyle(
                    "rubric_needs_review",
                    parent=styles["caption"],
                    textColor=_TONE_COLORS["caution"][1],
                ),
            )
        )
    flow.append(Spacer(1, 12))

    # 2. Verdict block.
    flow.append(
        _tone_badge(
            READINESS_LABEL.get(report.status, report.status),
            READINESS_TONE.get(report.status, "neutral"),
            styles,
        )
    )
    if is_draft:
        flow.append(Spacer(1, 6))
        draft_tag = Table([[Paragraph("DRAFT - NO DECISION RECORDED", styles["draft_badge"])]])
        draft_tag.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.75, _DRAFT_GRAY),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        flow.append(draft_tag)
    flow.append(Spacer(1, 6))
    # BUG-133 (ground rule 8): this used to render in `styles["score"]"
    # (20pt bold) -- twice the size of the readiness-band badge above it
    # (`body_bold`, 10pt) and completely unlabeled, so the composite
    # percentage visually dominated the band it's supposed to be
    # subordinate to. Ground rule 8's own text: "the verdict the
    # instructor sees is the band... The number still exists -- it just
    # is not what the instructor reads... still appears in... the
    # exported detail." Restyled to the same small, explicitly-attributed
    # treatment already established for `levelled_rating` a few lines
    # below (owner-approved 2026-08-25) -- reproducible, present, never
    # the headline.
    if report.composite_score is not None:
        flow.append(
            Paragraph(
                f"Composite score: {report.composite_score}% (reproducible detail, "
                "not the verdict; see the readiness band above).",
                styles["caption"],
            )
        )
    flow.append(
        Paragraph(
            f"Ready at {report.thresholds['ready_min_score']}% or above. "
            f"Not Ready at {report.thresholds['not_ready_max_score']}% or below.",
            styles["meta"],
        )
    )
    flow.append(Spacer(1, 4))
    flow.append(Paragraph(escape(_explainer(report)), styles["body"]))
    # V-069 AC2/AC5: the rubric's own institutional RATING, transcribed --
    # owner-approved treatment (2026-08-25): shown as a small, clearly-
    # attributed line (ground rule 8) -- the same treatment BUG-133 later
    # extended to the composite score above, once both existed side by
    # side and made the contrast concrete. Absent whenever no criterion in
    # this rubric is levelled (AC3).
    if report.levelled_rating is not None and report.levelled_rating.n_decided > 0:
        lr = report.levelled_rating
        flow.append(Spacer(1, 4))
        flow.append(
            Paragraph(
                escape(
                    "This rubric's own RATING, transcribed from its formula (not a VERIDICAL "
                    f"judgment): {lr.achieved_points:g}/{lr.max_points:g} points = "
                    f"{lr.rating_percent}% ({lr.n_decided} of {lr.n_levelled} levelled criteria "
                    "decided)."
                ),
                styles["caption"],
            )
        )
    flow.append(Spacer(1, 10))

    # 3. Pending escalations. BUG-081: this used to be gated on `is_draft`,
    # so a criterion still escalated at decide time (blocked in the normal
    # flow by `decide_report`'s own gate, `service.py`, but not something
    # this rendering function should assume will always hold -- data can
    # predate that gate or reach here by a path this function doesn't
    # control) fell out of BOTH this section and the Criteria Results
    # table below (`shown_ids` excludes pending rows unconditionally) --
    # disappearing from the decided PDF entirely while `EscalatedPanel.tsx`
    # still showed it on screen. Render it either way; only the wording
    # changes with `is_draft`.
    pending = [r for r in report.results if r.outcome in NEEDS_REVIEW_OUTCOMES and not r.resolution]
    if pending:
        flow.append(
            Paragraph(
                "Needs Your Review" if is_draft else "Unresolved At Time Of Decision", styles["h2"]
            )
        )
        for row in pending:
            block = [
                Paragraph(
                    f"{escape(row.text)} "
                    f"<i>({_format_weight_importance(row.weight_importance, long=True)})</i>",
                    styles["body_bold"],
                ),
                Paragraph(
                    escape(
                        _pending_review_reason(row.outcome)
                        + (" " + row.reason if row.reason else "")
                    ),
                    styles["body"],
                ),
                Paragraph(
                    "Not yet resolved. This is why this export is marked DRAFT."
                    if is_draft
                    else "Not yet resolved when the instructor recorded this decision.",
                    styles["body_bold"],
                ),
            ]
            flow.append(KeepTogether(block))
            flow.append(Spacer(1, 6))
        flow.append(Spacer(1, 6))

    # 4. Flags.
    if report.integrity_check_status or report.historical_citation_title_unverified:
        flow.append(Paragraph("Assessment limits", styles["h2"]))
        if report.historical_citation_title_unverified:
            flow.append(
                Paragraph(
                    escape(_coverage_labels()["historical_citation_title_unverified"]),
                    styles["body"],
                )
            )
        for coverage in report.integrity_check_status:
            label = _CHECK_KIND_LABEL.get(coverage.check_kind, coverage.check_kind)
            reason = _coverage_labels().get(
                coverage.coverage_reason,
                (coverage.coverage_reason or coverage.outcome).replace("_", " "),
            )
            if coverage.assessment_scope == "reference_metadata_only":
                reason = (
                    "Partially assessed. Reference details were checked where possible. "
                    "Citation matching and claim support across the manuscript were not assessed"
                    if coverage.analysis_coverage == "partial"
                    else "Not assessed. Reference entries were retained, but their details "
                    "could not be checked. Citation matching and claim support across the "
                    "manuscript were not assessed"
                )
            elif coverage.assessment_scope == "available_text_and_tables":
                reason = (
                    "Partially assessed. Statistical statements were extracted from readable "
                    "text or tables. Some image content remains unassessed. "
                    "This limit is not a statistical error"
                    if coverage.analysis_coverage == "partial"
                    else "Not assessed. No supported statistical statements were found in "
                    "readable text and tables. Unassessed images may contain information "
                    "this check missed"
                )
            elif coverage.analysis_coverage == "unsupported":
                reason = (
                    "Not assessed. VERIDICAL could not establish a reliable source for this check. "
                    "No conclusion was made"
                )
            counts = []
            if coverage.assessment_scope == "reference_metadata_only":
                counts.append(
                    f"{coverage.n_reference_metadata_attempted} reference checks attempted"
                )
            if coverage.n_metadata_api_down:
                counts.append(
                    f"{coverage.n_metadata_api_down} source checks could not reach "
                    "an external service"
                )
            if coverage.n_source_pages_unassessed:
                counts.append(f"{coverage.n_source_pages_unassessed} source pages unassessed")
            if coverage.n_metadata_skipped_insufficient:
                counts.append(f"{coverage.n_metadata_skipped_insufficient} references unverified")
            if coverage.n_claim_support_unassessed:
                counts.append(
                    f"{coverage.n_claim_support_unassessed} sources with unassessed claim support"
                )
            if coverage.n_skipped_quota:
                counts.append(
                    f"{coverage.n_skipped_quota} items skipped because AI capacity was unavailable"
                )
            if coverage.n_skipped_api_down:
                counts.append(
                    f"{coverage.n_skipped_api_down} items skipped because a service was unavailable"
                )
            if coverage.n_skipped_parse_failure:
                counts.append(
                    f"{coverage.n_skipped_parse_failure} items skipped because the model "
                    "response could not be validated"
                )
            if coverage.n_linkage_unassessed:
                counts.append(f"{coverage.n_linkage_unassessed} reference links remain unassessed")
            if coverage.n_images_unassessed:
                noun = "occurrence" if coverage.n_images_unassessed == 1 else "occurrences"
                counts.append(f"{coverage.n_images_unassessed} image {noun} unassessed")
            if coverage.n_image_occurrences_omitted:
                counts.append(
                    f"{coverage.n_image_occurrences_omitted} additional drawing occurrences "
                    "exceeded the inventory limit"
                )
            if coverage.n_equations_unassessed:
                noun = "equation" if coverage.n_equations_unassessed == 1 else "equations"
                counts.append(
                    f"{coverage.n_equations_unassessed} {noun} transcribed but not evaluated"
                )
            if coverage.n_recovery_figures_unassessed or coverage.n_recovery_tables_unassessed:
                counts.append(
                    f"{coverage.n_recovery_figures_unassessed} figure and "
                    f"{coverage.n_recovery_tables_unassessed} table recovery proposals unassessed"
                )
            text = f"{label}: {reason}. " + "; ".join(counts)
            flow.append(Paragraph(escape(text), styles["body"]))
            image_locations = {}
            for item in coverage.image_coverage_items:
                anchor = (
                    f"Page {item.page}"
                    if item.page is not None
                    else f"Extracted paragraph {item.paragraph + 1}"
                    if item.paragraph is not None
                    else "Source location unavailable"
                )
                key = anchor, item.reason
                image_locations[key] = image_locations.get(key, 0) + 1
            for (anchor, image_reason), count in image_locations.items():
                label = _image_coverage_labels().get(image_reason, "Image content unassessed")
                occurrences = "image occurrence" if count == 1 else "image occurrences"
                flow.append(
                    Paragraph(
                        escape(f"{anchor}: {label} ({count} {occurrences})."),
                        styles["caption"],
                    )
                )
        flow.append(
            Paragraph(
                "These are coverage limitations, not findings against the manuscript. "
                "No flag does not mean that every check completed.",
                styles["body"],
            )
        )
    if data.unassessed_references:
        flow.append(Paragraph("Reference entries with assessment limits", styles["h3"]))
        flow.append(
            Paragraph(
                "Entry numbers below are positions in this run's saved reference list. "
                "These are coverage notices, not findings against the manuscript.",
                styles["caption"],
            )
        )
        for entry in data.unassessed_references:
            if entry.status == "api_down":
                reason = "The external service was unavailable; verification did not complete."
            elif entry.reason == "ambiguous_identifier_wrap":
                reason = "A split source identifier could not be reconstructed reliably."
            elif entry.reason == "unsupported_reference_structure":
                reason = "The reference could not be parsed reliably enough for verification."
            elif entry.reason in _coverage_labels():
                reason = _coverage_labels()[entry.reason]
            else:
                reason = (
                    "This reference was not fully assessed. "
                    f"Recorded lookup status: {entry.status.replace('_', ' ')}."
                )
            attempted = (
                "A lookup was attempted." if entry.lookup_attempted else "No lookup was attempted."
            )
            flow.append(
                Paragraph(
                    escape(
                        f"Reference entry {entry.reference_order_index + 1}: {reason} {attempted}"
                    ),
                    styles["body"],
                )
            )
    if data.report.figure_readings is not None:
        readings = data.report.figure_readings
        available = [item for item in readings if item.status == "available"]
        flow.append(Paragraph("Saved visual readings", styles["h2"]))
        flow.append(
            Paragraph(
                escape(
                    f"Visual readings are available for {len(available)} of {len(readings)} "
                    f"image occurrences. {len(readings) - len(available)} remain unassessed. "
                    "Visual readings do not establish that statistical values in images "
                    "were checked or that rubric criteria were met."
                ),
                styles["body"],
            )
        )
        for item in readings:
            location = item.source_binding.get("locator", {})
            if not isinstance(location, dict):
                location = {}
            anchor = (
                f"Extracted paragraph {location['paragraph'] + 1}"
                if type(location.get("paragraph")) is int
                else f"Page {location['page']}"
                if type(location.get("page")) is int
                else "Source location unavailable"
            )
            flow.append(
                Paragraph(
                    escape(
                        f"Source image {item.image_index + 1} - {anchor}: "
                        f"{item.status}. {item.reason}"
                    ),
                    styles["body"],
                )
            )
            if item.status == "available":
                for text in [
                    item.visible_content,
                    "Visible labels: " + "; ".join(item.visible_labels),
                    "Limits: " + "; ".join(item.limitations),
                    item.support_reason,
                ]:
                    if text:
                        flow.append(Paragraph(escape(text), styles["body"]))
                if item.source_caption:
                    flow.append(
                        Paragraph(
                            escape("Nearby source caption: " + item.source_caption),
                            styles["caption"],
                        )
                    )
    if data.report.historical_semantic_support_unverified:
        flow.append(Paragraph("Historical AI grades need fresh evidence review", styles["h2"]))
        flow.append(
            Paragraph(
                "Some recorded AI suggestions predate criterion-support validation. They are not "
                "validated by the current safeguards. Run a new check or assess the manuscript "
                "independently. Reopening preserves the original record.",
                styles["body"],
            )
        )
    flag_intro = [Paragraph(f"Flags ({len(data.flags)})", styles["h2"])]
    docx_source = data.source_format == "docx" or any(
        image.reason_code == "docx_layout" for image in data.page_images.values()
    )
    if docx_source:
        flag_intro.append(
            Paragraph(
                "This manuscript was submitted as a DOCX. Evidence below uses saved text "
                "and source locations; original Word pages are not reproduced.",
                styles["caption"],
            )
        )
    flag_intro.append(
        Paragraph(
            "Possible inconsistencies found by VERIDICAL's integrity checks (F4 to F7), not "
            "accusations.",
            styles["caption_italic"],
        )
    )
    flag_intro.append(Spacer(1, 6))
    if not data.flags:
        flow.extend(flag_intro)
        flow.append(
            Paragraph(
                "No integrity flags were recorded. Read the check coverage and originality "
                "scope before interpreting this as an absence of issues.",
                styles["body"],
            )
        )
    else:
        finding_numbers = {flag.id: index for index, flag in enumerate(data.flags, start=1)}
        by_kind: dict[str, list[FlagSummaryOut]] = {}
        for f in data.flags:
            by_kind.setdefault(f.check_kind, []).append(f)
        for kind in sorted(by_kind, key=lambda k: _CHECK_KIND_ORDER.get(k, len(_CHECK_KIND_ORDER))):
            group = by_kind[kind]
            category_heading = Paragraph(
                f"{_CHECK_KIND_LABEL.get(kind, kind).upper()} ({len(group)})", styles["h3"]
            )
            for group_index, f in enumerate(group):
                detail = data.flag_details.get(f.id, FlagExportDetailOut())
                finding_number = finding_numbers[f.id]
                issue = _issue_title(detail.issue or f.problem_kind)
                severity_label = {
                    "high": "High severity",
                    "med": "Medium severity",
                    "low": "Low severity",
                }.get(f.severity, f.severity)
                # Bold weight for High only -- a grayscale-safe signal that
                # survives a printed/photocopied page where the tone color
                # alone would not (extends the on-screen "never color
                # alone" rule to a channel print actually needs).
                caption_style = (
                    styles["caption_bold"] if f.severity == "high" else styles["caption"]
                )
                caption_text = (
                    f"Anchor: {escape(_display_anchor(f.page_anchor))} · {escape(severity_label)}"
                )
                if f.overridden:
                    caption_text += " · Overridden"
                if not f.affects_readiness:
                    caption_text += " · Comparison lead: not scored"
                header = Paragraph(caption_text, caption_style)
                excerpt = Paragraph(escape(f.evidence_excerpt), styles["body"])
                # design review finding, 2026-08-25 (measured against a
                # real 34-flag export: EVERY flag's own rendered page image
                # trails onto the following page, so a page opening
                # directly into "Anchor: p. N · ..." metadata -- the same
                # weight/color as the CLOSING caption of the flag before it
                # -- is this document's default reading experience, not an
                # edge case). A hairline rule reused from the Criteria
                # Results table's own existing convention (`_RULE`) marks
                # "a new flag starts here" regardless of what's above it on
                # the page, glued to the header it introduces so it can
                # never appear detached from it.
                divider = HRFlowable(
                    width=content_width, thickness=0.5, color=_RULE, spaceBefore=6, spaceAfter=6
                )
                heading = Paragraph(
                    escape(f"Finding {finding_number}: {issue}"), styles["body_bold"]
                )
                evidence_label = Paragraph(
                    "System comparison summary"
                    if detail.evidence_is_summary
                    else "Manuscript evidence",
                    styles["caption_bold"],
                )
                prefix = list(flag_intro) + [category_heading] if group_index == 0 else []
                flag_intro = []
                start_items = [*prefix, divider, heading, header, evidence_label]
                for item in start_items:
                    item.keepWithNext = True
                start_height = 0
                previous_space = 0
                for item in start_items:
                    start_height += max(previous_space, item.getSpaceBefore())
                    start_height += item.wrap(content_width, A4[1])[1]
                    previous_space = item.getSpaceAfter()
                start_height += previous_space + min(
                    excerpt.wrap(content_width, A4[1])[1],
                    styles["body"].leading * _PRINT_LAYOUT["minimum_evidence_lines"],
                )
                flow.append(CondPageBreak(start_height))
                finding = [*start_items, excerpt]
                if detail.citation_metadata_review_warning:
                    finding.extend(
                        [
                            Paragraph(
                                escape(
                                    _coverage_labels()["historical_citation_title_warning_title"]
                                ),
                                styles["body_bold"],
                            ),
                            Paragraph(
                                escape(_coverage_labels()[detail.citation_metadata_review_warning]),
                                styles["body"],
                            ),
                        ]
                    )
                if detail.source_identifier:
                    finding.append(
                        Paragraph(
                            f"<b>Source identifier:</b> {escape(detail.source_identifier)}",
                            styles["body"],
                        )
                    )
                for comparison in detail.comparisons:
                    finding.append(
                        Paragraph(
                            f"<b>{escape(comparison.label)}:</b> {escape(comparison.text)}",
                            styles["body"],
                        )
                    )
                    if comparison.anchor:
                        finding.append(
                            Paragraph(
                                f"Source location: {escape(_display_anchor(comparison.anchor))}",
                                styles["caption"],
                            )
                        )
                finding.append(
                    Paragraph(
                        (
                            "<b>Original recorded explanation:</b> "
                            if detail.citation_metadata_review_warning
                            else "<b>Recorded reason:</b> "
                        )
                        + escape(
                            detail.reasoning
                            or (
                                "The legacy explanation is not reproduced because current "
                                "comparison policy removes outdated or identifying wording."
                                if detail.recorded_explanation_withheld
                                else None
                            )
                            or "No separate explanation was saved for this finding."
                        ),
                        styles["body"],
                    )
                )
                if detail.current_guidance:
                    finding.append(
                        Paragraph(
                            "<b>Current comparison guidance:</b> "
                            + escape(detail.current_guidance),
                            styles["body"],
                        )
                    )
                if detail.evidence_unavailable:
                    finding.append(
                        Paragraph(
                            "Supporting comparison evidence was not available for this finding.",
                            styles["body"],
                        )
                    )
                if f.overridden and detail.override_reason:
                    finding.append(
                        Paragraph(
                            "<b>Recorded instructor resolution:</b> "
                            + escape(detail.override_reason),
                            styles["body"],
                        )
                    )
                page_image = data.page_images.get(f.id)
                if page_image is None or page_image.reason_code != "docx_layout":
                    finding.extend(
                        _page_evidence_flowables(page_image, content_width, styles, export_dpi)
                    )
                # Split long evidence/reasoning instead of hiding or truncating
                # it; every continuation retains the finding and source identity.
                flow.append(
                    PTOContainer(
                        finding,
                        header=[
                            Paragraph(
                                escape(
                                    f"Finding {finding_number} (continued) - "
                                    f"Source location: {_display_anchor(f.page_anchor)}"
                                ),
                                styles["caption_bold"],
                            )
                        ],
                    )
                )
                flow.append(Spacer(1, 8))
    flow.append(Spacer(1, 4))
    scope = data.report.originality_scope
    flow.append(
        Paragraph(
            _archive_disclosure(
                data.archive_size_n,
                scope.corpus if scope else None,
                scope.not_compared_reason if scope else None,
            ),
            styles["caption"],
        )
    )
    flow.append(Spacer(1, 10))

    # 5. Escalation resolutions.
    resolved = [r for r in report.results if r.resolution]
    if resolved:
        flow.append(Paragraph("Escalation Resolutions", styles["h2"]))
        for row in resolved:
            for part in _criterion_flowable(row, styles):
                flow.append(part)
            flow.append(Spacer(1, 8))

    # 6. Criteria results table (excludes pending/resolved rows, already shown above).
    shown_ids = {r.criterion_id for r in pending} | {r.criterion_id for r in resolved}
    remaining = [r for r in report.results if r.criterion_id not in shown_ids]
    flow.append(Paragraph("Criteria Results", styles["h2"]))
    if remaining:
        header = [
            Paragraph("Importance", styles["cell_bold"]),
            Paragraph("Criterion", styles["cell_bold"]),
            Paragraph("Result", styles["cell_bold"]),
        ]
        rows = [header]
        for row in remaining:
            label, tone = _result_display(row)
            result_style = styles["cell_bold"] if tone in ("danger",) else styles["cell"]
            importance_ink = {"high": _INK, "med": _INK_SECONDARY, "low": _INK_TERTIARY}.get(
                row.weight_importance, _INK_SECONDARY
            )
            importance_style = ParagraphStyle(
                "importance_cell",
                parent=styles["cell_bold"] if row.weight_importance == "high" else styles["cell"],
                textColor=importance_ink,
            )
            rows.append(
                [
                    Paragraph(_format_weight_importance(row.weight_importance), importance_style),
                    _criterion_flowable(row, styles),
                    Paragraph(escape(label), result_style),
                ]
            )
        table = Table(rows, colWidths=[62, content_width - 62 - 85, 85], repeatRows=1)
        table_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f2f5")),
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, _RULE),
            ("LINEBELOW", (0, 1), (-1, -1), 0.5, _RULE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]
        for i in range(1, len(rows)):
            if i % 2 == 0:
                table_style.append(("BACKGROUND", (0, i), (-1, i), _ZEBRA))
        table.setStyle(TableStyle(table_style))
        flow.append(table)
    else:
        flow.append(Paragraph("No additional criteria to show.", styles["body"]))
    flow.append(Spacer(1, 12))

    # 7. Final decision.
    flow.append(Paragraph("Final Decision", styles["h2"]))
    if report.decision:
        flow.append(
            _tone_badge(
                DECISION_LABEL.get(report.decision, report.decision),
                DECISION_TONE.get(report.decision, "neutral"),
                styles,
            )
        )
        flow.append(Spacer(1, 6))
        decided_at = _format_date(report.decided_at) if report.decided_at else "an unrecorded date"
        flow.append(Paragraph(f"Decided by the instructor on {decided_at}.", styles["body"]))
        if report.decision_note:
            flow.append(Spacer(1, 4))
            note_table = Table([[Paragraph(escape(report.decision_note), styles["body"])]])
            note_table.setStyle(
                TableStyle(
                    [
                        ("BOX", (0, 0), (-1, -1), 0.5, _RULE),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            flow.append(note_table)
        # BUG-081: this used to be silent for a decided report -- the
        # undecided branch below always said so, the decided branch never
        # did, even though "Unresolved At Time Of Decision" above can be
        # non-empty for a decided report too.
        if pending:
            flow.append(Spacer(1, 4))
            flow.append(
                Paragraph(
                    f"{len(pending)} criteri{'on' if len(pending) == 1 else 'a'} "
                    f"{'was' if len(pending) == 1 else 'were'} still unresolved when this "
                    "decision was recorded. See Unresolved At Time Of Decision above.",
                    styles["body"],
                )
            )
    else:
        flow.append(Paragraph("This manuscript has no final decision yet.", styles["body"]))
        if pending:
            flow.append(
                Paragraph(
                    f"{len(pending)} criteri{'on' if len(pending) == 1 else 'a'} still needed the "
                    "instructor's review at the time this document was generated.",
                    styles["body"],
                )
            )

    def _on_page(canvas: Canvas, doc_: SimpleDocTemplate) -> None:
        canvas.saveState()
        page_num = canvas.getPageNumber()

        if page_num > 1:
            canvas.setFont(font, 8)
            canvas.setFillColor(_INK_TERTIARY)
            running_title = "VERIDICAL - Readiness Report"
            canvas.drawString(side_margin, A4[1] - 40, running_title)
            identity_width = (
                content_width
                - pdfmetrics.stringWidth(running_title, font, 8)
                - _PRINT_LAYOUT["header_identity_gap"]
            )
            truncated = _fit_running_identity(
                report.manuscript_group_label, font, 8, identity_width
            )
            canvas.drawRightString(A4[0] - side_margin, A4[1] - 40, truncated)
            canvas.setStrokeColor(_RULE)
            canvas.setLineWidth(0.5)
            canvas.line(54, A4[1] - 48, A4[0] - 54, A4[1] - 48)

        footer_y = _PRINT_LAYOUT["footer_bottom"]
        for row in footer_rows:
            _, height = row.wrap(content_width, A4[1])
            row.drawOn(canvas, side_margin, footer_y)
            footer_y += max(height, footer_style.leading) + _PRINT_LAYOUT["footer_row_gap"]
        footer_timestamp.drawOn(canvas, side_margin, timestamp_y)
        page_counter = Paragraph(
            f"Page {page_num}",
            ParagraphStyle("footer_page", parent=styles["caption"], alignment=2),
        )
        page_counter.wrap(content_width / 2, A4[1])
        page_counter.drawOn(canvas, side_margin + content_width / 2, timestamp_y)

        if is_draft:
            canvas.saveState()
            canvas.setFont(f"{font}-Bold", 72)
            canvas.setFillColor(_DRAFT_GRAY)
            with contextlib.suppress(AttributeError):
                canvas.setFillAlpha(0.10)
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(45)
            canvas.drawCentredString(0, 0, "DRAFT")
            canvas.restoreState()

        canvas.restoreState()

    doc.build(flow, onFirstPage=_on_page, onLaterPages=_on_page)
    return buf.getvalue()
