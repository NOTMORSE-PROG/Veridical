"""F3: quote existence and voting agreement are not criterion support."""

import json
from types import SimpleNamespace

import pytest

from app.checks.consistency import run_semantic_checks_with_consistency
from app.checks.semantic import build_semantic_batches, run_semantic_checks
from app.config import Settings
from app.errors import ApiDownError, QuotaExhaustedError
from app.ingest.schemas import ExtractionResult, SectionNode, SectionTree, TextBlock
from app.models.enums import ResultOutcome

QUOTE = "Smith (2020). A complete guide to writing. Research Press."
BODY = "The reference is discussed in the implementation and compared with our results."


class Session:
    def __init__(self):
        self.added = []

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        pass


class Client:
    def __init__(self, support="unsupported"):
        self.support = support
        self.calls = []

    async def complete(self, prompt_type, prompt, **context):
        self.calls.append((prompt_type, prompt, context))
        if prompt_type == "semantic_support":
            task = json.loads(prompt.split("--- TASK JSON ---\n", 1)[1])
            return {
                "items": [
                    {
                        "index": 0,
                        "status": self.support,
                        "reason": (
                            "The bibliography alone does not establish in-text completeness."
                            if self.support != "supported"
                            else "The supplied passage demonstrates the requested reference format."
                        ),
                        "requirements": [
                            {
                                "key": obligation["key"],
                                "status": self.support,
                                "evidence_quotes": [QUOTE] if self.support == "supported" else [],
                            }
                            for obligation in task["items"][0]["required_obligations"]
                        ],
                    }
                ]
            }
        return {
            "verdicts": [
                {
                    "index": 0,
                    "verdict": "Exceptional",
                    "reasoning": "Complete and correct documentation.",
                    "evidence_quotes": [QUOTE],
                }
            ]
        }


def criterion():
    return SimpleNamespace(
        id=1,
        text="Sources and documentation completeness and relevance of references",
        evidence="Reference list and in-text documentation",
        levels=[
            {"name": "Limited", "level": 1, "points": 1, "descriptor": "Incomplete documentation"},
            {
                "name": "Exceptional",
                "level": 2,
                "points": 2,
                "descriptor": "Complete documentation",
            },
        ],
    )


def extraction():
    return ExtractionResult(
        page_count=2,
        anchor_kind="page",
        image_only=False,
        text_chars=len(BODY + QUOTE),
        blocks=[
            TextBlock(page=1, text=BODY, max_font_size=11, bold_ratio=0),
            TextBlock(page=2, text="References", max_font_size=14, bold_ratio=1),
            TextBlock(page=2, text=QUOTE, max_font_size=11, bold_ratio=0),
        ],
        section_tree=SectionTree(
            source="heuristics",
            nodes=[SectionNode(title="References", level=1, page=2)],
        ),
        images=[],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", [run_semantic_checks, run_semantic_checks_with_consistency])
async def test_locatable_bibliography_cannot_establish_complete_documentation(runner):
    client = Client()
    result = (await runner(Session(), 1, [criterion()], extraction(), client, Settings()))[0]
    assert result.outcome == ResultOutcome.escalated
    assert result.score is None
    assert "level" not in result.detail
    assert "verdict" not in result.detail
    assert result.detail["proposed_verdict"] == "Exceptional"
    assert result.detail["support_check"]["status"] == "unsupported"
    assert "bibliography" in result.detail["reason"]
    assert result.detail["evidence"][0]["quote"] == QUOTE


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", [run_semantic_checks, run_semantic_checks_with_consistency])
async def test_agreeing_support_model_cannot_override_explicit_body_evidence_obligation(runner):
    client = Client("supported")
    result = (await runner(Session(), 1, [criterion()], extraction(), client, Settings()))[0]
    assert result.outcome == ResultOutcome.escalated and result.score is None
    assert result.detail["support_check"]["scope_guard"] == "required_body_evidence_unconfirmed"
    assert "in-text documentation" in result.detail["reason"]
    assert "verdict" not in result.detail and "level" not in result.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", [run_semantic_checks, run_semantic_checks_with_consistency])
@pytest.mark.parametrize(
    "variant", ["unknown_boundary", "descriptor_only", "unicode_hyphen", "unicode_dash"]
)
async def test_body_scope_guard_handles_uncertain_boundaries_and_requirement_forms(runner, variant):
    item, source = criterion(), extraction()
    if variant == "unknown_boundary":
        source.blocks.pop(1)
    elif variant == "descriptor_only":
        item.evidence = "Inspect documentation"
        item.levels[-1]["descriptor"] = "Complete in-text documentation"
    else:
        item.evidence = (
            "Reference list and in"
            + ("\u2011" if variant == "unicode_hyphen" else "\u2013")
            + "text documentation"
        )
    result = (await runner(Session(), 1, [item], source, Client("supported"), Settings()))[0]
    assert result.outcome == ResultOutcome.escalated and result.score is None
    assert result.detail["support_check"]["scope_guard"] == "required_body_evidence_unconfirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", [run_semantic_checks, run_semantic_checks_with_consistency])
