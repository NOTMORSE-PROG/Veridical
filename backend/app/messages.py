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
