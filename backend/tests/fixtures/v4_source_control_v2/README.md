# Source validated F5 and F6 control

This separately versioned synthetic input has two source-correct references,
one two-sided t-test example and a separate single integer-item mean example.
The input, recipe, document XML and provenance hashes were frozen before the
first product execution. The DOCX has deterministic package timestamps and
properties; tests consume the frozen binary and check its identity.

The provenance manifest records source retrieval times, response hashes,
publication metadata and independent numerical calculations. The two references
describe their actual subject. The numerical examples do not describe either
publication and are independent of each other. Nano Reviews' 11586 is an article
identifier rather than a physical page count.

Run `pytest tests/test_v4_source_validated_control.py` from `backend`, with
`DATABASE_URL` pointing to a PostgreSQL server where disposable databases may
be created. Each live case creates and drops its own uniquely named migrated
database and local data directory. It requires actual public metadata services.
The test preserves consumed response bytes and full run evidence in its pytest
temporary directory, printing `V4_CONTROL_EVIDENCE` for each executed case.

The metadata assertions require two complete comparisons and actual cold-cache
provider responses. The statistical assertions require real p-value and GRIM
evaluation, not only zero findings. Observation hooks call the production
functions unchanged. Fake LLM orchestration is persisted explicitly and audited;
its claim-support responses do not establish semantic correctness. An empty
test rubric does not demonstrate instructor format parsing or grading.

The original `test_v4_seeded_regression.py`, its source generator, default
selection and zero-flag assertion remain unchanged. Its historical clean input
contains Reyes/2026/capstone metadata attached to a Kucsko/2013/thermometry DOI.
A separate positive detection case identifies that specific mismatch; it does
not convert the original failing expectation into a pass.

Registry observations are dated and may change. A future changed record or
unexpected output must fail for investigation. These controls establish bounded
software behavior, with no representative accuracy, whole-F5, F4/F7, complete
readiness, whole-UAT or permanent unretracted-status claim.

The first execution exposed two harness representation mistakes. The frozen
recipe's `df1=28` names the single scientific t-test degree of freedom;
statcheck 0.0.7 actually stores that quantity in `df2`, leaving `df1` empty.
The test uses an explicit named mapping bound to the independent manifest's
`df=28` oracle. It requires the exact dependency fields, with no either-slot
fallback. The recipe, manifest, DOCX and numerical truth remain unchanged.

Metadata mismatch flags bind reference identity through the result's indexed
ledger, exact saved reference text, comparison evidence and source paragraph
anchor. A reference-order field on the flag itself is not part of that schema.
The first failed run remains recorded separately from corrected-harness runs.
The representation follows the primary dependency's
[degree-of-freedom parser](https://github.com/hplisiecki/statcheck_python/blob/main/src/statcheck/helpers_parse_stats.py)
and [t-distribution computation](https://github.com/hplisiecki/statcheck_python/blob/main/src/statcheck/compute_p.py).
Each run records the installed package version and parser-source hash; mutable
upstream links are explanatory references rather than executable identities.
