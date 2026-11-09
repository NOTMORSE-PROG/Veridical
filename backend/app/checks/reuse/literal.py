"""Bounded lexical candidate retrieval and exact contiguous-word verification."""

import hashlib
import json
import re
from dataclasses import dataclass

from sqlalchemy import func, literal_column, or_, select
from sqlalchemy.exc import DBAPIError

from app.models.enums import LibraryEntryStatus
from app.models.library import LibraryEntry
from app.models.manuscript import Manuscript, ManuscriptPassageArchive

NORMALIZATION = "casefold_whitespace_tokens_v1"


def literal_policy_record(settings) -> dict:
    from app.ingest.quotations import quotation_policy

    policy = quotation_policy(settings.reuse_quotation_policy_file)
    return {
        **{
            key.removeprefix("reuse_literal_"): value
            for key, value in settings.model_dump().items()
            if key.startswith("reuse_literal_")
        },
        "normalization": NORMALIZATION,
        "source_read_max_bytes": settings.reuse_source_read_max_bytes,
        "source_cache_max_bytes": settings.reuse_source_cache_max_bytes,
        "source_cache_max_entries": settings.reuse_source_cache_max_entries,
        "quotation_policy_version": policy["version"],
        "quotation_policy_canonical_sha256": hashlib.sha256(
            json.dumps(policy, sort_keys=True).encode()
        ).hexdigest(),
    }


@dataclass(frozen=True)
class LiteralSpan:
    own_start: int
    own_end: int
    matched_start: int
    matched_end: int
    word_count: int


def literal_window(text: str, context: str, char_start: int) -> tuple[str, int, str | None]:
    """Expand only into a uniquely bound, stored continuous source window."""
    position = context.find(text) if text else -1
    if position < 0 or context.rfind(text) != position or position > char_start:
        return text, char_start, "boundary_context_not_verified"
    return context, char_start - position, None


def word_spans(text: str):
    return [(match.group().casefold(), *match.span()) for match in re.finditer(r"\S+", text)]


def maximal_literal_matches(matches, work_limit):
    """Keep maximal aligned proofs without joining text or distinct occurrences."""
    groups = {}
    for index, match in enumerate(matches):
        if match.literal_overlap is None:
            continue
        key = (
            match.matched_manuscript_id,
            match.own_chapter_index,
            match.matched_chapter_index,
            match.own_is_block_quote,
            match.matched_is_block_quote,
            match.own_is_reference_list,
            match.matched_is_reference_list,
            match.literal_overlap["normalization"],
        )
        groups.setdefault(key, []).append((index, match))
    removed, work = set(), 0
    unassessed = {item.own_passage_index for group in groups.values() for _index, item in group}
    for group in groups.values():
        retained = []
        for index, match in sorted(
            group, key=lambda item: item[1].literal_overlap["word_count"], reverse=True
        ):
            proof = match.literal_overlap
            if match.level != "literal_overlap":
                retained.append(match)
                continue
            for larger in retained:
                work += 1
                if work > work_limit:
                    return matches, unassessed
                outer = larger.literal_overlap
                if not all(
                    outer[f"{side}_start"] <= proof[f"{side}_start"]
                    and proof[f"{side}_end"] <= outer[f"{side}_end"]
                    for side in ("own", "matched")
                ):
                    continue
                work += sum(
                    proof[f"{side}_start"] - outer[f"{side}_start"] for side in ("own", "matched")
                )
                if work > work_limit:
                    return matches, unassessed
                prefixes = [
                    getattr(larger, f"{side}_text")[
                        outer[f"{side}_start"] - getattr(larger, f"{side}_char_start") : proof[
                            f"{side}_start"
                        ]
                        - getattr(larger, f"{side}_char_start")
                    ]
                    .casefold()
                    .split()
                    for side in ("own", "matched")
                ]
                if prefixes[0] == prefixes[1]:
                    removed.add(index)
                    break
            else:
                retained.append(match)
    return [match for index, match in enumerate(matches) if index not in removed], set()


def phrase_probes(text: str, settings) -> tuple[list[str], bool]:
    if len(text) > settings.reuse_literal_passage_max_chars:
        return [], True
    words = word_spans(text)
    length, minimum = settings.reuse_literal_probe_words, settings.reuse_literal_min_words
    stride = settings.reuse_literal_probe_stride
    if length > minimum or stride > minimum - length + 1:
        raise ValueError("Literal probe stride cannot cover the configured minimum span")
    starts = list(range(0, max(0, len(words) - length + 1), stride))
    truncated = len(starts) > settings.reuse_literal_max_probes
    probes = [
        text[words[start][1] : words[start + length - 1][2]]
        for start in starts[: settings.reuse_literal_max_probes]
    ]
    return list(dict.fromkeys(probes)), truncated


