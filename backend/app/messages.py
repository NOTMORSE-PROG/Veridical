"""User-facing message templates — general system/ingest/parse errors that
don't belong to one specific check (BUG-079 correction: this is NOT the one
place all user-visible wording lives, despite what this docstring used to
claim — a real audit found ~18 more strings living as `*_WORDING` constants
in each check module, next to the logic that produces them, e.g.
`app/checks/citations/verify.py`'s `RETRACTED_WORDING`/
`UNVERIFIABLE_NOT_FOUND_WORDING`).

Charter rule 3: flags and errors describe possible problems and user-fixable
states, never accusations. The real, mechanically-enforced review surface
for that rule is `tools/check_dashes.py` (BUG-047, shipped) — an AST-parsed
pre-commit gate covering every string literal in `backend/app/**` and
`frontend/src/**`, docstrings/comments excluded, regardless of which file
it lives in. Physically consolidating every string into this one file was
the original (wrong) plan for achieving that; the gate makes location
irrelevant, so it was never finished and shouldn't be — see BUG-079's own
ticket for the full account of why this docstring was corrected instead.
"""

UNSUPPORTED_FILE_TYPE = "Unsupported file type '{suffix}'. Supported types: {supported}."

FILE_UNREADABLE = (
    "The file could not be read as a document. It may be corrupted or "
    "incomplete. Please re-export and upload it again."
)

FILE_ENCRYPTED = (
    "The file is password-protected, so its text cannot be read. Please upload an unlocked copy."
)

FILE_TOO_LARGE = (
    "The file is larger than the {limit_mb} MB upload limit. "
    "Please export a smaller copy (e.g. compress embedded images)."
)

DOCX_EXPANDS_TOO_LARGE = (
    "This document's internal content is much larger than its file size "
    "suggests and can't be safely processed. Please re-export a clean copy "
    "from Word."
)

IMAGE_ONLY_NOTE = (
    "This document contains little or no selectable text (it may be a "
    "scanned copy). Checks that need the text will be limited."
)

# --- criterion routing (F3.1, V-015) ----------------------------------------

STRUCTURAL_RULE_UNIMPLEMENTED = (
    "No implemented structural rule matches this criterion yet, so it will "
    "be graded semantically instead."
)

CRITERION_TYPE_UNRECOGNIZED = (
    "This criterion's type could not be recognized, so it could not be "
    "checked automatically. It needs manual review."
)

# BUG-092: a real, defense-day requirement (e.g. bringing bound copies,
# answering questions live) that no reading of the manuscript could ever
# settle -- distinct from CRITERION_TYPE_UNRECOGNIZED, which is a defect
# (an unroutable type). This one is a correct, honest routing decision.
CRITERION_NOT_ASSESSABLE_FROM_DOCUMENT = (
    "VERIDICAL cannot check this from the document. It describes something "
    "you observe directly, such as a defense-day behavior or a physical "
    "requirement, not a property of the manuscript itself."
)
