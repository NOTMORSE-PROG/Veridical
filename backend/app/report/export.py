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
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    CondPageBreak,
    Frame,
    HRFlowable,
    Image,
    PTOContainer,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.checks.escalation import NEEDS_REVIEW_OUTCOMES as _NEEDS_REVIEW_OUTCOMES_ENUM
from app.config import get_settings
from app.library.passage_indexing import indexing_scope_sentences
from app.report.rule_facts import rule_facts_copy, rule_facts_presentation
from app.report.schemas import (
    CriterionResultOut,
    FlagExportDetailOut,
    FlagSummaryOut,
    PageEvidenceImageOut,
    ReportExportData,
)
from app.report.source_coverage import project_source_coverage
from app.report.source_inventory import NativeSourceNote, group_native_source_notes
from app.report.source_print import source_evidence_flowables
from app.report.statistical_scope import statistical_scope_copy
from app.report.text import Paragraph, clusters, draw_right_text, register_fonts, text_width


class _NavigableParagraph(Paragraph):
    """Keep a destination on the first fragment of a potentially long heading."""

    navigation_key = None
    navigation_label = None

    def split(self, availWidth, availHeight):
        fragments = super().split(availWidth, availHeight)
        if fragments:
            fragments[0].navigation_key = self.navigation_key
            fragments[0].navigation_label = self.navigation_label
        return fragments

    def draw(self):
        if self.navigation_key:
            self.canv.bookmarkHorizontal(self.navigation_key, 0, 0)
            self.canv.addOutlineEntry(self.navigation_label, self.navigation_key, level=0)
        super().draw()


def _navigation_heading(text, style, key, label):
    paragraph = _NavigableParagraph(text, style)
    paragraph.navigation_key = key
    paragraph.navigation_label = label
    return paragraph


def _is_optional_comparison_lead(flag: FlagSummaryOut) -> bool:
    return flag.check_kind == "originality_reuse" and flag.affects_readiness is False


def _review_record_identities(flags: list[FlagSummaryOut]) -> dict[int, str]:
    leads = sorted(
        (flag for flag in flags if _is_optional_comparison_lead(flag)), key=lambda f: f.id
    )
    identities = {
        flag.id: f"Comparison lead {ordinal} of {len(leads)}. Record #{flag.id}"
        for ordinal, flag in enumerate(leads, start=1)
    }
    identities.update(
        {
            flag.id: f"Finding {ordinal}"
            for ordinal, flag in enumerate(
                (flag for flag in flags if not _is_optional_comparison_lead(flag)), start=1
            )
        }
    )
    return identities


def _register_fonts() -> str:
    return register_fonts()


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
    "top_margin": 98,
    "source_entry_gap": 8,
    "image_caption_gap": 2,
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
def _native_source_note_copy() -> dict[str, str]:
    return json.loads(
        (Path(__file__).parent / "data" / "native_source_note_copy.json").read_text("utf-8")
    )


def _native_source_note_flowables(
    note: NativeSourceNote,
    styles: dict[str, ParagraphStyle],
    frame_size: tuple[float, float] | None = None,
) -> list:
    copy = _native_source_note_copy()
    labels = ", ".join(_CHECK_KIND_LABEL.get(kind, kind) for kind in note.checks)
    title = Paragraph(escape(copy["heading"].format(number=note.number)), styles["body_bold"])
    title.keepWithNext = True
    body = [Paragraph(escape(copy["applies"].format(checks=labels)), styles["body"])]
    if len(note.checks) > 1:
        body.append(Paragraph(escape(copy["shared"]), styles["body"]))
    if note.state == "complete" and note.count is not None:
        body.append(
            Paragraph(
                escape(
                    copy["count"].format(
                        count=note.count, noun=copy["singular" if note.count == 1 else "plural"]
                    )
                ),
                styles["body"],
            )
        )
        body.append(Paragraph(escape(copy["scope"]), styles["body"]))
        if note.unlocated:
            body.append(
                Paragraph(
                    escape(
                        copy["unlocated"].format(
                            count=note.unlocated,
                            noun=copy[
                                "unlocated_singular" if note.unlocated == 1 else "unlocated_plural"
                            ],
                        )
                    ),
                    styles["body"],
                )
            )
        elif note.unlocated is None:
            body.append(Paragraph(escape(copy["unlocated_unknown"]), styles["body"]))
    else:
        body.append(Paragraph(escape(copy.get(note.state, copy["unknown"])), styles["body"]))
    initial = []
    if frame_size:
        width, height = frame_size
        prefix = [title, *body]
        required = sum(
            part.wrap(width, height)[1] + part.getSpaceBefore() + part.getSpaceAfter()
            for part in prefix
        )
        if required <= height:
            initial.append(CondPageBreak(required))
    if note.excluded:
        body.append(Paragraph(escape(copy["excluded"]), styles["body"]))
        body.append(
            Paragraph(
                escape(
                    copy["excluded_positions"].format(
                        positions=", ".join(str(value + 1) for value in note.excluded)
                    )
                ),
                styles["caption"],
            )
        )
    for kind, paragraph, count in note.items:
        anchor = (
            f"Extracted paragraph {paragraph + 1}"
            if paragraph is not None
            else "Source location unavailable"
        )
        noun = "occurrence" if count == 1 else "occurrences"
        location_kind = ", table" if kind == "table_cell" else ""
        body.append(
            Paragraph(
                escape(
                    f"{anchor}: Native Word equations unassessed ({count} {noun}{location_kind})."
                ),
                styles["caption"],
            )
        )
    return [
        *initial,
        title,
        PTOContainer(
            body,
            header=[
                Paragraph(
                    escape(copy["continued"].format(number=note.number)), styles["caption_bold"]
                )
            ],
        ),
    ]


