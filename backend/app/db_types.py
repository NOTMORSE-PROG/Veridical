"""Request value types that must fit the database's columns.

Kept free of database imports on purpose: request schemas use these types,
and the native-compute helper loads those schemas with only its own small set
of packages installed, so neither asyncpg nor SQLAlchemy is there to import.
"""

from typing import Annotated

from pydantic import AfterValidator, Field
from pydantic_core import PydanticCustomError

from app.config import BIGINT_MAX, BIGINT_MIN

__all__ = ["BIGINT_MAX", "BIGINT_MIN", "TEXT_NOT_STORABLE", "DbInt", "DbText", "db_text"]

# Every id, page and revision we look up fits in BIGINT, so a request integer
# outside it cannot match anything. asyncpg refuses to send such a value at
# all, which used to surface as a 500; route parameters and request fields
# typed DbInt are refused with a 422 instead (BUG-536). Only BIGINT columns are
# covered: an integer compared with an INTEGER column needs its own bound.


class _FitsBigint:
    """The range check behind DbInt.

    Not a plain `Field(ge=..., le=...)`: pydantic keeps only the last bound of
    each kind, so `x: DbInt = Field(ge=0)` would silently lose its own `ge=0`.
    Instead this narrows the int schema to the BIGINT range while keeping any
    tighter bound the field already has. A bound added after it (a
    `Query(le=200)`, say) replaces that side of the range, so it must itself
    fit BIGINT; tests/test_request_int_bounds.py checks every route.
    """

    @classmethod
    def __get_pydantic_core_schema__(cls, source, handler):
        schema = handler(source)
        if schema.get("type") != "int":
            raise TypeError(f"DbInt must annotate a plain int, not {schema.get('type')}")
        schema = {**schema, "le": min(schema.get("le", BIGINT_MAX), BIGINT_MAX)}
        # A strict lower bound (gt=0) already inside the range needs no ge.
        if schema.get("gt", BIGINT_MIN - 1) < BIGINT_MIN:
            schema["ge"] = max(schema.get("ge", BIGINT_MIN), BIGINT_MIN)
        return schema

    @classmethod
    def __get_pydantic_json_schema__(cls, schema, handler):
        # A gt added after DbInt sits beside its ge; publish only the one that
        # applies, so the docs do not suggest negative ids are accepted.
        out = handler(schema)
        if out.get("exclusiveMinimum", BIGINT_MIN - 1) >= out.get("minimum", BIGINT_MIN - 1):
            out.pop("minimum", None)
        return out


DbInt = Annotated[int, _FitsBigint]

# PostgreSQL text and JSONB cannot hold U+0000, and request text carrying it
# used to reach asyncpg and fail with a 500 (BUG-537). Request text typed
# DbText is refused with a 422 naming the field instead. (Lone surrogates are
# already refused by pydantic's own str validation.)
TEXT_NOT_STORABLE = (
    "This text contains a hidden character that cannot be saved, often from text "
    "copied out of a PDF. Remove it and try again."
)


def _storable_text(value: str) -> str:
    if "\x00" in value:
        raise PydanticCustomError("text_not_storable", TEXT_NOT_STORABLE)
    return value


DbText = Annotated[str, AfterValidator(_storable_text)]


def db_text(max_length: int):
    """DbText with a length limit, for optional fields. The limit has to come
    before the check: on `DbText | None = Field(max_length=...)` pydantic can
    only apply it generically and reports "items" instead of characters."""
    return Annotated[str, Field(max_length=max_length), AfterValidator(_storable_text)]
