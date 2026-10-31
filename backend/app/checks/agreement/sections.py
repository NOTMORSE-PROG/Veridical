"""Keep only the thesis's own results and objectives as F4 candidates (V-094,
BUG-603, BUG-625).

A sentence that opens "Results showed..." is not always the thesis's own
result: it can summarise another paper in the literature review, report a
needs survey run before development, or sit in an author biography. Pairing
an objective with such a sentence produced findings that two independent
raters judged wrong. So a candidate result is set aside when its chapter or
its nearest heading has a role that is not this study's results, using the
shared section map (`app.checks.section_roles`).

Objectives get the same treatment (BUG-625): an "aim" sentence in the
literature review is another paper's aim, and one in the methodology or
results chapter is usually a step in how the system works. Only the chapter
decides for objectives, not the nearest heading, because a thesis states its
goal under "Background of the Study", a heading the role map reads as
literature.

The rule fails open: when no chapter of the document can be recognised,
every candidate is kept and the run records that the section was not
identified.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.checks.agreement.extract import Statement
from app.checks.section_roles import SectionMap

NOT_IDENTIFIED_NOTE = "results section not identified, all sections used"
OBJECTIVES_NOT_IDENTIFIED_NOTE = "chapters not identified, objectives from all sections used"
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


# Chapters whose "aims" are not this study's objectives. The introduction, the
# abstract and unrecognised chapters ("3 AirSpecs ...", which state that
# chapter's study objectives) are kept.
NOT_OWN_OBJECTIVES = frozenset(
    {
        "table_of_contents",
        "front_matter",
        "biography",
        "references",
        "appendix",
        "literature",
        "methodology",
        "results",
        "conclusion",
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


@dataclass(frozen=True)
class ObjectiveGate:
    kept: list[Statement]
    set_aside: list[dict] = field(default_factory=list)
    identified: bool = True

    def detail(self) -> dict:
        detail = {
            "objectives_section_identified": self.identified,
            "n_objectives_kept": len(self.kept),
            "n_objectives_set_aside": len(self.set_aside),
            "set_aside": self.set_aside,
        }
        if not self.identified:
            detail["note"] = OBJECTIVES_NOT_IDENTIFIED_NOTE
        return detail


def gate_objectives(intents: list[Statement], sections: SectionMap) -> ObjectiveGate:
    if not sections.identified:
        return ObjectiveGate(kept=list(intents), identified=False)
    kept, set_aside = [], []
    for intent in intents:
        where = sections.locate(intent.text, intent.anchor, intent.page)
        if where.chapter_role in NOT_OWN_OBJECTIVES:
            set_aside.append(
                {
                    "text": intent.text,
                    "anchor": intent.anchor,
                    "chapter": where.chapter,
                    "chapter_role": where.chapter_role,
                }
            )
        else:
            kept.append(intent)
    return ObjectiveGate(kept=kept, set_aside=set_aside)
