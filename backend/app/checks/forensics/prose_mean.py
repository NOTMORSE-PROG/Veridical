"""Source-local integer means with explicit design and quotation evidence (F6)."""

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.checks.forensics.checks import GRIM_INCONSISTENT_WORDING, ForensicsFlagDraft
from app.checks.forensics.grim import grim_check
from app.config import get_settings
from app.ingest.analytical_source import analytical_source
from app.ingest.blockquotes import block_quote_ids_in_scope
from app.ingest.quotations import located_quote_status
from app.ingest.schemas import ExtractionResult
from app.models.enums import FlagSeverity


def load_prose_mean_policy() -> dict:
    path = get_settings().forensics_prose_mean_policy_file
    content = (path or Path(__file__).parent / "data/prose_mean_policy.json").read_bytes()
    return {**json.loads(content), "sha256": hashlib.sha256(content).hexdigest()}


@dataclass(frozen=True)
class ProseMeanAssessment:
    flags: list[ForensicsFlagDraft]
    ledger: dict
    covered_ranges: list[tuple[int, int, int]]


def _count(raw: str, policy: dict) -> int:
    if re.fullmatch(policy["digit_count_pattern"], raw):
        return int(raw.replace(policy["count_group_separator"], ""))
    parts = re.split(policy["word_separator"], raw.casefold())
    if len(parts) == 1:
        return {**policy["small_numbers"], **policy["tens"]}[parts[0]]
    return policy["tens"][parts[0]] + policy["small_numbers"][parts[1]]


