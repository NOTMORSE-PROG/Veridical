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

MANUSCRIPT_SOURCE_MISSING = (
    "VERIDICAL could not find this manuscript's stored source file. "
    "It is not recorded as deliberately removed. Upload the manuscript "
    "again to create a new reviewable copy."
)

MANUSCRIPT_PREPARATION_FAILED = (
    "VERIDICAL could not finish preparing this manuscript. Try uploading it again."
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

PDF_TOO_MANY_PAGES = (
    "This PDF has {page_count} pages, more than the {limit} page limit. "
    "Please split it or upload a shorter version."
)

EXTRACTION_TIMED_OUT = (
    "This document took too long to process and could not be checked. "
    "Please try a shorter or simpler file."
)

UNEXPECTED_PIPELINE_ERROR = (
    "This check stopped because of an unexpected system error. Run it again. "
    "If the problem continues, contact the VERIDICAL team."
)

IMAGE_ONLY_NOTE = (
    "This document contains little or no selectable text (it may be a "
    "scanned copy). Checks that need the text will be limited."
)

# --- Library submissions (V-077, D-028) -------------------------------------

LIBRARY_ADDED = (
    "Added to the Library. Manuscripts checked from now on will be compared against "
    "it. Earlier reports are not updated."
)

LIBRARY_EXACT_DUPLICATE = "This exact file is already in your Library, so nothing new was added."

LIBRARY_EXACT_DUPLICATE_PROCESSING = (
    "This exact file is already being added to your Library, so nothing new was added."
)

LIBRARY_EXACT_DUPLICATE_WAITING = (
    "This exact file is already among your uploads waiting for a duplicate "
    "decision, so nothing new was added."
)

LIBRARY_EXACT_DUPLICATE_FAILED = (
    "VERIDICAL could not read this exact file before, so it was not stored again."
)

LIBRARY_REMOVED_WHILE_READING = (
    "This upload was removed before VERIDICAL finished reading it, so it was not added."
)

LIBRARY_STILL_READING = "VERIDICAL is still reading this file. Try again in a few minutes."

LIBRARY_STORAGE_FAILED = (
    "VERIDICAL could not store this file right now. Nothing was added. Try again."
)

LIBRARY_POSSIBLE_DUPLICATE = (
    "This file's text is very similar to a manuscript already in your Library "
    "(it may be the same manuscript in another file, or a revised version). It "
    "has not been added yet. Compare them, then choose Add anyway or Skip."
)

LIBRARY_NO_READABLE_TEXT = (
    "VERIDICAL could not find readable text in this file (it may be a scanned "
    "copy), so it cannot be compared against other manuscripts. It was not "
    "added to the Library."
)

LIBRARY_FILE_UNREADABLE = (
    "VERIDICAL could not read this file, so it was not added to the Library. "
    "Please re-export it and try again."
)

LIBRARY_NOT_AWAITING_DECISION = "This manuscript is not waiting for a duplicate decision."

LIBRARY_SOURCE_MISSING = (
    "VERIDICAL could not find this manuscript's stored text, so it cannot be "
    "added. Remove it and add the file again."
)

LIBRARY_UNKNOWN_PROGRAM = "No program named {program}."

# V-078: a stored file is read by a check while it runs, so it is never
# deleted underneath one. Recoverable: wait, or stop the check.
STORED_FILE_CHECK_RUNNING = (
    "A check is still running on this manuscript. Wait for it to finish, "
    "or stop it from its progress page, then delete the stored file."
)
STORED_FILE_PREPARATION_RUNNING = (
    "This upload's preparation is still running. Wait for it to finish "
    "before deleting the stored file."
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