@lru_cache
def _semantic_support_labels() -> dict[str, str]:
    return json.loads(
        (Path(__file__).parent / "data" / "semantic_support_labels.json").read_text("utf-8")
    )


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
    if text_width(text, font, size) <= width:
        return text
    suffix = "..."
    pieces = list(clusters(text))
    low, high = 0, len(pieces)
    while low < high:
        middle = (low + high + 1) // 2
        if text_width("".join(pieces[:middle]) + suffix, font, size) <= width:
            low = middle
        else:
            high = middle - 1
    return "".join(pieces[:low]) + suffix


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
    "needs_document": "Excluded from readiness calculation: needs another document",
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
# disclosure at all; `unknown` means the record does not establish response
# provenance. Missing mode metadata alone does not establish the run's age.
_LLM_MODE_DISCLOSURE = {
    "fake": (
        "Test-mode AI responses. This run is recorded in test AI mode. "
        "AI responses use fixtures instead of real AI grading. "
        "Do not treat AI-derived judgments as a real assessment of the manuscript. "
        "Check the recorded evidence directly.",
        "danger",
    ),
    "unknown": (
        "AI response mode could not be verified. This record does not establish whether "
        "responses came from a real external model or simulated fixtures. "
        "Check the recorded evidence directly before relying on AI-derived results.",
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


def _originality_scope_flowables(data: ReportExportData, styles: dict[str, ParagraphStyle]) -> list:
    """F7 qualifications belong before the comparison records they qualify."""
    scope = data.report.originality_scope
    items = [
        Paragraph(
            _archive_disclosure(
                data.archive_size_n,
                scope.corpus if scope else None,
                scope.not_compared_reason if scope else None,
            ),
            styles["caption"],
        ),
        Spacer(1, 10),
    ]
    if scope and scope.compared_count and not scope.not_compared_reason:
        sentences = indexing_scope_sentences(scope.library_passage_indexing)
        if sentences and scope.library_passage_indexing is not None:
            items.append(Paragraph("Passage comparison limits", styles["h3"]))
        items.extend(Paragraph(escape(sentence), styles["caption"]) for sentence in sentences)
        if sentences:
            items.append(Spacer(1, 10))
    return items


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
    if (
        row.rule_scope_unconfirmed
        and row.resolution is None
        and row.outcome in {"passed", "failed"}
    ):
        saved_label, _ = _result_display(row.model_copy(update={"rule_scope_unconfirmed": False}))
        return f"Previously recorded: {saved_label}", "neutral"
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
    return _semantic_support_labels()["pending"]


def _pending_semantic_basis_flowables(
    row: CriterionResultOut, styles: dict[str, ParagraphStyle], *, include_evidence=True
) -> list:
    if row.kind != "semantic" or row.basis != "llm" or row.outcome != "escalated":
        return []
    labels = _semantic_support_labels()
    parts = [
        Paragraph(escape(labels["review"]), styles["body"]),
        Paragraph(escape(labels["qualification"]), styles["body"]),
    ]
    if row.reasoning and row.reasoning.strip():
        parts.append(Paragraph("Recorded AI explanation", styles["body_bold"]))
        parts.append(Paragraph(escape(row.reasoning), styles["body"]))
    else:
        parts.append(Paragraph(escape(labels["missing_explanation"]), styles["body"]))
    if row.evidence and include_evidence:
        parts.append(Paragraph("Recorded passages and locations", styles["body_bold"]))
        for entry in row.evidence:
            parts.append(Paragraph(escape(entry.quote), styles["body"]))
            location = (
                _display_anchor(entry.anchor)
                if entry.anchor.strip()
                else labels["missing_location"]
            )
            parts.append(Paragraph(escape(location), styles["caption"]))
    elif not row.evidence:
        parts.append(Paragraph(escape(labels["missing_excerpt"]), styles["body"]))
    for part in parts:
        if part.style is styles["body_bold"]:
            part.keepWithNext = True
    return parts


def _criterion_source_flowables(row: CriterionResultOut, styles: dict[str, ParagraphStyle]) -> list:
    coverage = row.source_coverage
    if coverage is None:
        if row.kind != "semantic":
            return []
        coverage = project_source_coverage(None)
    state = coverage.native_math_inventory_state
    if state == "not_applicable" or (state == "not_recorded" and row.kind != "semantic"):
        return []
    count = coverage.n_native_equations_unassessed
    if state == "complete" and count == 0:
        return []
    copy = _native_source_note_copy()
    title = Paragraph(
        escape(copy["criterion_earlier_heading" if row.resolution else "criterion_heading"]),
        styles["body_bold"],
    )
    title.keepWithNext = True
    parts = [title]
    if state == "complete":
        parts.append(
            Paragraph(
                escape(
                    copy["criterion_omitted"].format(
                        count=count,
                        noun=copy["criterion_singular" if count == 1 else "criterion_plural"],
                    )
                ),
                styles["body"],
            )
        )
        if coverage.native_math_excluded_paragraphs:
            parts.append(Paragraph(escape(copy["excluded"]), styles["body"]))
        if coverage.n_native_equations_unlocated:
            parts.append(Paragraph(escape(copy["unlocated_limit"]), styles["body"]))
        parts.append(Paragraph(escape(copy["scope"]), styles["caption"]))
    else:
        parts.append(Paragraph(escape(copy[state]), styles["body"]))
    return parts


def _rule_facts_flowables(row: CriterionResultOut, styles: dict[str, ParagraphStyle]) -> list:
    if row.rule_facts is None:
        return []
    paragraphs, headings = rule_facts_presentation(row.rule_facts)
    title = Paragraph(
        escape(rule_facts_copy()["earlier_heading" if row.resolution else "heading"]),
        styles["body_bold"],
    )
    title.keepWithNext = True
    parts = []
    if row.rule_scope_unconfirmed:
        qualification = rule_facts_copy()["scope_qualification"]
        if row.resolution:
            qualification = "Earlier rule record. " + qualification
        parts.append(Paragraph(escape(qualification), styles["body"]))
    parts.append(title)
    for index, text in enumerate(paragraphs):
        parts.append(Paragraph(escape(text), styles["body"]))
        if index == 0:
            for number, heading in enumerate(headings, start=1):
                parts.append(Paragraph(escape(f"{number}. {heading}"), styles["body"]))
    return parts


def _criterion_flowable(
    row: CriterionResultOut, styles: dict[str, ParagraphStyle], *, include_evidence=True
) -> list:
    title = f"Criterion #{row.criterion_id}: {row.text}" if row.resolution else row.text
    parts = [_navigation_heading(escape(title), styles["cell_bold"],
                                 f"criterion-{row.criterion_id}", title)]
    if not row.resolution:
        parts.extend(_criterion_source_flowables(row, styles))
    caption_bits = [] if row.resolution else [_source_caption(row)]
    # V-069 AC5: the decided rung's own points, alongside the level name
    # already shown as the row's Result column — this is the "score" half
    # of AC2's "shows a per-criterion level and score."
    if row.level is not None:
        caption_bits.append(f"{row.level.points:g}/{row.level.max_points:g} pts")
    if row.resolution:
        parts[0].keepWithNext = True
        verb = (
            f"Marked {row.level.name}"
            if row.resolution.type == "mark_level" and row.level is not None
            else RESOLUTION_VERB.get(row.resolution.type, "Judgment recorded")
        )
        parts.append(
            Paragraph(
                f"Instructor judgment recorded. <b>{escape(verb)}.</b> "
                f"{escape(row.resolution.reason)}",
                styles["cell"],
            )
        )
        if row.resolution.type == "needs_document":
            parts.append(Paragraph("No grade recorded.", styles["cell_caption"]))
        if row.resolution.ai_majority_verdict:
            parts.append(
                Paragraph(
                    f"{escape(_semantic_support_labels()['recorded_suggestion'])} "
                    f"{escape(row.resolution.ai_majority_verdict)}.",
                    styles["cell_caption"],
                )
            )
        if row.reasoning and row.reasoning.strip():
            parts.append(Paragraph(
                "<b>Earlier recorded system explanation:</b> " + escape(row.reasoning),
                styles["cell"],
            ))
        if row.reason and row.reason.strip() and row.reason != row.reasoning:
            parts.append(Paragraph(
                "<b>Earlier recorded system note:</b> " + escape(row.reason), styles["cell"],
            ))
        if include_evidence:
            for evidence in row.evidence:
                parts.append(Paragraph(escape(evidence.quote), styles["cell"]))
                parts.append(Paragraph(
                    f"Anchor: {escape(_display_anchor(evidence.anchor))}", styles["cell_caption"],
                ))
        parts.extend(_criterion_source_flowables(row, styles))
    elif row.evidence and include_evidence:
        for evidence in row.evidence:
            parts.append(Paragraph(escape(evidence.quote), styles["cell"]))
            parts.append(Paragraph(
                f"Anchor: {escape(_display_anchor(evidence.anchor))}", styles["cell_caption"]
            ))
        if row.reasoning or row.reason:
            parts.append(Paragraph(escape(row.reasoning or row.reason or ""), styles["cell"]))
    elif row.reasoning or row.reason:
        parts.append(Paragraph(escape(row.reasoning or row.reason or ""), styles["cell"]))
    if not row.resolution and row.reasoning and row.reason and row.reason != row.reasoning:
        parts.append(Paragraph(
            "<b>Recorded system note:</b> " + escape(row.reason), styles["cell"]
        ))
    facts = _rule_facts_flowables(row, styles)
    if facts and not row.resolution:
        parts[0].keepWithNext = True
    parts.extend(facts)
    if caption_bits:
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


def _report_footer_parts(report, styles, font, generated_at):
    content_width = A4[0] - 2 * _PRINT_LAYOUT["side_margin"]
    footer_llm_disclosure = _LLM_MODE_DISCLOSURE.get(report.llm_mode)
    is_draft = report.decision is None
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
        Paragraph('<link href="#report-contents">Return to contents</link>', footer_style),
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

    return footer_rows, footer_style, footer_timestamp, timestamp_y, bottom_margin


def source_image_print_width():
    return Frame(0, 0, A4[0] - 2 * _PRINT_LAYOUT["side_margin"], A4[1])._aW


def source_image_settings_for(report, settings):
    from app.report.source_print import source_image_height

    font = _register_fonts()
    styles = _styles(font)
    generated_at = _format_date(datetime.now(UTC), with_time=True)
    *_, bottom_margin = _report_footer_parts(report, styles, font, generated_at)
    frame = Frame(
        0,
        0,
        A4[0] - 2 * _PRINT_LAYOUT["side_margin"],
        A4[1] - _PRINT_LAYOUT["top_margin"] - bottom_margin,
    )

    def measured(reference):
        height = source_image_height(
            reference, frame._aW, frame._aH, styles, _PRINT_LAYOUT, settings
        )
        return settings.model_copy(update={"export_page_image_max_height": height})

    return measured


def build_report_pdf(data: ReportExportData) -> bytes:
    font = _register_fonts()
    styles = _styles(font)
    report = data.report
    is_draft = report.decision is None
    pending = [r for r in report.results if r.outcome in NEEDS_REVIEW_OUTCOMES and not r.resolution]
    export_dpi = get_settings().export_page_image_dpi
    generated_at = _format_date(datetime.now(UTC), with_time=True)
    side_margin = _PRINT_LAYOUT["side_margin"]
    content_width = A4[0] - 2 * side_margin
    footer_rows, footer_style, footer_timestamp, timestamp_y, bottom_margin = _report_footer_parts(
        report, styles, font, generated_at
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=_PRINT_LAYOUT["top_margin"],
        bottomMargin=bottom_margin,
        leftMargin=side_margin,
        rightMargin=side_margin,
        title=f"VERIDICAL Readiness Report - {report.manuscript_group_label}",
        author="VERIDICAL",
        subject="Capstone manuscript readiness report",
    )
    content_width = doc.width
    source_frame = Frame(0, 0, doc.width, doc.height)
    source_width = source_frame._aW

    def source_blocks(entries):
        return [
            part
            for entry in entries
            for part in source_evidence_flowables(
                entry,
                source_width,
                styles,
                export_dpi,
                _PRINT_LAYOUT,
                frame_height=source_frame._aH,
            )
        ]

    def criterion_sources(row):
        return [
            entry
            for entry in (data.source_evidence or ())
            if entry.criterion_id == row.criterion_id
        ]

    flow: list = []

    # 1. Title block.
    manuscript_title = report.subject.title or report.manuscript_original_filename
    flow.append(_navigation_heading(escape(manuscript_title or report.manuscript_group_label),
                                    styles["title"], "report-overview", "Report overview"))
    flow.append(Paragraph("VERIDICAL Readiness Report", styles["body_bold"]))
    if report.subject.basis == "confirmed_assignment_at_check":
        flow.append(Paragraph(
            "Group members recorded when this check began: "
            + escape(", ".join(report.subject.member_names) or "Not recorded"), styles["meta"]
        ))
        flow.append(Paragraph(
            "Title and members are the instructor-confirmed assignment saved for this check.",
            styles["caption"],
        ))
    else:
        flow.append(Paragraph(
            "Manuscript title and authors were not recorded with this check. "
            "The original filename and group label identify this historical report.",
            styles["caption"],
        ))
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

    # F8.5: show the human decision before the supporting assessment/appendix.
    flow.append(Paragraph("Instructor decision", styles["body_bold"]))
    if report.decision:
        flow.append(
            _tone_badge(
                DECISION_LABEL.get(report.decision, report.decision),
                DECISION_TONE.get(report.decision, "neutral"),
                styles,
            )
        )
        if report.decided_at:
            flow.append(
                Paragraph(f"Recorded {_format_date(report.decided_at)}.", styles["caption"])
            )
        if pending:
            noun = "criterion remains" if len(pending) == 1 else "criteria remain"
            flow.append(
                Paragraph(f"{len(pending)} {noun} unresolved in this report.", styles["body"])
            )
            if report.decision == "returned":
                flow.append(Paragraph("Returning did not grade or exclude them.", styles["body"]))
            flow.append(
                Paragraph("Reopen the report before changing their judgments.", styles["body"])
            )
        flow.append(
            Paragraph(
                '<link href="#instructor-decision-record">'
                "See decision reason and full decision record.</link>",
                styles["caption"],
            )
        )
    else:
        flow.append(Paragraph("Not recorded", styles["body"]))
    flow.append(Spacer(1, 6))
    unconfirmed = sum(
        row.rule_scope_unconfirmed
        and row.resolution is None
        and row.outcome in {"passed", "failed"}
        for row in report.results
    )
    flow.append(
        Paragraph(
            "Saved readiness band" if unconfirmed else "System readiness", styles["body_bold"]
        )
    )
    # 2. System band and subordinate calculation detail.
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
        label = {
            "recorded_decision": "Recorded composite detail",
            "recomputed_without_recorded_breakdown": (
                "Recomputed composite detail under the current policy"
            ),
        }.get(report.assessment_provenance, "Composite score")
        qualification = (
            "partial criterion coverage; not the readiness verdict"
            if pending
            else "reproducible detail, not the verdict; see the readiness band above"
        )
        flow.append(
            Paragraph(
                f"{label}: {report.composite_score}% ({qualification}).",
                styles["caption"],
            )
        )
    else:
        flow.append(Paragraph("Composite detail: not available.", styles["caption"]))
    if unconfirmed:
        noun = "result" if unconfirmed == 1 else "results"
        flow.append(
            Paragraph(
                f"Complete-requirement coverage is unconfirmed for {unconfirmed} recorded rule "
                f"{noun}. Review the criterion records before relying on this saved band.",
                styles["body"],
            )
        )
    if pending:
        flow.append(
            Paragraph(
                f"Assessment coverage is incomplete: {len(pending)} of {len(report.results)} "
                "criteria remain unresolved. This is not an assessment of every criterion.",
                styles["body"],
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
                    f"Rubric formula detail: {lr.achieved_points:g}/{lr.max_points:g} points = "
                    f"{lr.rating_percent}% ({lr.n_decided} of {lr.n_levelled} levelled criteria "
                    "with recorded grades). "
                    + (
                        "Other criteria remain ungraded or unresolved as recorded. "
                        if lr.n_decided < lr.n_levelled
                        else ""
                    )
                    + "This is the rubric's formula, not the VERIDICAL readiness verdict."
                ),
                styles["caption"],
            )
        )
    flow.append(Spacer(1, 10))

    flow.append(_navigation_heading("Report contents", styles["h2"],
                                    "report-contents", "Report contents"))
    flow.append(Paragraph(
        "Select an item to open its complete record. PDF bookmarks provide the same navigation.",
        styles["caption"],
    ))
    flow.append(Paragraph('<link href="#report-overview">Readiness and instructor decision</link>',
                          styles["body"]))
    for row in report.results:
        flow.append(Paragraph(
            f'<link href="#criterion-{row.criterion_id}">Criterion #{row.criterion_id}</link>',
            styles["body"],
        ))
    record_identities = _review_record_identities(data.flags)
    for flag in data.flags:
        flow.append(Paragraph(
            f'<link href="#finding-{flag.id}">{escape(record_identities[flag.id])}</link>',
            styles["body"],
        ))
    flow.append(Paragraph('<link href="#instructor-decision-record">Full decision record</link>',
                          styles["body"]))
    flow.append(Spacer(1, _PRINT_LAYOUT["source_entry_gap"]))
    flow.append(Paragraph("Grades at a glance", styles["h2"]))
    for row in report.results:
        label, _tone = _result_display(row)
        flow.append(Paragraph(
            f'<link href="#criterion-{row.criterion_id}">Criterion #{row.criterion_id}</link>: '
            f'{escape(label)}. '
            + ("Instructor judgment recorded." if row.resolution else "Recorded system outcome."),
            styles["body"],
        ))
    flow.append(Spacer(1, _PRINT_LAYOUT["source_entry_gap"]))

    if data.source_evidence:
        included = sum(entry.image.image_png is not None for entry in data.source_evidence)
        without_image = len(data.source_evidence) - included
        flow.append(
            Paragraph(
                f"{included} source image{'s' if included != 1 else ''} included. "
                f"{without_image} recorded evidence "
                f"{'entry is' if without_image == 1 else 'entries are'} shown "
                "without an image; see their individual notes.",
                styles["caption"],
            )
        )
        flow.append(Spacer(1, _PRINT_LAYOUT["source_entry_gap"]))

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
    if pending:
        flow.append(
            Paragraph(
                "Needs Your Review" if is_draft else "Unresolved At Time Of Decision", styles["h2"]
            )
        )
        if not is_draft:
            flow.append(
                Paragraph(
                    "These criteria remain unresolved. Reopen the report before recording "
                    "or changing their judgments.",
                    styles["body"],
                )
            )
        for row in pending:
            block = [
                _navigation_heading(
                    f"Criterion #{row.criterion_id}: {escape(row.text)} "
                    f"<i>({_format_weight_importance(row.weight_importance, long=True)})</i>",
                    styles["body_bold"],
                    f"criterion-{row.criterion_id}", f"Criterion #{row.criterion_id}: {row.text}",
                ),
                *_criterion_source_flowables(row, styles),
                Paragraph(escape(_pending_review_reason(row.outcome)), styles["body"]),
                *(
                    [Paragraph(escape(row.reason), styles["body"])]
                    if row.reason and row.reason.strip()
                    else []
                ),
                Paragraph(
                    "Not yet resolved. This is why this export is marked DRAFT."
                    if is_draft
                    else "Not yet resolved when the instructor recorded this decision.",
                    styles["body_bold"],
                ),
            ]
            for part in block:
                part.keepWithNext = True
            flow.append(
                PTOContainer(
                    [
                        *block,
                        *_rule_facts_flowables(row, styles),
                        *_pending_semantic_basis_flowables(
                            row, styles, include_evidence=data.source_evidence is None
                        ),
                        *source_blocks(criterion_sources(row)),
                    ],
                    header=[
                        Paragraph(
                            escape(f"Criterion #{row.criterion_id} (continued)"),
                            styles["caption_bold"],
                        )
                    ],
                )
            )
            flow.append(Spacer(1, 6))
        flow.append(Spacer(1, 6))

    # 4. Flags.
    if (
        report.integrity_check_status
        or report.historical_citation_title_unverified
        or report.historical_native_math_unverified
    ):
        flow.append(Paragraph("Assessment limits", styles["h2"]))
        if report.historical_native_math_unverified:
            flow.append(
                Paragraph(
                    escape(_coverage_labels()["historical_native_math_unverified"]), styles["body"]
                )
            )
        if report.historical_citation_title_unverified:
            flow.append(
                Paragraph(
                    escape(_coverage_labels()["historical_citation_title_unverified"]),
                    styles["body"],
                )
            )
        coverages = sorted(
            report.integrity_check_status,
            key=lambda value: _CHECK_KIND_ORDER.get(value.check_kind, len(_CHECK_KIND_ORDER)),
        )
        native_notes, native_references = group_native_source_notes(coverages)
        for coverage, native_reference in zip(coverages, native_references, strict=True):
            label = _CHECK_KIND_LABEL.get(coverage.check_kind, coverage.check_kind)
            reason = _coverage_labels().get(
                coverage.coverage_reason,
                (coverage.coverage_reason or coverage.outcome).replace("_", " "),
            )
            if coverage.coverage_reason == "statistical_notation_unassessed":
                key = (
                    "statistical_notation_partial"
                    if coverage.analysis_coverage == "partial"
                    else "statistical_notation_unsupported"
                )
                reason = (
                    _coverage_labels()[key] + " " + _coverage_labels()["statistical_notation_limit"]
                ).rstrip(".")
            elif coverage.assessment_scope == "reference_metadata_only":
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
                    "text or tables. Some source content remains unassessed. "
                    "This limit is not a statistical error"
                    if coverage.analysis_coverage == "partial"
                    else "Not assessed. No supported statistical statements were found in "
                    "the text and tables used by this check. Unassessed source content may "
                    "contain information this check missed. This does not mean the manuscript "
                    "contains no statistics"
                )
            elif coverage.analysis_coverage == "unsupported":
                reason = (
                    "Not assessed. VERIDICAL could not establish a reliable source for this check. "
                    "No conclusion was made"
                )
            if (
                coverage.statistical_notation_unassessed is True
                and coverage.coverage_reason != "statistical_notation_unassessed"
            ):
                key = (
                    "statistical_notation_partial"
                    if coverage.analysis_coverage == "partial"
                    else "statistical_notation_unsupported"
                )
                reason = (
                    reason.rstrip(".")
                    + ". "
                    + _coverage_labels()[key]
                    + " "
                    + _coverage_labels()["statistical_notation_limit"]
                ).rstrip(".")
            counts = []
            if coverage.n_source_candidates_withheld:
                counts.append(
                    f"{coverage.n_source_candidates_withheld} selected comparison candidates "
                    "were withheld because their source could not be verified"
                )
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
            if native_reference is not None:
                counts.append(f"Native source coverage: note {native_reference}")
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
        for note in native_notes:
            print_frame = Frame(0, 0, doc.width, doc.height)
            flow.extend(
                _native_source_note_flowables(note, styles, (print_frame._aW, print_frame._aH))
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
    if data.report.figure_readings:
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
        labels = _semantic_support_labels()
        flow.append(Paragraph(escape(labels["title"]), styles["h2"]))
        flow.append(Paragraph(escape(labels["body"]), styles["body"]))
    other_flag_count = sum(not _is_optional_comparison_lead(flag) for flag in data.flags)
    lead_count = len(data.flags) - other_flag_count
    flag_intro = [Paragraph("Integrity review", styles["h2"])]
    scope_label, scope_detail = statistical_scope_copy(report.statistical_check_scope)
    flag_intro.append(Paragraph(escape(scope_label), styles["body"]))
    flag_intro.append(Paragraph(escape(scope_detail), styles["body"]))
    flag_intro.append(
        Paragraph(
            f"Other integrity records: {other_flag_count}. "
            f"Optional comparison leads: {lead_count}.",
            styles["body"],
        )
    )
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
    scope_emitted = False
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
        identities = _review_record_identities(data.flags)
        by_kind: dict[tuple[str, bool], list[FlagSummaryOut]] = {}
        for f in data.flags:
            by_kind.setdefault((f.check_kind, _is_optional_comparison_lead(f)), []).append(f)
        for kind, optional in sorted(
            by_kind, key=lambda key: (key[1], _CHECK_KIND_ORDER.get(key[0], len(_CHECK_KIND_ORDER)))
        ):
            group = by_kind[(kind, optional)]
            if optional:
                group = sorted(group, key=lambda flag: flag.id)
            category_heading = Paragraph(
                f"Optional comparison leads ({len(group)})"
                if optional
                else f"{_CHECK_KIND_LABEL.get(kind, kind).upper()} ({len(group)})",
                styles["h3"],
            )
            for group_index, f in enumerate(group):
                detail = data.flag_details.get(f.id, FlagExportDetailOut())
                common_entries = [
                    entry for entry in (data.source_evidence or ()) if entry.flag_id == f.id
                ]
                record_identity = identities[f.id]
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
                    styles["caption_bold"]
                    if f.severity == "high" and not optional
                    else styles["caption"]
                )
                caption_text = f"Anchor: {escape(_display_anchor(f.page_anchor))} · " + (
                    "Comparison lead: not scored" if optional else escape(severity_label)
                )
                if f.overridden:
                    caption_text += " · Overridden"
                if f.affects_readiness is False and not optional:
                    caption_text += " · Not scored"
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
                heading = _navigation_heading(escape(f"{record_identity}: {issue}"),
                    styles["body_bold"], f"finding-{f.id}", f"{record_identity}: {issue}")
                evidence_label = Paragraph(
                    "System comparison summary"
                    if detail.evidence_is_summary
                    else "Manuscript evidence",
                    styles["caption_bold"],
                )
                prefix = list(flag_intro) + [category_heading] if group_index == 0 else []
                if optional and group_index == 0:
                    prefix.append(
                        Paragraph(
                            "Each record is a saved comparison lead. Records can overlap or cover "
                            "different scopes. These leads do not affect readiness or block the "
                            "instructor decision.",
                            styles["body"],
                        )
                    )
                if kind == "originality_reuse" and group_index == 0 and not scope_emitted:
                    prefix.extend(_originality_scope_flowables(data, styles))
                    scope_emitted = True
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
                finding = [*start_items]
                if not any(entry.quote for entry in common_entries):
                    finding.append(excerpt)
                finding.extend(source_blocks(common_entries))
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
                    if any(
                        entry.quote == comparison.text and entry.anchor == comparison.anchor
                        for entry in common_entries
                    ):
                        finding.append(Paragraph(escape(comparison.label), styles["caption_bold"]))
                        continue
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
                            "<b>Recorded instructor judgment:</b> "
                            + escape(detail.override_reason),
                            styles["body"],
                        )
                    )
                page_image = data.page_images.get(f.id) if data.source_evidence is None else None
                if data.source_evidence is None and (
                    page_image is None or page_image.reason_code != "docx_layout"
                ):
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
                                    f"{record_identity} (continued)"
                                    if data.source_evidence is not None
                                    else f"{record_identity} (continued) - "
                                    f"Source location: {_display_anchor(f.page_anchor)}"
                                ),
                                styles["caption_bold"],
                            )
                        ],
                    )
                )
                flow.append(Spacer(1, 8))
    flow.append(Spacer(1, 4))
    if not scope_emitted:
        flow.extend(_originality_scope_flowables(data, styles))

    # 5. Recorded judgments, including initial review of settled criteria.
    resolved = [r for r in report.results if r.resolution]
    if resolved:
        flow.append(Paragraph("Instructor Judgments", styles["h2"]))
        for row in resolved:
            flow.append(
                PTOContainer(
                    [
                        *_criterion_flowable(
                            row, styles, include_evidence=data.source_evidence is None
                        ),
                        *(
                            [
                                Paragraph(
                                    "Recorded source passages from the system assessment",
                                    styles["body_bold"],
                                )
                            ]
                            if criterion_sources(row)
                            else []
                        ),
                        *source_blocks(criterion_sources(row)),
                    ],
                    header=[
                        Paragraph(
                            escape(f"Criterion #{row.criterion_id} (continued)"),
                            styles["caption_bold"],
                        )
                    ],
                )
            )
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

        def criteria_table(rows):
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
            return table

        # Match SimpleDocTemplate's actual default Frame padding. Only an
        # individually oversized record leaves the compact table layout.
        print_frame = Frame(0, 0, doc.width, doc.height)
        rows = [header]
        for row in remaining:
            label, tone = _result_display(row)
            entries = criterion_sources(row)
            if entries:
                if len(rows) > 1:
                    flow.append(criteria_table(rows))
                    rows = [header]
                identity = Paragraph(
                    escape(
                        f"Criterion #{row.criterion_id}: {label}. "
                        f"{_format_weight_importance(row.weight_importance, long=True)}."
                    ),
                    styles["body_bold"],
                )
                identity.keepWithNext = True
                flow.append(
                    PTOContainer(
                        [
                            identity,
                            *_criterion_flowable(row, styles, include_evidence=False),
                            *source_blocks(entries),
                        ],
                        header=[
                            Paragraph(
                                escape(f"Criterion #{row.criterion_id} (continued)"),
                                styles["caption_bold"],
                            )
                        ],
                    )
                )
                flow.append(Spacer(1, _PRINT_LAYOUT["source_entry_gap"]))
                continue
            result_style = styles["cell_bold"] if tone in ("danger",) else styles["cell"]
            importance_ink = {"high": _INK, "med": _INK_SECONDARY, "low": _INK_TERTIARY}.get(
                row.weight_importance, _INK_SECONDARY
            )
            importance_style = ParagraphStyle(
                "importance_cell",
                parent=styles["cell_bold"] if row.weight_importance == "high" else styles["cell"],
                textColor=importance_ink,
            )
            candidate = [
                Paragraph(_format_weight_importance(row.weight_importance), importance_style),
                _criterion_flowable(row, styles),
                Paragraph(escape(label), result_style),
            ]
            measured = criteria_table([header, candidate]).wrap(print_frame._aW, print_frame._aH)[1]
            if measured <= print_frame._aH:
                rows.append(candidate)
                continue
            if len(rows) > 1:
                flow.append(criteria_table(rows))
                rows = [header]
            identity = Paragraph(
                escape(
                    f"Criterion #{row.criterion_id}: {label}. "
                    f"{_format_weight_importance(row.weight_importance, long=True)}."
                ),
                styles["body_bold"],
            )
            identity.keepWithNext = True
            parts = _criterion_flowable(row, styles)
            parts[0].keepWithNext = True
            flow.append(
                PTOContainer(
                    [identity, *parts],
                    header=[
                        Paragraph(
                            escape(f"Criterion #{row.criterion_id} (continued)"),
                            styles["caption_bold"],
                        )
                    ],
                )
            )
            flow.append(Spacer(1, 8))
        if len(rows) > 1:
            flow.append(criteria_table(rows))
    else:
        flow.append(Paragraph("No additional criteria to show.", styles["body"]))
    flow.append(Spacer(1, 12))

    # 7. Final decision.
    flow.append(Paragraph('<a name="instructor-decision-record"/>Final Decision', styles["h2"]))
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
            note_table = Table(
                [
                    [Paragraph("Decision reason", styles["body_bold"])],
                    [Paragraph(escape(report.decision_note), styles["body"])],
                ],
                repeatRows=1,
                splitInRow=1,
            )
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
            draw_right_text(canvas, truncated, font, 8, A4[0] - side_margin, A4[1] - 40)
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
