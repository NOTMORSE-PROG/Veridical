"""Known-answer case sets for F4 internal agreement: objectives paired with
planted results statements.

Built from the stored extractions of the permitted theses:

* recall: a results statement that plainly reports a real objective as not
  achieved, with nothing around it that explains the gap. Label
  `contradictory`.
* specificity: the objective restated as achieved. Label `not_contradictory`.
  These are the false-alarm controls.
* context-blind probe: a negative result with reconciling context placed
  where theses put it (a stated scope or phase, an acknowledged limitation).
  The judge is given only the objective and one result sentence, never the
  context, so these measure how often a reconciled gap is still flagged.
  They are reported on their own and never pooled with specificity.

Plants go into the stored extraction, not the PDF, so pairing and the
judgement step see them in place. Every copy carries at most three plants and
all inserted blocks sit five or more pages apart. The sets are templated, so
they are known-answer/seeded cases and not instructor-labelled accuracy.

    uv run --project backend python -m tools.accuracy.f4_cases \
        --golden context/golden/accuracy --seed 20261031
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from tools.accuracy.core import (  # noqa: E402
    Context,
    body_pages,
    git_commit,
    load_extraction,
)
from tools.accuracy.levels import balance_problems, baseline_accuracy  # noqa: E402

from app.checks.agreement.cues import load_cues  # noqa: E402
from app.checks.agreement.extract import extract_statements  # noqa: E402
from app.checks.agreement.sections import NOT_OWN_RESULTS  # noqa: E402
from app.checks.section_roles import SectionMap, load_section_roles  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.ingest.schemas import ExtractionResult, TextBlock  # noqa: E402

LANE = "f4"
HONESTY = (
    "Known-answer/seeded cases, NOT instructor-labelled. Plants follow templates, so real-world "
    "prevalence and difficulty differ. This does not close G39."
)
PLANTS_PER_COPY = 3
PAGE_GAP = 5
RECALL_TARGET = 120
PROBE_PER_KIND = 36
RESTATEMENT_TARGET = 60
MIN_HOST_WORDS = 8
MIN_CORE_WORDS = 5
MAX_CORE_WORDS = 16
PAGE_ATTEMPTS = 60

# Where on the body-page range each inserted block goes (fractions of the
# sorted body pages). Scope and method text sits in the Chapter 1 to 3 area,
# results in the later half, limitations and conclusions near the end.
REGIONS = {
    "recall_result": (0.50, 1.00),
    "scope_context": (0.00, 0.40),
    "scope_result": (0.50, 0.90),
    "limitation_result": (0.50, 0.78),
    "limitation_context": (0.80, 1.00),
    "restatement_result": (0.85, 1.00),
}

# Hosts differ in voice: a capstone team versus a single author.
VOICE = {
    "plural": {"subjects": ("the proponents", "the researchers", "the team"), "was": "were"},
    "singular": {"subjects": ("the author", "the researcher"), "was": "was"},
}
HOST_VOICE = {"augmentedrl": "singular", "mit": "singular"}

# Hand calls on sentences the cue rules accept that are still not this study's
# own objective. (host, start of the sentence, reason)
MANUAL_SKIPS = (
    (
        "destamp",
        "The objective is to maximize",
        (
            "problem formulation of a cited study in the related-work chapter, not this study's "
            "objective"
        ),
    ),
    (
        "mit",
        "My aim is to establish",
        "chapter-level aim of the literature review, not a testable objective",
    ),
    (
        "mit",
        "To engineer and characterize a versatile",
        (
            "the same CollarID objective as the chapter-five statement, and its quoted phrase "
            "came from a different sentence"
        ),
    ),
    (
        "mit",
        "The overarching goal is to demonstrate",
        "overall thesis goal cut mid-clause, not a testable objective",
    ),
    (
        "mit",
        "A second objective was to deploy these units",
        "the quoted phrase ends mid-noun-phrase ('to collect a unique')",
    ),
    (
        "bimby",
        "To develop a module that provides complete information",
        "the quoted phrase is cut inside its own list of features",
    ),
)

KIND_RECALL = "objective_not_achieved"
KIND_SCOPE = "scope_or_phase"
KIND_LIMITATION = "acknowledged_limitation"
KIND_RESTATE = "restatement_control"
PROBE_KINDS = (KIND_SCOPE, KIND_LIMITATION)
LABELS = ("contradictory", "not_contradictory")
KIND_CODE = {
    KIND_RECALL: "recall",
    KIND_SCOPE: "scope",
    KIND_LIMITATION: "limit",
    KIND_RESTATE: "restate",
}
# Two kinds from the first draft were dropped after the template review, not
# tuned: a revised objective reused the recall sentences, so the judge, which
# never sees the revision, could only score it as one minus recall; and the
# population/measure results shared almost no words with their objective, so
# they mostly measured pairing, and several named groups or measures that did
# not fit the host.

# --- templates ---------------------------------------------------------------
# {x} is the objective's own substance (verb phrase), {S} the subject, {was} the
# matching "was/were". Result sentences open with phrases F4's cue file treats
# as results, and avoid its future-work and scope-negation guard phrases.

RECALL_FRAMES = {
    "R01": "Results showed that {S} did not {x}, and this objective was not met.",
    "R02": "{Sc} {was} not able to {x}, and the corresponding objective was left unmet.",
    "R03": (
        "The study found that {S} could not {x} within the project period, so the objective was not"
        " achieved."
    ),
    "R04": "It was found that {S} failed to {x}; the objective was therefore not accomplished.",
    "R05": "Results revealed that the objective to {x} was not met.",
    "R06": "Based on the findings, {S} did not {x}, and the objective was not fulfilled.",
    "R07": "The study revealed that the project was unable to {x}, leaving the objective unmet.",
    "R08": "The study found that the work to {x} was not completed, so the objective was not met.",
    "R09": "Results indicated that {S} {was} unable to {x}, and the objective was not reached.",
    "R10": "Results revealed no sign that {S} could {x}, so the objective was not achieved.",
}

SCOPE_CONTEXT = {
    "C1": (
        "Scope and Delimitation. This study covers only {scope}. Later phases of the project were "
        "outside the evaluation reported here."
    ),
    "C3": (
        "For the purposes of this capstone, testing was delimited to {scope}, and the full system "
        "is expected to be evaluated in a separate phase."
    ),
    "C5": (
        "Data gathering was restricted to {scope} because the complete system was still under "
        "development."
    ),
}
SCOPE_RESULT = {
    "R1": "Results showed that {S} {was} not yet able to {x} in {scope}.",
    "R2": (
        "The study found that {S} did not yet {x} in {scope}, so the objective was not met there."
    ),
}
SCOPES = (
    "the pilot deployment with one class section",
    "the first development phase",
    "the initial prototype release",
    "the single-campus trial",
    "the pilot group of {n} respondents",
    "the Phase 1 build",
    "the two-week field trial in one partner office",
)

LIMITATION_CONTEXT = {
    "C1": (
        "Limitations. {part_c} was not completed within the project period because of {reason}. "
        "The item is left for later development."
    ),
    "C2": (
        "One limitation of this study is that {part} could not be finished due to {reason}; this "
        "part is left for further work by other teams."
    ),
    "C3": (
        "It is acknowledged that {part} was not completed ({reason}), and this remains an open item"
        " for further development."
    ),
    "C4": (
        "Limitations of the Study. Because of {reason}, {part} was only partly done, and it is "
        "recommended that it be taken up in later development."
    ),
}
LIMITATION_RESULT = {
    "R2": (
        "The study found that {part} was left unfinished, so the objective to {x} was only partly "
        "carried out."
    ),
    "R3": "It was found that {S} did not complete {part} while working to {x}.",
    "R4": "Based on the findings, {S} did not finish {part} of the effort to {x}.",
    "R5": (
        "Results revealed that {S} failed to finish {part}, which was one piece of the objective to"
        " {x}."
    ),
}
PARTS = (
    "the final integration step",
    "the second round of user testing",
    "the offline mode",
    "the full data-collection phase",
    "the load-testing step",
    "the validation with the remaining respondents",
)
REASONS = (
    "limited time",
    "delays in obtaining the partner office's data",
    "hardware delivery delays",
    "the small number of available respondents",
    "device compatibility problems",
    "scheduling conflicts with the partner office",
)

RESTATE_FRAMES = {
    "K1": "Based on the findings, {S} {was} able to {x}, which fulfilled this objective.",
    "K2": "The study concluded that {S} successfully carried out the work to {x}.",
    "K3": (
        "Results showed that {S} {was} able to {x}, in line with the objective set at the start of "
        "the study."
    ),
    "K4": "The study found that the objective to {x} was achieved.",
    "K5": "It was found that {S} {was} able to {x}, and this objective was met.",
    "K6": "Results revealed that {S} {was} able to {x}.",
}

# Lead-verb swaps so a planted sentence does not copy the objective word for
# word. Overlap with the objective stays high on purpose; that is a known
# limitation of this set.
VERB_SWAPS = {
    "develop": "build",
    "evaluate": "assess",
    "identify": "pinpoint",
    "understand": "determine",
    "define": "specify",
    "implement": "apply",
    "collect": "gather",
    "gather": "collect",
    "investigate": "examine",
    "engineer": "build",
    "explore": "examine",
    "adapt": "modify",
    "integrate": "incorporate",
    "conduct": "carry out",
    "demonstrate": "show",
    "deploy": "roll out",
    "determine": "establish",
    "train": "fit",
    "validate": "confirm",
    "prioritize": "emphasize",
    "measure": "quantify",
    "create": "produce",
}

# Fixed before any model run so the rule cannot follow the results.
SCORING_RULE = (
    "F4 answers each judged pair with consistent, contradictory, partial or cannot_determine. "
    "A plant is 'reached' only when the local pairing step gives its objective the planted "
    "sentence; otherwise the judge never sees it. "
    "Recall (primary): reached and judged contradictory, the high-severity flag. Recall "
    "(secondary): reached and given any flag (contradictory, partial or cannot_determine). "
    "A plant that was not reached is a recall miss, and pairing reach is reported on its own. "
    "Specificity, on restatement controls only (primary): reached and NOT judged "
    "contradictory, since a high-severity contradiction against a correct result is the false "
    "accusation that matters most. Specificity (secondary): reached and judged consistent, "
    "with no flag of any kind. Controls not reached are counted, not scored. "
    "Context-blind probe (scope_or_phase, acknowledged_limitation): the same two rates per "
    "kind, never pooled with specificity. Expected in advance: the judge sees only the "
    "objective and the negative result sentence, never the reconciling context, so most of "
    "these are expected to draw partial or contradictory; the probe measures that cost. "
    "No pooled accuracy is given; the rate of always giving one label is stated beside every "
    "figure. Each rate has a Wilson interval and a bootstrap interval clustered by objective. "
    "Nothing is tuned between the dev and holdout halves in this baseline."
)

# What a reader might call a contradiction, kept as data so templates.md and the
# manifest cannot drift apart.
BORDERLINE = {
    KIND_SCOPE: (
        "A reader can say the objective is still unmet inside the pilot, so the result is "
        "negative about the objective even with the scope sentence. The label rests on reading "
        "the objective as covering the whole system and the result as a statement about a "
        "stated subset. Only the 'not yet' frames R1 and R2 are kept, each with a context that "
        "defers the rest to a later phase (C1, C3, C5). Expect disagreement on this kind; it "
        "is a probe, not a specificity figure."
    ),
    KIND_LIMITATION: (
        "The result reports part of the objective as not done, which is true, and the "
        "limitations sentence agrees with it. Whether that counts as a contradiction is a "
        "matter of framing: the objective is partly unmet, and the document says so. Frame R2 "
        "('only partly carried out') is the clearest. 'partial' is the answer the judge's own "
        "instructions ask for here, so the primary rate (not judged contradictory) is the one to "
        "read for this kind."
    ),
    KIND_RESTATE: (
        "Clean control: the objective restated as achieved. The only risk is that it is "
        "word-for-word close to the objective, so it also tests lexical-overlap shortcuts."
    ),
    KIND_RECALL: (
        "Clear by construction. The sentence reuses the objective's own verb phrase (verb "
        "swaps only), so lexical overlap is high and this set is easier than real "
        "contradictions, which rarely repeat the objective's wording."
    ),
}

_WORD = re.compile(r"[A-Za-z]+")
_TERMINAL = re.compile(r"[.!?][\"')\]]*$")
_CONSTRUCTION = re.compile(r"\b(?:is|are|was|were)\s+to\s+", re.IGNORECASE)
_CUTS = re.compile(
    r"(?:\.\s|;|\s\(|\s—|\s�|\sthereby\b|,\s*enabling\b|,\s*ensuring\b|\sensuring\b|"
    r",\s*which\b|\swhich\b|,\s*while\b|\swhile\b|,?\s+and\s+to\s|,\s*so\s+that\b|\sso\s+as\s+to\b|"
    r"\scapable\s+of\b|\swith\s+dataset)",
    re.IGNORECASE,
)
_TRAILING = {
    "and",
    "or",
    "the",
    "of",
    "for",
    "to",
    "with",
    "in",
    "a",
    "an",
    "that",
    "from",
    "by",
    "as",
    "on",
    "into",
    "at",
    "between",
    "using",
    "through",
    "across",
    "its",
    "their",
    "be",
    "is",
    "are",
}
_PRONOUN_START = re.compile(r"^(?:it|its|his|her|their)\b", re.IGNORECASE)


# --- objectives ---------------------------------------------------------------


def _fix_hyphens(text: str, vocab: Counter) -> str:
    def join(match: re.Match) -> str:
        left, right = match.group(1), match.group(2)
        return (left + right) if vocab[(left + right).lower()] else f"{left}-{right}"

    return re.sub(r"([A-Za-z]+)- ([A-Za-z]+)", join, text)


def objective_core(text: str, vocab: Counter) -> str | None:
    """The verb phrase an objective asks for ("design, develop, and test X"),
    cut at the first descriptive clause so it reads naturally after "to"."""
    text = re.sub(r"�s\b", "'s", text.strip())
    construction = _CONSTRUCTION.search(text)
    core = text[construction.end() :] if construction else re.sub(r"^to\s+", "", text, flags=re.I)
    core = _fix_hyphens(core, vocab)
    cut = _CUTS.search(core)
    if cut:
        core = core[: cut.start()]
    quoted = re.search(r"[”\"],\s+and\s", core)
    if quoted:
        core = core[: quoted.start() + 1]
    words = core.replace("�", "").split()
    if len(words) > MAX_CORE_WORDS:
        # Cut at the last comma that leaves a whole phrase; otherwise the objective is
        # too long to quote without chopping it mid-phrase, so it is not used.
        commas = [
            i + 1
            for i, w in enumerate(words[:MAX_CORE_WORDS])
            if w.endswith(",") and i + 1 >= MIN_CORE_WORDS
        ]
        if not commas:
            return None
        words = words[: commas[-1]]
    while words and (words[-1].rstrip(",").lower() in _TRAILING):
        words.pop()
    core = " ".join(words).rstrip(",;:.!?")
    if len(core.split()) < MIN_CORE_WORDS or not core[:1].isalpha() or core[0].isupper():
        return None
    return core


def swap_verbs(core: str) -> str:
    """Replace the leading verb list ("design, develop, and test") word by word
    where a plain synonym exists."""
    lead = re.match(r"^((?:\w+, )*\w+(?:,? and \w+)?)", core)
    if not lead:
        return core
    swapped = re.sub(
        r"\w+", lambda m: VERB_SWAPS.get(m.group(0).lower(), m.group(0)), lead.group(0)
    )
    return swapped + core[lead.end() :]


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text) if len(w) > 3}


def select_objectives(name: str, extraction: ExtractionResult, settings) -> tuple[list, list]:
    """Real objectives among the statements F4 extracts, and the ones skipped
    with the reason."""
    intents = extract_statements(extraction, settings=settings).intents
    vocab = Counter(w.lower() for b in extraction.blocks for w in _WORD.findall(b.text))
    usable, skipped, kept_tokens = [], [], []
    for index, statement in enumerate(intents):
        record = {
            "index": index,
            "text": statement.text,
            "anchor": statement.anchor,
            "cue": statement.cue,
        }
        reason = None
        manual = next(
            (r for h, start, r in MANUAL_SKIPS if h == name and statement.text.startswith(start)),
            None,
        )
        is_list = statement.cue == "intent_list"
        is_construction = statement.cue.startswith("\\b(?:objective")
        if manual:
            reason = manual
        elif not (is_list or is_construction):
            reason = (
                "modal-cue sentence about a feature, design choice or quoted source, "
                "not a stated study objective"
            )
        elif _PRONOUN_START.match(statement.text) or re.search(
            r"\b(?:his|her)\b", statement.text, re.I
        ):
            reason = "begins with a pronoun or is biographical, no standalone objective"
        else:
            core = objective_core(statement.text, vocab)
            if core is None:
                reason = "could not isolate a verb phrase of usable length"
            elif re.search(r"\b(?:we|our|my|I)\b", core):
                reason = "first-person phrasing does not carry into a third-person results sentence"
            else:
                tokens = _tokens(core)
                twin = next(
                    (
                        k
                        for k, other in enumerate(kept_tokens)
                        if len(tokens & other) / max(1, len(tokens | other)) >= 0.5
                    ),
                    None,
                )
                if twin is not None:
                    reason = f"restates an objective already used (index {usable[twin]['index']})"
                else:
                    kept_tokens.append(tokens)
                    record["core"] = core
        if reason:
            record["reason"] = reason
            skipped.append(record)
        else:
            usable.append(record)
    return usable, skipped


# --- insertion ----------------------------------------------------------------


def _eligible(blocks: list[TextBlock], index: int) -> bool:
    block = blocks[index]
    text = block.text.strip()
    if block.is_furniture or block.page is None or not _TERMINAL.search(text):
        return False
    if len(text.split()) < MIN_HOST_WORDS:
        return False
    after = blocks[index + 1] if index + 1 < len(blocks) else None
    return not (after and after.page == block.page and after.text[:1].islower())


def insertion_index(blocks: list[TextBlock], page: int) -> int | None:
    """Index of the last block on the page that ends a sentence cleanly; the
    planted block goes right after it."""
    found = None
    for index, block in enumerate(blocks):
        if block.page == page and _eligible(blocks, index):
            found = index
    return found


def eligible_pages(extraction: ExtractionResult) -> list[int]:
    return [p for p in body_pages(extraction) if insertion_index(extraction.blocks, p) is not None]


def section_title(extraction: ExtractionResult, page: int) -> str | None:
    flat, stack = [], list(extraction.section_tree.nodes)
    while stack:
        node = stack.pop(0)
        stack = list(node.children) + stack
        if node.page is not None:
            flat.append(node)
    before = [n for n in flat if n.page <= page]
    return max(before, key=lambda n: n.page).title if before else None


def apply_case(extraction: ExtractionResult, copy: dict) -> ExtractionResult:
    """A planted copy of `extraction`: every inserted block of every plant in
    `copy`, each directly after the last clean sentence on its page."""
    planted = extraction.model_copy(deep=True)
    for plant in copy["plants"]:
        for block in plant["blocks"]:
            index = insertion_index(planted.blocks, block["page"])
            if index is None:
                raise ValueError(f"No place to plant on page {block['page']} ({plant['plant_id']})")
            host = planted.blocks[index]
            planted.blocks.insert(
                index + 1,
                TextBlock(
                    page=block["page"],
                    bbox=host.bbox,
                    text=block["text"],
                    max_font_size=host.max_font_size,
                    bold_ratio=0.0,
                ),
            )
    return planted


# --- building -----------------------------------------------------------------

_host_cache: dict[Path, dict] = {}


def results_pages(extraction: ExtractionResult, pages: list[int]) -> list[int]:
    """Pages where the thesis reports its own results, by the product's own
    section rule: results chapters when the document has them, otherwise any
    page outside a non-results chapter. A planted result placed in a
    literature review would test where it was put, not the check."""
    settings = get_settings()
    sections = SectionMap(
        extraction,
        load_section_roles(settings.section_roles_file),
        heading_max_words=settings.agreement_heading_max_words,
        locate_chars=settings.agreement_context_locate_chars,
    )
    roles = sections.page_roles(pages)
    results = [p for p in pages if roles.get(p) == "results"]
    return results or [p for p in pages if roles.get(p) not in NOT_OWN_RESULTS]


def _host_data(path: Path) -> dict:
    if path not in _host_cache:
        extraction = load_extraction(path)
        usable, skipped = select_objectives(path.stem, extraction, get_settings())
        pages = eligible_pages(extraction)
        _host_cache[path] = {
            "extraction": extraction,
            "usable": usable,
            "skipped": skipped,
            "pages": pages,
            "result_pages": results_pages(extraction, pages),
        }
    return _host_cache[path]


class _Deck:
    """Shuffled frame ids dealt out evenly, avoiding a repeat for one objective
    where the deck allows."""

    def __init__(self, rng: random.Random, ids):
        self.rng, self.ids, self.cards = rng, list(ids), []

    def deal(self, seen: set) -> str:
        for _ in range(len(self.ids) * 2):
            if not self.cards:
                self.cards = self.rng.sample(self.ids, len(self.ids))
            card = self.cards.pop()
            if card not in seen:
                return card
        return card


def _fill(template: str, slots: dict) -> str:
    full = dict(slots)
    for key, value in slots.items():
        if isinstance(value, str) and value:
            full[f"{key}_c"] = value[0].upper() + value[1:]
    return template.format(**full)


def _guard_ok(text: str, cues) -> bool:
    lowered = text.lower()
    return not any(
        p in lowered for p in (*cues.future_work_guard_phrases, *cues.scope_negation_phrases)
    )


def _spec(kind, label, host, obj, rng, decks, seen, slots_for, ctx_table, res_table, cues):
    """One plant's blocks, rendered with its own seeded slot choices."""
    for _ in range(12):
        ctx_id = (
            decks[(kind, "ctx")].deal(seen[(kind, "ctx", obj["index"], host)])
            if ctx_table
            else None
        )
        res_id = decks[(kind, "res")].deal(seen[(kind, "res", obj["index"], host)])
        slots = slots_for(rng)
        blocks = []
        if ctx_table:
            blocks.append(("context", ctx_id, _fill(ctx_table[ctx_id], slots)))
        blocks.append(("result", res_id, _fill(res_table[res_id], slots)))
        if all(_guard_ok(text, cues) for role, _fid, text in blocks if role == "result"):
            if ctx_table:
                seen[(kind, "ctx", obj["index"], host)].add(ctx_id)
            seen[(kind, "res", obj["index"], host)].add(res_id)
            return blocks, ctx_id, res_id
    return None


