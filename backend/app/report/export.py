"""Report PDF export (F8.6, V-039, export action on screen 4h).

reportlab, not WeasyPrint/headless-chromium -- see
tickets/V6-defense-day/V-039.md's own Pre-implementation research for
the memory-footprint reasoning (Render's 512MB free-tier ceiling).

Layout/typography per `ui-designer`'s spec (2026-08-15): A4, Public Sans
(vendored TTF converted from the frontend's own @fontsource woff2 files --
reportlab needs TTF/OTF, not woff), a print-calibrated type scale, direct
reuse of tokens.css's own AA-checked hex colors, and a triple-redundant
DRAFT marking (watermark + inline badge + footer line) whenever
`decision is None` -- the single, server-enforced invariant
`DecisionPanel.tsx`'s own blocked-state gate already relies on.

Every manuscript/user-derived string is XML-escaped before reaching a
reportlab `Paragraph` (its mini-markup parser chokes on a literal `&`/
`<`/`>`, which real citation excerpts contain) -- found live by
`ui-designer` against real seeded data, not a hypothetical.

backend-critic note: the embedded Public Sans TTF renders the middot
separator (`·`, U+00B7) correctly -- visually confirmed by rendering
real exports to PNG. PyMuPDF's `get_text()` extracts that SAME glyph as
U+FFFD, a cmap-lookup quirk of this specific subset font, not a real
corruption. Visual rendering is the authority for this font; a bare `�`
in a `get_text()`-based test assertion is not automatically evidence of
a bug -- check the actual rendered page before treating one as real.
"""

