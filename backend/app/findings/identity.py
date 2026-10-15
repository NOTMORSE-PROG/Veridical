"""One canonical identity for scoring, report, export, review, and audit."""

import hashlib
import json
import unicodedata

IDENTITY_VERSION = 2
FINDING_GROUP_FIELD = "finding_group"


def _text(value: str | None) -> str | None:
    return unicodedata.normalize("NFC", value) if value is not None else None


def finding_group_fingerprint(namespace: str, *parts: str | int) -> str:
    """Build an opaque, engine-owned fingerprint for rows known to be one fact.

    The fingerprint is persisted with each flag.  Similar-looking prose is not
    enough to group findings: an engine must opt in by assigning the same
    fingerprint to rows it deliberately emitted as multiple locations for one
    underlying fact.
    """
    normalized_parts = tuple(_text(part) if isinstance(part, str) else part for part in parts)
    encoded = json.dumps(
        (1, _text(namespace), normalized_parts),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"g1_{hashlib.sha256(encoded).hexdigest()}"