def _all_specs(ctx: Context, rng: random.Random, hosts: dict) -> tuple[list, list]:
    cues = load_cues(get_settings().agreement_cues_file)
    pool = [(h, o) for h in sorted(hosts) for o in hosts[h]["usable"]]
    rng.shuffle(pool)
    decks, seen = {}, defaultdict(set)
    for kind, ctx_table, res_table in (
        (KIND_RECALL, None, RECALL_FRAMES),
        (KIND_SCOPE, SCOPE_CONTEXT, SCOPE_RESULT),
        (KIND_LIMITATION, LIMITATION_CONTEXT, LIMITATION_RESULT),
        (KIND_RESTATE, None, RESTATE_FRAMES),
    ):
        decks[(kind, "res")] = _Deck(rng, res_table)
        if ctx_table:
            decks[(kind, "ctx")] = _Deck(rng, ctx_table)

    def pieces(kind, host, obj, index):
        rng_case = random.Random(f"{ctx.seed}-{kind}-{host}-{obj['index']}-{index}")
        voice = VOICE[HOST_VOICE.get(host, "plural")]
        # Every kind gets the same verb swaps, so a recall/specificity gap
        # cannot come from one side copying the objective's wording more closely.
        slots = {
            "S": rng_case.choice(voice["subjects"]),
            "was": voice["was"],
            "x": swap_verbs(obj["core"]),
        }
        slots["Sc"] = slots["S"][0].upper() + slots["S"][1:]
        return rng_case, slots

    specs, dropped = [], []
    cursor = [0]

    def add(kind, label, n_target):
        count, tries = 0, 0
        while count < n_target and tries < n_target * 6:
            host, obj = pool[(cursor[0] + tries) % len(pool)]
            tries += 1
            rng_case, slots = pieces(kind, host, obj, tries)
            ctx_table, res_table = None, None
            if kind == KIND_RECALL:
                res_table = RECALL_FRAMES
            elif kind == KIND_RESTATE:
                res_table = RESTATE_FRAMES
            elif kind == KIND_SCOPE:
                ctx_table, res_table = SCOPE_CONTEXT, SCOPE_RESULT
                slots["scope"] = rng_case.choice(SCOPES).format(n=rng_case.randint(12, 30))
            elif kind == KIND_LIMITATION:
                ctx_table, res_table = LIMITATION_CONTEXT, LIMITATION_RESULT
                slots["part"], slots["reason"] = rng_case.choice(PARTS), rng_case.choice(REASONS)
            built = _spec(
                kind,
                label,
                host,
                obj,
                rng_case,
                decks,
                seen,
                lambda _r, s=slots: s,
                ctx_table,
                res_table,
                cues,
            )
            if built is None:
                dropped.append({"host": host, "objective": obj["index"], "kind": kind})
                continue
            blocks, ctx_id, res_id = built
            code = KIND_CODE[kind]
            template_id = f"{code}:{ctx_id}+{res_id}" if ctx_id else f"{code}:{res_id}"
            region_prefix = {
                KIND_RECALL: "recall",
                KIND_SCOPE: "scope",
                KIND_LIMITATION: "limitation",
                KIND_RESTATE: "restatement",
            }[kind]
            specs.append(
                {
                    "host": host,
                    "kind": kind,
                    "label": label,
                    "objective": obj,
                    "template_id": template_id,
                    "blocks": [
                        {
                            "role": role,
                            "frame_id": frame_id,
                            "text": text,
                            "region": f"{region_prefix}_{role}",
                        }
                        for role, frame_id, text in blocks
                    ],
                }
            )
            count += 1
        cursor[0] += tries
        return count

    add(KIND_RECALL, "contradictory", RECALL_TARGET)
    for kind in PROBE_KINDS:
        add(kind, "not_contradictory", PROBE_PER_KIND)
    add(KIND_RESTATE, "not_contradictory", RESTATEMENT_TARGET)
    return specs, dropped


