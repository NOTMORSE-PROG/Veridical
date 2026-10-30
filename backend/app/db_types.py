"""Request value types that must fit the database's columns.

Kept free of database imports on purpose: request schemas use these types,
and the native-compute helper loads those schemas with only its own small set
of packages installed, so neither asyncpg nor SQLAlchemy is there to import.
"""

from typing import Annotated

from app.config import BIGINT_MAX, BIGINT_MIN

__all__ = ["BIGINT_MAX", "BIGINT_MIN", "DbInt"]

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
