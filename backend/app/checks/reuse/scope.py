"""Project recorded retrieval limits without inventing historical coverage."""

from app.checks.reuse.literal import NORMALIZATION


def literal_unassessed_count(value) -> int | None:
    if not isinstance(value, dict) or value.get("method") != NORMALIZATION:
        return None
    entries = value.get("unassessed")
    if not isinstance(entries, list):
        return None
    indexes = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        index = entry.get("passage_index")
        if type(index) is not int or index < 0 or not isinstance(entry.get("reason"), str):
            return None
        indexes.add(index)
    return len(indexes)