def _region_pages(pages: list[int], region: str) -> list[int]:
    low, high = REGIONS[region]
    start = int(len(pages) * low)
    return pages[start : max(int(len(pages) * high), start + 1)]


def _assign_pages(rng, spec, taken, pages, result_pages) -> list[int] | None:
    """Pages for a plant's blocks: the result on one of the thesis's own results
    pages, five or more pages from everything already in the copy, context
    before the result except limitations, which come after."""
    blocks = spec["blocks"]
    context = next((b for b in blocks if b["role"] == "context"), None)

    def free(candidates, others):
        return [p for p in candidates if all(abs(p - t) >= PAGE_GAP for t in (*taken, *others))]

    for _ in range(PAGE_ATTEMPTS):
        options = free(result_pages, ())
        if not options:
            return None
        result_page = rng.choice(options)
        if context is None:
            return [result_page]
        options = free(_region_pages(pages, context["region"]), (result_page,))
        if spec["kind"] == KIND_LIMITATION:
            options = [p for p in options if p > result_page]
        else:
            options = [p for p in options if p < result_page]
        if options:
            context_page = rng.choice(options)
            return (
                [context_page, result_page] if blocks[0] is context else [result_page, context_page]
            )
    return None


def build_cases(ctx: Context) -> list[dict]:
    """Planted copies, each with up to three plants. Deterministic for a seed."""
    rng = random.Random(ctx.seed)
    hosts = {name: _host_data(path) for name, path in ctx.hosts().items()}
    specs, dropped = _all_specs(ctx, rng, hosts)
    by_host = defaultdict(list)
    for spec in specs:
        by_host[spec["host"]].append(spec)

    copies, counter = [], Counter()
    for host in sorted(by_host):
        pending = by_host[host]
        rng.shuffle(pending)
        pages = hosts[host]["pages"]
        extraction = hosts[host]["extraction"]
        while pending:
            plants, taken, used_objectives, placed = [], [], set(), []
            for spec in list(pending):
                if len(plants) == PLANTS_PER_COPY:
                    break
                if spec["objective"]["index"] in used_objectives:
                    continue
                assigned = _assign_pages(rng, spec, taken, pages, hosts[host]["result_pages"])
                if assigned is None:
                    continue
                placed.append((spec, assigned))
                taken.extend(assigned)
                used_objectives.add(spec["objective"]["index"])
                plants.append(spec)
                pending.remove(spec)
            if not plants:
                dropped.append({"host": host, "reason": "no page placement", "n": len(pending)})
                break
            counter[host] += 1
            copy_id = f"{host}-{counter[host]:03d}"
            records = []
            for spec, pages_for in placed:
                counter["plant"] += 1
                records.append(
                    {
                        "plant_id": f"f4-{KIND_CODE[spec['kind']]}-{counter['plant']:04d}",
                        "host": host,
                        "copy_id": copy_id,
                        "kind": spec["kind"],
                        "label": spec["label"],
                        "split": None,
                        "scored_as": (
                            "recall"
                            if spec["kind"] == KIND_RECALL
                            else "probe"
                            if spec["kind"] in PROBE_KINDS
                            else "specificity"
                        ),
                        "objective_index": spec["objective"]["index"],
                        "intent_text": spec["objective"]["text"],
                        "intent_anchor": spec["objective"]["anchor"],
                        "template_id": spec["template_id"],
                        "blocks": [
                            {
                                "role": b["role"],
                                "frame_id": b["frame_id"],
                                "text": b["text"],
                                "page": page,
                                "anchor": f"p. {page}",
                                "section": section_title(extraction, page),
                            }
                            for b, page in zip(spec["blocks"], pages_for, strict=True)
                        ],
                    }
                )
            copies.append({"copy_id": copy_id, "host": host, "plants": records})

    # Split by objective, not by plant: every plant that uses one objective goes to the
    # same side, so a development result cannot leak into the held-out half through a
    # shared objective. Alternating over a seeded shuffle keeps the halves close in size.
    split_rng = random.Random(f"{ctx.seed}-split")
    keys = sorted({(p["host"], p["objective_index"]) for c in copies for p in c["plants"]})
    split_rng.shuffle(keys)
    side = {key: ("dev" if i % 2 == 0 else "holdout") for i, key in enumerate(keys)}
    for copy in copies:
        for plant in copy["plants"]:
            plant["split"] = side[(plant["host"], plant["objective_index"])]
    build_cases.dropped = dropped
    return copies