def assess_prose_means(extraction: ExtractionResult) -> ProseMeanAssessment:
    policy, settings = load_prose_mean_policy(), get_settings()
    small = "|".join(map(re.escape, policy["small_numbers"]))
    tens = "|".join(map(re.escape, policy["tens"]))
    ones = "|".join(map(re.escape, policy["ones"]))
    count = (
        rf"(?:{policy['digit_count_pattern']}|"
        rf"(?:{tens})(?:{policy['word_separator']}(?:{ones}))?|(?:{small}))"
    )
    sample_patterns = [
        re.compile(item.replace("$COUNT", count), re.I) for item in policy["sample_patterns"]
    ]
    mean_pattern = re.compile(policy["mean_pattern"], re.I)
    sample_context_patterns = [
        item.replace("$COUNT", count).replace("$DIGITS", policy["digit_count_pattern"])
        for item in policy["sample_context_patterns"]
    ]
    source = analytical_source(extraction)
    blocks = [block for scope in source.scopes for block in scope if not block.is_furniture]
    # Native PDF geometry supports the existing indentation-based quote check.
    # Legacy text without that geometry keeps its unknown quotation status.
    if (
        source.state == "not_applicable"
        and blocks
        and all(block.page is not None and block.bbox is not None for block in blocks)
    ):
        quote_ids = set()
        for scope in source.scopes:
            quote_ids.update(block_quote_ids_in_scope(list(scope), settings))
        source = replace(source, quotation_block_ids=frozenset(quote_ids))
    receipts, flags, ranges = [], [], []
    quote_cache, omitted = {}, 0

    def matches(text, patterns):
        return list(
            {
                match.span(): match
                for pattern in patterns
                for match in re.finditer(pattern, text, re.I)
            }.values()
        )

    for index, block in enumerate(blocks):
        over_limit = len(block.text) > policy["max_source_chars"]
        if over_limit:
            if not re.search(policy["over_limit_candidate_pattern"], block.text, re.I):
                continue
            means, counts = [], []
        else:
            means = list(mean_pattern.finditer(block.text))
            counts = list(
                {
                    match.span(): match
                    for pattern in sample_patterns
                    for match in pattern.finditer(block.text)
                }.values()
            )
        if not means and not counts and not over_limit:
            continue
        if (
            not means
            and not over_limit
            and not (
                any(re.search(pattern, block.text, re.I) for pattern in policy["integer_patterns"])
                and any(
                    re.search(pattern, block.text, re.I)
                    for pattern in policy["single_item_patterns"]
                )
            )
        ):
            continue
        if len(receipts) >= policy["max_receipts"]:
            omitted += 1
            continue
        anchor = (
            f"p. {block.page}"
            if block.page is not None
            else f"¶{block.paragraph}"
            if block.paragraph is not None
            else "document"
        )
        record = {
            "source_block_index": index,
            "page": block.page,
            "paragraph": block.paragraph,
            "anchor": anchor,
            "state": "unassessed",
            "reason": None,
            "evidence": [],
            "checks_not_performed": policy["unperformed_checks"],
        }
        receipts.append(record)
        if over_limit:
            record["reason"] = "source_block_limit"
            continue
        record["source_text"] = block.text
        record["quantities"] = [
            {
                "kind": kind,
                "source_start": match.start(),
                "source_end": match.end(),
                "text": match.group(),
            }
            for kind, observed in (("sample_size", counts), ("mean", means))
            for match in observed
        ]
        if matches(block.text, policy["table_reference_patterns"]):
            record["reason"] = "table_reference_requires_binding"
            continue
        if len(means) > 1 or len(counts) > 1:
            record["reason"] = "missing_or_multiple_sample_sizes_or_means"
            continue
        if matches(block.text, sample_context_patterns) or any(
            re.match(policy["count_continuation_pattern"], block.text[match.end("count") :])
            or (
                match.groupdict().get("assignment") is not None
                and not re.match(policy["assignment_end_pattern"], block.text[match.end("count") :])
            )
            for match in counts
        ):
            record["reason"] = "unsupported_or_qualified_sample_size"
            continue
        if means and re.match(policy["mean_continuation_pattern"], block.text[means[0].end() :]):
            record["reason"] = "unsupported_mean_expression"
            continue
        integer = matches(block.text, policy["integer_patterns"])
        single_item = matches(block.text, policy["single_item_patterns"])
        vetoes = [
            (reason, match)
            for reason, patterns in policy["veto_patterns"].items()
            for match in matches(block.text, patterns)
        ]
        proofs = [*counts, *means, *integer, *single_item, *(match for _, match in vetoes)]
        statuses = []
        for match in proofs:
            status = located_quote_status(source, match.group(), settings, _scope_cache=quote_cache)
            quoted = (
                status[1]
                if status is not None and status[0] == block.paragraph and status[2] == block.page
                else None
            )
            statuses.append(quoted)
            record["evidence"].append(
                {
                    "source_start": match.start(),
                    "source_end": match.end(),
                    "text": match.group(),
                    "quotation_status": quoted,
                }
            )
        if any(status is True for status in statuses):
            record.update(state="excluded", reason="quoted_quantitative_evidence")
        elif any(status is None for status in statuses):
            record["reason"] = "quotation_or_location_unverified"
        elif vetoes:
            record.update(state="excluded", reason=vetoes[0][0])
        elif matches(block.text, policy["uncertain_context_patterns"]):
            record["reason"] = "conditional_or_negated_context"
        elif len(means) != 1 or len(counts) != 1:
            record["reason"] = "missing_or_multiple_sample_sizes_or_means"
        elif len(integer) != 1 or len(single_item) != 1:
            record["reason"] = "single_integer_item_design_unverified"
        else:
            n, raw_mean = _count(counts[0]["count"], policy), means[0]["mean"]
            try:
                mean = Decimal(raw_mean)
                precision = max(0, -mean.as_tuple().exponent)
                valid = (
                    mean.is_finite()
                    and math.isfinite(float(mean))
                    and (mean == 0 or float(mean) != 0)
                    and Decimal(repr(float(mean))) == mean
                    and 0 < n <= policy["max_sample_size"]
                    and precision <= policy["max_mean_precision"]
                )
            except (InvalidOperation, ValueError):
                valid = False
            if not valid:
                record["reason"] = "unsupported_numeric_value_or_precision"
                continue
            record.update(
                state="assessed",
                n=n,
                mean=raw_mean,
                mean_precision=precision,
                consistent=grim_check(n, float(mean), prec=precision),
            )
            if not record["consistent"]:
                flags.append(
                    ForensicsFlagDraft(
                        severity=FlagSeverity.med,
                        evidence_excerpt=block.text,
                        page_anchor=anchor,
                        detail={
                            "kind": "grim_inconsistent",
                            "reason": GRIM_INCONSISTENT_WORDING.format(mean=raw_mean, n=n),
                            "n": n,
                            "mean": float(mean),
                            "mean_precision": precision,
                            "prose_source": record.copy(),
                            "policy_version": policy["version"],
                        },
                    )
                )
        if means and record["state"] in {"assessed", "excluded"}:
            ranges.append((index, means[0].start(), means[0].end()))
        sd = matches(block.text, policy["sd_patterns"])
        if sd:
            record["unassessed_sd"] = [
                {
                    "source_start": match.start(),
                    "source_end": match.end(),
                    "text": match.group(),
                    "reason": "prose_standard_deviation_not_supported",
                }
                for match in sd
            ]
    ledger = {
        "version": policy["version"],
        "policy_sha256": policy["sha256"],
        "scope": "explicit_single_integer_item_means_in_one_native_block",
        "records": receipts,
        "omitted_blocks": omitted,
        "omitted_reason": "receipt_limit" if omitted else None,
        "assessed": sum(row["state"] == "assessed" for row in receipts),
        "excluded": sum(row["state"] == "excluded" for row in receipts),
        "unassessed": sum(
            row["state"] == "unassessed" or bool(row.get("unassessed_sd")) for row in receipts
        ),
    }
    return ProseMeanAssessment(flags, ledger, ranges)
