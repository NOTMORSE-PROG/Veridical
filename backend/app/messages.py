"""User-facing message templates — the ONE place wording lives.

Charter rule 3: flags and errors describe possible problems and user-fixable
states, never accusations. Keeping every user-visible string here makes the
wording reviewable in a single file (CODING.md §1).
"""

UNSUPPORTED_FILE_TYPE = "Unsupported file type '{suffix}'. Supported types: {supported}."

FILE_UNREADABLE = (
    "The file could not be read as a document. It may be corrupted or "
    "incomplete — please re-export and upload it again."
)

FILE_ENCRYPTED = (
    "The file is password-protected, so its text cannot be read. Please upload an unlocked copy."
)

FILE_TOO_LARGE = (
    "The file is larger than the {limit_mb} MB upload limit. "
    "Please export a smaller copy (e.g. compress embedded images)."
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