# --- sanity: does F4 see the planted results? -----------------------------------


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def outcome_pickup(ctx: Context, copies: list[dict]) -> dict:
    """Local, no model call: after planting, how many planted result blocks
    does extract_statements return as outcomes, and does any planted text
    create a new objective."""
    settings = get_settings()
    hosts = ctx.hosts()
    twins = {}
    for name, path in hosts.items():
        out = extract_statements(_host_data(path)["extraction"], settings=settings)
        twins[name] = ({_norm(s.text) for s in out.intents}, {_norm(s.text) for s in out.outcomes})
    by_kind = defaultdict(lambda: [0, 0])
    missed, new_intents = [], 0
    for copy in copies:
        planted = apply_case(_host_data(hosts[copy["host"]])["extraction"], copy)
        out = extract_statements(planted, settings=settings)
        outcomes = [(_norm(s.text), s.other_anchors) for s in out.outcomes]
        new_intents += sum(1 for s in out.intents if _norm(s.text) not in twins[copy["host"]][0])
        for plant in copy["plants"]:
            for block in plant["blocks"]:
                if block["role"] != "result":
                    continue
                key = _norm(block["text"])
                picked = any(
                    key[:60] in text or (block["anchor"] in anchors and text[:30] == key[:30])
                    for text, anchors in outcomes
                )
                by_kind[plant["kind"]][1] += 1
                by_kind[plant["kind"]][0] += picked
                if not picked:
                    missed.append(
                        {
                            "plant_id": plant["plant_id"],
                            "frame": block["frame_id"],
                            "text": block["text"],
                        }
                    )
    total = [sum(v[0] for v in by_kind.values()), sum(v[1] for v in by_kind.values())]
    return {
        "picked_up": total[0],
        "planted_results": total[1],
        "by_kind": {k: {"picked_up": v[0], "planted": v[1]} for k, v in sorted(by_kind.items())},
        "new_intents_created_by_planted_text": new_intents,
        "missed": missed,
    }


