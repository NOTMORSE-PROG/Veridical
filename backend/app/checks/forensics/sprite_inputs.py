"""F6.3: explicit native-table declarations, never inferred score scales."""

import json
import re
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.checks.forensics.keywords import ForensicsKeywords, load_keywords
from app.ingest.analytical_source import analytical_source
from app.ingest.schemas import ExtractionResult


@dataclass(frozen=True)
class SpriteInput:
    n: int
    mean: float
    sd: float
    mean_prec: int
    sd_prec: int
    min_val: int
    max_val: int
    n_items: int


@dataclass(frozen=True)
class SpriteRow:
    table_index: int
    row_index: int
    anchor: str
    cells: dict
    reason: str | None
    values: SpriteInput | None

    def receipt(self) -> dict:
        return {**asdict(self), "values": asdict(self.values) if self.values else None}


def single_item_incompatible(row: SpriteRow, policy: dict) -> bool:
    """Use explicit native declarations only to tighten the older single-item check."""
    if row.reason == "native_table_required":
        return False  # Vision metadata cannot establish this additional declaration gate.
    if not row.cells or row.reason == "ambiguous_columns" or "mean" not in row.cells:
        return True
    for name, permitted in (
        ("unit", policy["integer_units"]),
        ("sd_convention", policy["sample_sd_conventions"]),
    ):
        cell = row.cells.get(name)
        if cell is not None and (
            not isinstance(cell["value"], str)
            or cell["value"].strip().casefold() not in {value.casefold() for value in permitted}
        ):
            return True
    cell = row.cells.get("n_items")
    if cell is not None:
        raw = cell["value"]
        if not isinstance(raw, str) or len(raw.strip()) > policy["max_numeric_token_chars"]:
            return True
        try:
            return Decimal(raw.strip()) != 1
        except InvalidOperation:
            return True
    return False


def load_sprite_policy(path: Path | None = None) -> dict:
    if path is None:
        from app.config import get_settings

        path = get_settings().forensics_sprite_policy_file
    return json.loads(
        (path or Path(__file__).parent / "data/sprite_policy.json").read_text(encoding="utf-8")
    )


def _number(raw: str, max_chars: int) -> tuple[Decimal, int] | None:
    token = raw.strip()
    if len(token) > max_chars or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", token):
        return None
    try:
        number = Decimal(token)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return number, max(0, -number.as_tuple().exponent)


def extract_sprite_rows(
    extraction: ExtractionResult,
    policy: dict | None = None,
    *,
    max_rows: int | None = None,
    deadline: float | None = None,
    keywords: ForensicsKeywords | None = None,
) -> Iterator[SpriteRow]:
    policy = policy or load_sprite_policy()
    aliases = {
        name: {alias.strip().casefold() for alias in names}
        for name, names in policy["headers"].items()
    }
    required = set(aliases)
    keywords = keywords or load_keywords()
    allowed = {id(table) for table in analytical_source(extraction).tables}
    if max_rows is None:
        from app.config import get_settings

        max_rows = get_settings().forensics_sprite_max_input_rows
    constructed = 0
    for table_index, table in enumerate(extraction.tables):
        if not table.rows:
            continue
        columns = {
            name: [
                index
                for index, header in enumerate(table.rows[0])
                if header.strip().casefold() in names
            ]
            for name, names in aliases.items()
        }
        headers = {header.strip().casefold() for header in table.rows[0]}
        has_mean = bool(columns["mean"] or headers & keywords.mean_headers)
        has_summary = bool(
            columns["n"] or columns["sd"] or headers & (keywords.n_headers | keywords.sd_headers)
        )
        if not has_mean or not has_summary:
            continue
        anchor = (
            f"p. {table.page}"
            if table.page is not None
            else (f"¶{table.paragraph}" if table.paragraph is not None else "document")
        )
        missing = sorted(name for name in required if not columns[name])
        known_headers = {
            "n": keywords.n_headers,
            "mean": keywords.mean_headers,
            "sd": keywords.sd_headers,
        }
        ambiguous = any(len(indexes) > 1 for indexes in columns.values()) or any(
            sum(header.strip().casefold() in aliases[name] | known for header in table.rows[0]) > 1
            for name, known in known_headers.items()
        )
        for row_index, row in enumerate(table.rows):
            if row_index == 0 or not any(cell.strip() for cell in row):
                continue
            if constructed >= max_rows or (deadline is not None and time.monotonic() >= deadline):
                yield SpriteRow(
                    table_index,
                    row_index,
                    anchor,
                    {},
                    "input_admission_budget_reached"
                    if constructed >= max_rows
                    else "input_admission_deadline_reached",
                    None,
                )
                continue
            constructed += 1
            cells = {
                name: {
                    "column": indexes[0],
                    "header": table.rows[0][indexes[0]],
                    "value": row[indexes[0]] if indexes[0] < len(row) else None,
                }
                for name, indexes in columns.items()
                if len(indexes) == 1
            }
            reason = None
            values = None
            if id(table) not in allowed:
                reason = "source_scope_unavailable"
            elif table.source != "native" or table.low_confidence:
                reason = "native_table_required"
            elif ambiguous or len({indexes[0] for indexes in columns.values() if indexes}) != len(
                cells
            ):
                reason = "ambiguous_columns"
            elif missing:
                reason = "missing_declarations:" + ",".join(missing)
            elif any(cell["value"] is None for cell in cells.values()):
                reason = "missing_cells"
            elif cells["unit"]["value"].strip().casefold() not in policy["integer_units"]:
                reason = "integer_item_unit_not_declared"
            elif (
                cells["sd_convention"]["value"].strip().casefold()
                not in policy["sample_sd_conventions"]
            ):
                reason = "sample_sd_not_declared"
            else:
                parsed = {
                    name: _number(cells[name]["value"], policy["max_numeric_token_chars"])
                    for name in required - {"unit", "sd_convention"}
                }
                if any(value is None for value in parsed.values()):
                    reason = "invalid_numeric_declaration"
                elif any(
                    parsed[name][0] != parsed[name][0].to_integral_value()
                    for name in ("n", "n_items", "min_val", "max_val")
                ):
                    reason = "integer_declaration_required"
                else:
                    values = SpriteInput(
                        n=int(parsed["n"][0]),
                        mean=float(parsed["mean"][0]),
                        sd=float(parsed["sd"][0]),
                        mean_prec=parsed["mean"][1],
                        sd_prec=parsed["sd"][1],
                        min_val=int(parsed["min_val"][0]),
                        max_val=int(parsed["max_val"][0]),
                        n_items=int(parsed["n_items"][0]),
                    )
                    if (
                        values.n < 2
                        or values.n_items < 1
                        or values.min_val >= values.max_val
                        or values.sd < 0
                    ):
                        reason, values = "invalid_declared_bounds", None
                    elif not values.min_val <= values.mean <= values.max_val:
                        reason, values = "mean_outside_declared_scale", None
                    elif values.sd_prec > policy["max_sd_precision"]:
                        reason, values = "unsupported_sd_precision", None
            yield SpriteRow(table_index, row_index, anchor, cells, reason, values)
