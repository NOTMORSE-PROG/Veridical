"""Source-local declarations used to restrict numerical applicability (F6)."""

import json
import re
from pathlib import Path

from app.checks.forensics.keywords import load_keywords
from app.checks.forensics.sprite_inputs import declared_item_count, load_sprite_policy
from app.config import get_settings
from app.ingest.analytical_source import analytical_source
from app.ingest.quotations import located_quote_status
from app.ingest.schemas import ExtractionResult


def load_table_context_policy() -> dict:
    path = get_settings().forensics_table_context_file
    return json.loads(
        (path or Path(__file__).parent / "data/table_context_policy.json").read_text(
            encoding="utf-8"
        )
    )


def has_pattern(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def table_contexts(extraction: ExtractionResult, policy: dict | None = None) -> dict[int, dict]:
    """Keep only an adjacent, safe Word block or the table's own caption.

    A shared page number cannot bind prose to a table. PDF prose therefore
    requires future geometric binding; it is never borrowed from the whole page.
    """
    policy = policy or load_table_context_policy()
    settings = get_settings()
    quote_cache = {}
    source = analytical_source(extraction)
    safe_tables = {id(table) for table in source.tables}
    blocks = [block for scope in source.scopes for block in scope if not block.is_furniture]
    by_paragraph = {}
    raw_by_paragraph = {}
    for block in extraction.blocks:
        if block.paragraph is not None:
            raw_by_paragraph.setdefault(block.paragraph, []).append(block)
    safe_blocks = {id(block) for block in blocks}
    for block in blocks:
        if block.paragraph is not None:
            by_paragraph.setdefault(block.paragraph, []).append(block)
    table_paragraphs = {table.paragraph for table in extraction.tables}
    heading_paragraphs, pending = set(), list(extraction.section_tree.nodes)
    while pending:
        node = pending.pop()
        if node.paragraph is not None:
            heading_paragraphs.add(node.paragraph)
        pending.extend(node.children)
    contexts = {}
    for index, table in enumerate(extraction.tables):
        if id(table) not in safe_tables or table.low_confidence:
            continue
        receipts = []
        receipt_owners = []
        caption_owner = None
        if (
            table.source == "native"
            and table.paragraph is not None
            and table.caption
            and len(table.caption) <= policy["max_context_chars"]
        ):
            possible = [
                block
                for position in (table.paragraph - 1, table.paragraph + 1)
                for block in raw_by_paragraph.get(position, [])
                if block.text.strip() == table.caption.strip()
            ]
            if (
                len(possible) == 1
                and id(possible[0]) in safe_blocks
                and len(raw_by_paragraph[possible[0].paragraph]) == 1
                and possible[0].paragraph not in heading_paragraphs
                and (
                    possible[0].paragraph < table.paragraph
                    or (
                        table.paragraph - 1 not in source.excluded_paragraphs
                        and len(raw_by_paragraph.get(table.paragraph - 1, [])) <= 1
                    )
                )
            ):
                caption_owner = possible[0]
        # A copied caption field cannot recover text withheld from its source
        # paragraph, or establish a declaration from an image reading alone.
        if caption_owner is not None:
            receipts.append({"kind": "caption", "text": table.caption})
            receipt_owners.append(caption_owner)
        if table.source == "native" and table.paragraph is not None:
            end = table.paragraph
            adjacent = by_paragraph.get(end - 1, [])
            # The table's own caption is already retained above. It does not
            # separate a declaration from its table, but any other body item does.
            if (
                caption_owner is not None
                and len(adjacent) == 1
                and adjacent[0] is caption_owner
                and adjacent[0].paragraph not in heading_paragraphs
            ):
                end = adjacent[0].paragraph
            preceding = [
                block
                for gap in range(1, policy["max_body_item_gap"] + 1)
                for block in by_paragraph.get(end - gap, [])
            ]
            if len(preceding) == 1:
                block = preceding[0]
                intervening = any(
                    position in table_paragraphs
                    or position in heading_paragraphs
                    or position in source.excluded_paragraphs
                    for position in range(block.paragraph, table.paragraph)
                )
                if (
                    not intervening
                    and len(block.text) <= policy["max_context_chars"]
                    and (not table.caption or block.text.strip() != table.caption.strip())
                ):
                    receipts.append(
                        {
                            "kind": "preceding_block",
                            "paragraph": block.paragraph,
                            "text": block.text,
                        }
                    )
                    receipt_owners.append(block)
        declarations = []
        for receipt, owner in zip(receipts, receipt_owners, strict=True):
            seen = set()
            for pattern in policy["single_integer_patterns"]:
                for match in re.finditer(pattern, receipt["text"], re.IGNORECASE):
                    if match.span() in seen:
                        continue
                    seen.add(match.span())
                    # Validate the assertion itself, so an unrelated quotation
                    # elsewhere in the paragraph cannot establish or veto it.
                    status = located_quote_status(
                        source, match.group(), settings, _scope_cache=quote_cache
                    )
                    anchored = (
                        status is not None
                        and status[0] == owner.paragraph
                        and status[2] == owner.page
                    )
                    declarations.append(
                        {
                            "kind": receipt["kind"],
                            "paragraph": owner.paragraph,
                            "text": match.group(),
                            "quotation_status": status[1] if anchored else None,
                        }
                    )
        contexts[index] = {
            "policy_version": policy["version"],
            "sources": receipts,
            "text": "\n".join(receipt["text"] for receipt in receipts),
            "integer_declarations": declarations,
        }
    return contexts


def mean_applicability(extraction: ExtractionResult, contexts: dict[int, dict]) -> dict[int, dict]:
    policy = load_table_context_policy()
    keywords = load_keywords()
    sprite = load_sprite_policy()
    receipts = {}
    for index, table in enumerate(extraction.tables):
        if not table.rows or not any(
            header.strip().casefold() in keywords.mean_headers for header in table.rows[0]
        ):
            continue
        context = contexts.get(index, {})
        text = context.get("text", "")
        reason = None
        for name in ("continuous", "weighted", "composite"):
            if has_pattern(text, policy[f"{name}_patterns"]):
                reason = name
                break
        headers = " | ".join(table.rows[0]) if table.rows else ""
        if has_pattern(headers, policy["weighted_patterns"]):
            reason = "weighted"
        single_integer = any(
            declaration["quotation_status"] is False
            for declaration in context.get("integer_declarations", [])
        )
        columns = {
            name: [
                position
                for position, header in enumerate(table.rows[0])
                if header.strip().casefold() in set(sprite["headers"][name])
            ]
            for name in ("unit", "n_items")
        }
        item_counts = []
        if len(columns["n_items"]) == 1:
            position = columns["n_items"][0]
            item_counts = [
                declared_item_count(row[position], sprite) if position < len(row) else None
                for row in table.rows[1:]
            ]
            if any(count is not None and count > 1 for count in item_counts):
                reason = "composite"
            elif any(count is None for count in item_counts):
                reason = "integer_granularity_unassessed"
        elif len(columns["n_items"]) > 1 or len(columns["unit"]) > 1:
            reason = "integer_granularity_unassessed"
        declared = table.source == "native" and all(
            len(positions) == 1 for positions in columns.values()
        )
        if declared:
            unit, items = columns["unit"][0], columns["n_items"][0]
            declared = len(table.rows) > 1 and all(
                max(unit, items) < len(row)
                and row[unit].strip().casefold() in sprite["integer_units"]
                and declared_item_count(row[items], sprite) == 1
                for row in table.rows[1:]
            )
        single_integer = single_integer or declared
        if has_pattern(text, policy["single_integer_veto_patterns"]):
            single_integer = False
        if reason is None and not single_integer:
            reason = "integer_granularity_unassessed"
        receipts[index] = {
            **context,
            "reason": reason,
            "state": "unassessed"
            if reason == "integer_granularity_unassessed"
            else "excluded"
            if reason
            else "eligible",
            "single_integer_declared": single_integer,
            "declared_item_counts": item_counts,
        }
    return receipts