# --- outputs ------------------------------------------------------------------


def _plants(copies):
    return [p for c in copies for p in c["plants"]]


def build_manifest(ctx: Context, copies: list[dict], pickup: dict | None) -> dict:
    plants = _plants(copies)
    hosts = {name: _host_data(path) for name, path in ctx.hosts().items()}
    counts = {
        "total": len(plants),
        "by_kind": Counter(p["kind"] for p in plants),
        "by_label": Counter(p["label"] for p in plants),
        "by_host": Counter(p["host"] for p in plants),
        "by_split": Counter(p["split"] for p in plants),
        "by_kind_and_host": {
            k: dict(Counter(p["host"] for p in plants if p["kind"] == k))
            for k in sorted({p["kind"] for p in plants})
        },
        "by_kind_and_split": {
            k: dict(Counter(p["split"] for p in plants if p["kind"] == k))
            for k in sorted({p["kind"] for p in plants})
        },
        "by_host_and_split": {
            h: dict(Counter(p["split"] for p in plants if p["host"] == h))
            for h in sorted({p["host"] for p in plants})
        },
        "by_role": dict(Counter(p["scored_as"] for p in plants)),
        "objectives_by_split": {
            sp: len({(p["host"], p["objective_index"]) for p in plants if p["split"] == sp})
            for sp in ("dev", "holdout")
        },
        "copies": len(copies),
        "copies_by_host": dict(Counter(c["host"] for c in copies)),
        "max_plants_in_a_copy": max(len(c["plants"]) for c in copies),
    }
    # An answer that never varies must not look accurate: the set has to hold both
    # labels in a real share, and the manifest states what always answering the
    # commoner label would score, so no figure is read without that floor. The
    # probe kinds are reported on their own, so they are not part of this mix.
    scored = [p for p in plants if p["scored_as"] != "probe"]
    labels = [p["label"] for p in scored]
    counts["label_balance"] = {
        "problems": balance_problems(labels, list(LABELS)),
        "always_one_label_accuracy": baseline_accuracy(labels),
        "by_split": {
            sp: {
                "problems": balance_problems(
                    [p["label"] for p in scored if p["split"] == sp], list(LABELS)
                ),
                "always_one_label_accuracy": baseline_accuracy(
                    [p["label"] for p in scored if p["split"] == sp]
                ),
            }
            for sp in ("dev", "holdout")
        },
    }
    frames = defaultdict(Counter)
    for plant in plants:
        for block in plant["blocks"]:
            frames[plant["kind"]][block["frame_id"]] += 1
    gaps = []
    for copy in copies:
        pages = sorted(b["page"] for p in copy["plants"] for b in p["blocks"])
        gaps.append(min((b - a for a, b in zip(pages, pages[1:], strict=False)), default=999))
    return {
        "lane": LANE,
        "ticket": "V-093",
        "seed": ctx.seed,
        "code_commit": ctx.code_commit,
        "honesty": HONESTY,
        "settings": {
            "plants_per_copy": PLANTS_PER_COPY,
            "page_gap": PAGE_GAP,
            "recall_target": RECALL_TARGET,
            "probe_per_kind": PROBE_PER_KIND,
            "restatement_target": RESTATEMENT_TARGET,
            "regions": REGIONS,
        },
        "counts": json.loads(json.dumps(counts)),
        "smallest_page_gap_in_any_copy": min(gaps),
        "distinct_templates": {
            k: len({p["template_id"] for p in plants if p["kind"] == k})
            for k in sorted({p["kind"] for p in plants})
        },
        "frame_usage": {k: dict(v) for k, v in sorted(frames.items())},
        "distinct_recall_frames": len(frames[KIND_RECALL]),
        "usable_objectives": {
            h: [
                {
                    "index": o["index"],
                    "anchor": o["anchor"],
                    "text": o["text"][:160],
                    "core": o["core"],
                }
                for o in d["usable"]
            ]
            for h, d in hosts.items()
        },
        "skipped_objectives": {
            h: [
                {
                    "index": o["index"],
                    "anchor": o["anchor"],
                    "text": o["text"][:160],
                    "reason": o["reason"],
                }
                for o in d["skipped"]
            ]
            for h, d in hosts.items()
        },
        "dropped_specs": getattr(build_cases, "dropped", []),
        "outcome_pickup": pickup,
        "known_limitations": [
            (
                "Recall plants are objectives the thesis itself says were not met. That tests "
                "'detects explicitly reported unmet objectives', not an overclaiming conclusion "
                "set against a weaker result, which this set does not contain."
            ),
            (
                "The result frames were written to open with phrases F4's cue file treats as "
                "results, so the extraction pickup figure measures the templates, not F4."
            ),
            (
                "All plants sit after the last clean sentence of a page, a fixed position a "
                "detector could in principle learn."
            ),
            (
                "About 27 real objectives from four theses, and most plants come from one "
                "non-capstone host (a public doctoral dissertation). The split is by objective, "
                "but the effective sample is the number of objectives, not the number of cases, "
                "which is why intervals are also bootstrapped by objective."
            ),
            (
                "Planted sentences reuse the objective's own verb phrase (lead-verb swaps only, "
                "the same for every kind), so lexical overlap with the objective is high. Pairing "
                "finds them more easily than it would a real contradiction."
            ),
            (
                "The judge sees only the objective and one result sentence, never surrounding "
                "context. The scope and limitation kinds therefore cannot be passed by reading "
                "context; they are a pre-declared probe of that cost, not a specificity figure."
            ),
            (
                "Dev and holdout share the same templates, so a prompt tuned on dev would carry "
                "into holdout. Nothing is tuned in this baseline; a later tuning round needs "
                "frames held out of dev."
            ),
            (
                "Plants enter the stored extraction, not the PDF, so this tests F4, not PDF "
                "extraction."
            ),
            (
                "Labels come from the template design, not from an instructor. Borderline kinds are"
                " listed in templates.md."
            ),
        ],
        "scoring_rule": SCORING_RULE,
        "borderline_by_kind": BORDERLINE,
    }


