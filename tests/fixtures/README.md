# Cross-language contract fixtures

BUG-082 found that `ResultsTable.tsx`'s `sourceCaption` (frontend) and
`export.py`'s `_source_caption` (backend PDF) implement the identical rule in
two languages with no test asserting they agree — they diverged silently
(one read `type`, the other `kind`) until a manual audit caught it. This
directory exists so that class of bug gets a mechanical guard instead of
relying on someone noticing.

Each `*.json` file here is a table of `{input, expected_output}` cases for
one duplicated-in-two-languages rule. Both a backend pytest file and a
frontend vitest file load the SAME file and assert their own
implementation against every case — if either implementation drifts from
the other, one of the two suites goes red.

**Rule when adding a new case:** add it here first, run both suites, and
confirm both actually read it (a fixture file neither test loads is not a
guard). Do not duplicate this file's content into a second file per
language — that recreates the exact problem this directory exists to
prevent.

| Fixture | Backend test | Frontend test |
|---|---|---|
| `source_caption_cases.json` | `backend/tests/test_report_export_live.py::test_source_caption_matches_the_shared_contract_fixture` | `frontend/src/report/ResultsTable.contract.test.ts` |