async def test_distinct_body_passage_can_satisfy_necessary_source_scope(runner):
    body = "Smith (2020) explains the reference format used for our results."
    source = extraction()
    source.blocks[0].text = body

    class BodyClient(Client):
        async def complete(self, kind, prompt, **context):
            answer = await super().complete(kind, prompt, **context)
            if kind == "semantic_grading":
                answer["verdicts"][0]["evidence_quotes"] = [body]
            if kind == "semantic_support":
                for item in answer["items"]:
                    for requirement in item["requirements"]:
                        requirement["evidence_quotes"] = [body]
            return answer

    result = (
        await runner(Session(), 1, [criterion()], source, BodyClient("supported"), Settings())
    )[0]
    assert result.outcome == ResultOutcome.passed
    assert result.detail["support_check"]["status"] == "supported"
    assert result.detail["evidence"][0]["quote"] == body


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", [run_semantic_checks, run_semantic_checks_with_consistency])
async def test_supported_named_level_retains_its_scale_and_basis(runner):
    item = criterion()
    item.text = "Reference entry gives author, year, title and publisher"
    item.evidence = "The bibliography entry"
    item.levels[-1]["descriptor"] = "Author, year, title and publisher all present"
    client = Client("supported")
    result = (await runner(Session(), 1, [item], extraction(), client, Settings()))[0]
    assert result.outcome == ResultOutcome.passed
    assert result.detail["level"]["name"] == "Exceptional"
    assert result.detail["support_check"]["status"] == "supported"
    assert result.score == 100
    assert [call[0] for call in client.calls].count("semantic_support") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", [run_semantic_checks, run_semantic_checks_with_consistency])
async def test_source_identifier_alone_cannot_establish_scholarly_contribution(runner):
    doi = "DOI: 10.21105/joss.01943"
    source, item = extraction(), criterion()
    source.blocks[0].text = doi
    item.text, item.evidence = "Contribution to the body of knowledge", "Research findings"

    class IdentifierClient(Client):
        async def complete(self, kind, prompt, **context):
            answer = await super().complete(kind, prompt, **context)
            if kind == "semantic_grading":
                answer["verdicts"][0]["evidence_quotes"] = [doi]
            else:
                for requirement in answer["items"][0]["requirements"]:
                    requirement["evidence_quotes"] = [doi]
            return answer

    result = (
        await runner(Session(), 1, [item], source, IdentifierClient("supported"), Settings())
    )[0]
    assert result.outcome == ResultOutcome.escalated and result.score is None
    assert "source identifier" in result.detail["reason"]
    assert "verdict" not in result.detail


def test_older_scope_configuration_inherits_new_packaged_provenance_guard(tmp_path):
    from app.checks.support import _scope_patterns

    path = tmp_path / "old-scopes.json"
    path.write_text(json.dumps({"body_required": [r"\bin-text\b"]}), encoding="utf-8")
    configured = _scope_patterns(path)
    assert configured["body_required"][0].pattern == r"\bin-text\b"
    assert any(
        pattern.fullmatch("doi: 10.21105/joss.01943")
        for pattern in configured["non_substantive_quotes"]
    )


def test_mentioning_references_does_not_discard_body_context():
    batches, missing = build_semantic_batches([criterion()], extraction())
    assert not missing
    assert len(batches) == 1
    assert BODY in batches[0][0].context_text
    assert QUOTE in batches[0][0].context_text


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", [run_semantic_checks, run_semantic_checks_with_consistency])
@pytest.mark.parametrize(
    "mutation",
    [
        "wrapper",
        "missing",
        "duplicate_item",
        "boolean_index",
        "unknown_index",
        "missing_obligation",
        "duplicate_obligation",
        "wrong_obligation",
        "replacement_quote",
        "changed_case",
        "contradiction",
        "blank_reason",
        "empty_quotes",
    ],
)
async def test_bad_support_never_leaves_an_accepted_grade(runner, mutation):
    class Mutated(Client):
        async def complete(self, prompt_type, prompt, **context):
            response = await super().complete(prompt_type, prompt, **context)
            if prompt_type != "semantic_support":
                return response
            item = response["items"][0]
            part = item["requirements"][0]
            if mutation == "wrapper":
                return []
            if mutation == "missing":
                return {"items": []}
            if mutation == "duplicate_item":
                response["items"].append(item)
            if mutation == "boolean_index":
                item["index"] = False
            if mutation == "unknown_index":
                response["items"].append({**item, "index": 99})
            if mutation == "missing_obligation":
                item["requirements"].pop()
            if mutation == "duplicate_obligation":
                item["requirements"].append(part)
            if mutation == "wrong_obligation":
                part["key"] = "different"
            if mutation == "replacement_quote":
                part["evidence_quotes"] = [BODY]
            if mutation == "changed_case":
                part["evidence_quotes"] = [QUOTE.upper()]
            if mutation == "contradiction":
                part["status"] = "uncertain"
            if mutation == "blank_reason":
                item["reason"] = " "
            if mutation == "empty_quotes":
                part["evidence_quotes"] = []
            return response

    result = (
        await runner(Session(), 1, [criterion()], extraction(), Mutated("supported"), Settings())
    )[0]
    assert result.outcome == ResultOutcome.escalated
    assert result.score is None
    assert "level" not in result.detail and "verdict" not in result.detail
    assert result.detail["support_check"]["status"] == "incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", [run_semantic_checks, run_semantic_checks_with_consistency])
@pytest.mark.parametrize("error", [QuotaExhaustedError, ApiDownError])
async def test_unavailable_support_preserves_availability_and_does_not_persist(runner, error):
    class Unavailable(Client):
        async def complete(self, prompt_type, prompt, **context):
            if prompt_type == "semantic_support":
                raise error("controlled unavailable service")
            return await super().complete(prompt_type, prompt, **context)

    session = Session()
    with pytest.raises(error):
        await runner(session, 1, [criterion()], extraction(), Unavailable(), Settings())
    assert session.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize("runner", [run_semantic_checks, run_semantic_checks_with_consistency])
async def test_full_context_overflow_makes_no_grading_call(runner):
    client = Client()
    settings = Settings(semantic_context_max_chars=1)
    result = (await runner(Session(), 1, [criterion()], extraction(), client, settings))[0]
    assert result.outcome == ResultOutcome.escalated and result.score is None
    assert client.calls == []
    assert "silently omitted" in result.detail["reason"]