def render_templates(copies: list[dict]) -> str:
    examples = defaultdict(list)
    for plant in _plants(copies):
        for block in plant["blocks"]:
            key = (
                plant["kind"],
                plant["template_id"].split(":")[0],
                block["role"],
                block["frame_id"],
            )
            if len(examples[key]) < 2 and block["text"] not in examples[key]:
                examples[key].append(block["text"])
    families = [
        (KIND_RECALL, "contradictory", [("recall", "result", RECALL_FRAMES)]),
        (
            KIND_SCOPE,
            "not_contradictory",
            [("scope", "context", SCOPE_CONTEXT), ("scope", "result", SCOPE_RESULT)],
        ),
        (
            KIND_LIMITATION,
            "not_contradictory",
            [("limit", "context", LIMITATION_CONTEXT), ("limit", "result", LIMITATION_RESULT)],
        ),
        (KIND_RESTATE, "not_contradictory", [("restate", "result", RESTATE_FRAMES)]),
    ]
    lines = [
        "# F4 known-answer templates (V-093)",
        "",
        f"> {HONESTY}",
        "",
        "For realism review before any model run. Every frame is listed with two filled "
        "examples taken from the generated cases. `{x}` is the objective's own verb phrase, "
        "`{S}` the team or author, `{was}` was/were. A plain reading of every frame is given "
        "first, then the places where a reasonable reader might disagree with the label.",
        "",
        "Wording note: all frames describe what a thesis reports. None accuses anyone.",
        "",
    ]
    for kind, label, tables in families:
        lines += [
            f"## {kind}  (label: {label})",
            "",
            f"**Borderline note.** {BORDERLINE[kind]}",
            "",
        ]
        for family, role, table in tables:
            lines += [f"### {role} frames ({family})", ""]
            for frame_id, template in table.items():
                lines += [f"- **{frame_id}**: `{template}`"]
                for example in examples.get((kind, family, role, frame_id), []) or [
                    "(no example generated)"
                ]:
                    lines += [f"  - e.g. {example}"]
            lines += [""]
    return "\n".join(lines) + "\n"


