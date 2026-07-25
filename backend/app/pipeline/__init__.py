"""Check-run orchestration (ENGINEERING.md §4): the state machine that
walks a manuscript+rubric pair through queued -> ingesting -> structural
-> semantic -> integrity -> aggregating -> done | failed, calling V-015
(routing), V-016 (structural), V-017 (semantic), and V-019 (scoring) in
sequence, resumably.
"""