import contextlib
from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.checks.escalation import NEEDS_REVIEW_OUTCOMES as _NEEDS_REVIEW_OUTCOMES_ENUM
from app.report.schemas import CriterionResultOut, FlagSummaryOut, ReportExportData

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
# `backend-critic` finding (BUG-081/082 review): this used to be a
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
# every pre-migration run (backend-critic finding, live-reproduced
# against report/29 -- the ticket's own reproduction case).
_LLM_MODE_DISCLOSURE = {
    "fake": (
        "Test-mode run: AI results are simulated and do not describe this document.",
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
        "score": ParagraphStyle("score", fontName=bold, fontSize=20, leading=24, textColor=_INK),
        "h2": ParagraphStyle(
            "h2",
            fontName=bold,
            fontSize=13,
            leading=16,
            textColor=_INK,
            spaceBefore=14,
            spaceAfter=6,
        ),
        "h3": ParagraphStyle(
            "h3",
            fontName=bold,
            fontSize=10,
            leading=13,
            textColor=_INK_TERTIARY,
            spaceBefore=8,
            spaceAfter=3,
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
# `backend-critic` finding (live-reproduced, frontend half): the first
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
    prior history as `_format_weight` (ux-critic P2, live-reproduced) for
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
    depending on a platform-specific flag."""
    date_part = f"{dt:%b} {dt.day}, {dt:%Y}"
    if not with_time:
        return date_part
    hour12 = dt.hour % 12 or 12
    return f"{date_part}, {hour12}:{dt:%M %p}"


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


def _archive_disclosure(archive_size_n: int | None) -> str:
    if archive_size_n is None:
        return (
            "Originality and reuse check did not run for this manuscript "
            "(no comparable content could be extracted)."
        )
    if archive_size_n == 0:
        return (
            "Originality and reuse check: compared against 0 previously processed manuscripts. "
            "This may be one of the first checked under this rubric family."
        )
    return (
        f"Originality and reuse check: compared against {archive_size_n} previously "
        "processed manuscripts in VERIDICAL's archive."
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
    is_partial = row.outcome == "passed" and row.score is not None and round(row.score) == 50
    if is_partial:
        return "Partial", "caution"
    return {
        "passed": ("Pass", "success"),
        "failed": ("Fail", "danger"),
        "not_applicable": ("Not applicable", "neutral"),
        "unverifiable": ("Unverifiable", "neutral"),
    }.get(row.outcome, (row.outcome, "neutral"))


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
    if row.resolution:
        verb = RESOLUTION_VERB.get(row.resolution.type, "Resolved by instructor")
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
        parts.append(Paragraph(f"Anchor: {escape(row.evidence[0].anchor)}", styles["cell_caption"]))
    elif row.reasoning or row.reason:
        parts.append(Paragraph(escape(row.reasoning or row.reason or ""), styles["cell"]))
    parts.append(Paragraph(" · ".join(escape(c) for c in caption_bits), styles["cell_caption"]))
    return parts


def build_report_pdf(data: ReportExportData) -> bytes:
    font = _register_fonts()
    styles = _styles(font)
    report = data.report
    is_draft = report.decision is None

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=98,
        bottomMargin=56,
        leftMargin=54,
        rightMargin=54,
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
    generated_at = _format_date(datetime.now(), with_time=True)
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
    if report.composite_score is not None:
        flow.append(Paragraph(f"{report.composite_score}%", styles["score"]))
    flow.append(
        Paragraph(
            f"Ready at {report.thresholds['ready_min_score']}% or above. "
            f"Not Ready at {report.thresholds['not_ready_max_score']}% or below.",
            styles["meta"],
        )
    )
    flow.append(Spacer(1, 4))
    flow.append(Paragraph(escape(_explainer(report)), styles["body"]))
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
                Paragraph(_pending_review_reason(row.outcome), styles["body"]),
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
    flow.append(Paragraph(f"Flags ({len(data.flags)})", styles["h2"]))
    flow.append(
        Paragraph(
            "Possible inconsistencies found by VERIDICAL's integrity checks (F4 to F7), not "
            "accusations.",
            styles["caption_italic"],
        )
    )
    flow.append(Spacer(1, 6))
    if not data.flags:
        flow.append(
            Paragraph(
                "No integrity flags on this run. VERIDICAL's four integrity checks (internal "
                "agreement, citation integrity, statistical forensics, and originality/reuse) "
                "found nothing to report.",
                styles["body"],
            )
        )
    else:
        by_kind: dict[str, list[FlagSummaryOut]] = {}
        for f in data.flags:
            by_kind.setdefault(f.check_kind, []).append(f)
        for kind in sorted(by_kind, key=lambda k: _CHECK_KIND_ORDER.get(k, len(_CHECK_KIND_ORDER))):
            group = by_kind[kind]
            flow.append(
                Paragraph(
                    f"{_CHECK_KIND_LABEL.get(kind, kind).upper()} ({len(group)})", styles["h3"]
                )
            )
            for f in group:
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
                caption_text = f"Anchor: {escape(f.page_anchor)} · {escape(severity_label)}"
                if f.overridden:
                    caption_text += " · Overridden"
                header = Paragraph(caption_text, caption_style)
                excerpt = Paragraph(escape(f.evidence_excerpt), styles["body"])
                # Only the short header needs to stay glued to the excerpt
                # that follows it; the excerpt itself paginates normally if
                # genuinely long (never truncated -- this IS the evidence
                # an instructor/adviser verifies independently).
                flow.append(KeepTogether([header, Spacer(1, 2), excerpt]))
                flow.append(Spacer(1, 8))
    flow.append(Spacer(1, 4))
    flow.append(Paragraph(_archive_disclosure(data.archive_size_n), styles["caption"]))
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
        flow.append(
            Paragraph("No decision has been recorded for this manuscript yet.", styles["body"])
        )
        if pending:
            flow.append(
                Paragraph(
                    f"{len(pending)} criteri{'on' if len(pending) == 1 else 'a'} still needed the "
                    "instructor's review at the time this document was generated.",
                    styles["body"],
                )
            )

    footer_llm_disclosure = _LLM_MODE_DISCLOSURE.get(report.llm_mode)

    def _on_page(canvas: Canvas, doc_: SimpleDocTemplate) -> None:
        canvas.saveState()
        page_num = canvas.getPageNumber()

        if page_num > 1:
            canvas.setFont(font, 8)
            canvas.setFillColor(_INK_TERTIARY)
            canvas.drawString(54, A4[1] - 40, "VERIDICAL - Readiness Report")
            group = report.manuscript_group_label
            truncated = group if len(group) <= 45 else group[:44] + "…"
            canvas.drawRightString(A4[0] - 54, A4[1] - 40, truncated)
            canvas.setStrokeColor(_RULE)
            canvas.setLineWidth(0.5)
            canvas.line(54, A4[1] - 48, A4[0] - 54, A4[1] - 48)

        canvas.setFont(font, 8)
        canvas.setFillColor(_INK_TERTIARY)
        canvas.drawString(54, 40, f"Generated {generated_at}")
        canvas.drawCentredString(A4[0] / 2, 40, f"Page {page_num}")
        if is_draft:
            canvas.setFont(f"{font}-Bold", 8)
            canvas.setFillColor(_DRAFT_GRAY)
            canvas.drawRightString(
                A4[0] - 54,
                40,
                f"DRAFT - {report.manuscript_group_label} has no final decision yet",
            )
        canvas.setFont(font, 8)
        canvas.setFillColor(_INK_TERTIARY)
        canvas.drawCentredString(
            A4[0] / 2,
            28,
            "This report reflects the instructor's own review and decisions. It is not an "
            "automated grade.",
        )
        if footer_llm_disclosure is not None:
            footer_text, footer_tone = footer_llm_disclosure
            canvas.setFont(f"{font}-Bold", 8)
            canvas.setFillColor(_TONE_COLORS[footer_tone][1])
            canvas.drawCentredString(A4[0] / 2, 16, footer_text)

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