def write_outputs(ctx: Context, copies: list[dict], pickup: dict | None, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "honesty": HONESTY,
        "seed": ctx.seed,
        "code_commit": ctx.code_commit,
        "copies": copies,
    }
    (out_dir / "cases.json").write_text(
        json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    manifest = build_manifest(ctx, copies, pickup)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "templates.md").write_text(render_templates(copies), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261031)
    parser.add_argument(
        "--skip-pickup", action="store_true", help="skip the extract_statements sanity pass"
    )
    args = parser.parse_args()
    ctx = Context(
        golden=args.golden,
        output=args.golden / "runs",
        seed=args.seed,
        code_commit=git_commit(ROOT),
    )
    copies = build_cases(ctx)
    pickup = None if args.skip_pickup else outcome_pickup(ctx, copies)
    write_outputs(ctx, copies, pickup, args.golden / LANE)
    manifest = json.loads((args.golden / LANE / "manifest.json").read_text(encoding="utf-8"))
    print(json.dumps(manifest["counts"], indent=1))
    problems = manifest["counts"]["label_balance"]["problems"]
    if problems:
        print("LABEL BALANCE PROBLEMS:", "; ".join(problems))
    if pickup:
        print("pickup", pickup["picked_up"], "/", pickup["planted_results"], pickup["by_kind"])
        print("new intents from planted text:", pickup["new_intents_created_by_planted_text"])


if __name__ == "__main__":
    main()