def literal_spans(
    own: str,
    matched: str,
    work_remaining: int,
    *,
    minimum: int,
    span_limit: int,
    own_quotes: list[bool],
    matched_quotes: list[bool],
    include_quotes: bool = False,
) -> tuple[list[LiteralSpan], int, bool]:
    left, right = word_spans(own), word_spans(matched)
    work = len(left) * len(right)
    if work > work_remaining:
        raise OverflowError("Literal token comparison budget exhausted")
    previous = [0] * (len(right) + 1)
    assert len(own_quotes) == len(left) and len(matched_quotes) == len(right)
    spans = []
    overflow = False
    for i, (token, _, _) in enumerate(left):
        current = [0] * (len(right) + 1)
        for j, (other, _, _) in enumerate(right):
            if token == other and (include_quotes or not (own_quotes[i] or matched_quotes[j])):
                current[j + 1] = previous[j] + 1
                length = current[j + 1]
                continues = (
                    i + 1 < len(left)
                    and j + 1 < len(right)
                    and left[i + 1][0] == right[j + 1][0]
                    and (include_quotes or not (own_quotes[i + 1] or matched_quotes[j + 1]))
                )
                if length >= minimum and not continues:
                    if len(spans) < span_limit:
                        spans.append(
                            LiteralSpan(
                                left[i - length + 1][1],
                                left[i][2],
                                right[j - length + 1][1],
                                right[j][2],
                                length,
                            )
                        )
                    else:
                        overflow = True
        previous = current
    return spans, work, overflow


async def literal_candidates(
    session, passage, *, exclude, include_reference_list, include_block_quote, settings, scope=None
):
    if len(passage.text) > settings.reuse_literal_passage_max_chars:
        return [], ["passage_text_limit"]
    probes, truncated = phrase_probes(passage.text, settings)
    if not probes:
        return [], ["probe_limit"] if truncated else []
    config = literal_column("'simple'::regconfig")
    issues = ["probe_limit"] if truncated else []
    if not passage.text.isascii():
        issues.append("unicode_retrieval_not_assessed")
    eligibility = [
        ManuscriptPassageArchive.model_id == settings.embedding_model_id,
        ManuscriptPassageArchive.manuscript_id.notin_(exclude),
        Manuscript.purged_at.is_(None),
        LibraryEntry.status == LibraryEntryStatus.active,
    ]
    if not include_reference_list:
        eligibility.append(ManuscriptPassageArchive.is_reference_list.is_(False))
    conditions = [
        *eligibility,
        or_(
            *(
                func.to_tsvector(config, ManuscriptPassageArchive.text).op("@@")(
                    func.phraseto_tsquery(config, probe)
                )
                for probe in probes
            )
        ),
    ]
    query = (
        select(
            ManuscriptPassageArchive,
            (1 - ManuscriptPassageArchive.embedding.cosine_distance(passage.embedding)).label(
                "similarity"
            ),
            Manuscript.group_label,
            Manuscript.instructor_id,
        )
        .join(Manuscript, Manuscript.id == ManuscriptPassageArchive.manuscript_id)
        .join(LibraryEntry, LibraryEntry.manuscript_id == Manuscript.id)
        .where(*conditions)
        .order_by(ManuscriptPassageArchive.id)
        .limit(settings.reuse_literal_candidate_limit + 1)
    )
    try:
        async with session.begin_nested():
            previous = await session.scalar(select(func.current_setting("statement_timeout")))
            await session.execute(
                select(
                    func.set_config(
                        "statement_timeout", str(settings.reuse_literal_query_timeout_ms), True
                    )
                )
            )
            nodes = (
                await session.execute(
                    select(
                        *(func.numnode(func.phraseto_tsquery(config, probe)) for probe in probes)
                    )
                )
            ).one()
            if any(node == 0 for node in nodes):
                issues.append("unindexable_probe")
            if scope is not None:
                if "unicode_corpus" not in scope:
                    inventory = (
                        select(ManuscriptPassageArchive.id)
                        .join(Manuscript, Manuscript.id == ManuscriptPassageArchive.manuscript_id)
                        .join(LibraryEntry, LibraryEntry.manuscript_id == Manuscript.id)
                        .where(
                            *eligibility,
                            func.octet_length(ManuscriptPassageArchive.text)
                            != func.length(ManuscriptPassageArchive.text),
                        )
                    )
                    scope["unicode_corpus"] = bool(await session.scalar(select(inventory.exists())))
                if scope["unicode_corpus"]:
                    issues.append("unicode_corpus_retrieval_not_assessed")
            inventory = query.with_only_columns(
                ManuscriptPassageArchive.id,
                func.length(ManuscriptPassageArchive.text).label("text_chars"),
                func.length(ManuscriptPassageArchive.context_text).label("context_chars"),
            )
            candidates = list((await session.execute(inventory)).all())
            if len(candidates) > settings.reuse_literal_candidate_limit:
                issues.append("candidate_limit")
            ids = []
            for candidate in candidates[: settings.reuse_literal_candidate_limit]:
                if (
                    candidate.text_chars > settings.reuse_literal_candidate_max_chars
                    or candidate.context_chars > settings.reuse_literal_candidate_max_chars
                ):
                    issues.append("candidate_text_limit")
                else:
                    ids.append(candidate.id)
            rows = (
                list(
                    (await session.execute(query.where(ManuscriptPassageArchive.id.in_(ids)))).all()
                )
                if ids
                else []
            )
            await session.execute(select(func.set_config("statement_timeout", previous, True)))
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != "57014":
            raise
        return [], ["query_timeout"]
    return rows[: settings.reuse_literal_candidate_limit], issues
