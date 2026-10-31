"""Keep only the thesis's own results as F4 result candidates (V-094, BUG-603).

A sentence that opens "Results showed..." is not always the thesis's own
result: it can summarise another paper in the literature review, report a
needs survey run before development, or sit in an author biography. Pairing
an objective with such a sentence produced findings that two independent
raters judged wrong. So a candidate result is set aside when its chapter or
its nearest heading has a role that is not this study's results, using the
shared section map (`app.checks.section_roles`).

The rule fails open: when no chapter of the document can be recognised,
every candidate is kept and the run records that the results section was not
identified.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.checks.agreement.extract import Statement
from app.checks.section_roles import SectionMap

NOT_IDENTIFIED_NOTE = "results section not identified, all sections used"
# Roles whose text is not this study's own results. Results, conclusion,
# abstract and unrecognised chapters ("3 AirSpecs ...") are kept.
NOT_OWN_RESULTS = frozenset(
    {
        "table_of_contents",
        "front_matter",
        "biography",
        "references",
        "appendix",
        "introduction",
        "literature",
        "methodology",
        "future_work",
    }
)


@dataclass(frozen=True)
class SectionGate:
    kept: list[Statement]
    set_aside: list[dict] = field(default_factory=list)
    identified: bool = True
    source: str = "section_tree"
    # The chapter title of each kept result, in the same order; a result in
    # the "3 AirSpecs ..." chapter is about AirSpecs even when the sentence
    # does not say so.
    kept_chapters: list[str | None] = field(default_factory=list)

    def detail(self) -> dict:
        detail = {
            "results_section_identified": self.identified,
            "chapter_source": self.source,
            "n_results_kept": len(self.kept),
            "n_results_set_aside": len(self.set_aside),
            "set_aside": self.set_aside,
        }
        if not self.identified:
            detail["note"] = NOT_IDENTIFIED_NOTE
        return detail


def gate_results(outcomes: list[Statement], sections: SectionMap) -> SectionGate:
    if not sections.identified:
        return SectionGate(
            kept=list(outcomes),
            identified=False,
            source="none",
            kept_chapters=[None] * len(outcomes),
        )
    kept, set_aside, kept_chapters = [], [], []
    for outcome in outcomes:
        where = sections.locate(outcome.text, outcome.anchor, outcome.page)
        if {where.chapter_role, where.heading_role} & NOT_OWN_RESULTS:
            set_aside.append(
                {
                    "text": outcome.text,
                    "anchor": outcome.anchor,
                    "chapter": where.chapter,
                    "chapter_role": where.chapter_role,
                    "heading": where.heading,
                    "heading_role": where.heading_role,
                }
            )
        else:
            kept.append(outcome)
            kept_chapters.append(where.chapter)
    return SectionGate(
        kept=kept,
        set_aside=set_aside,
        identified=True,
        source=sections.source,
        kept_chapters=kept_chapters,
    )
