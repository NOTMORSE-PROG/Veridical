# VERIDICAL

**An AI-assisted quality and integrity assurance platform for academic research** — it checks a CS/IT capstone manuscript against *whatever required format the Capstone Instructor uploads*, runs four integrity checks on the paper itself (internal agreement, citation integrity, statistical forensics, originality/reuse), and produces a readiness report: **Ready / Conditionally Ready / Not Ready**. The Capstone Instructor always makes the final call — VERIDICAL is a decision-support system, never an auto-approver.

A capstone project of the Technological Institute of the Philippines (BSIT), by Condino · Concepcion · Munoz.

**Instructor-only scope:** instructors upload rubrics and manuscripts, run checks,
manage the Library, and review reports. Student accounts, a student submission
gate/queue, and student resubmission screens are outside the product scope.
Instructors may still share read-only report links; recipients do not get upload,
review, or decision rights.

## What's in this repo

| Path | What it is |
|---|---|
| `FEATURES.md` | The finalized feature specification (modules F1–F9, user flows, tech stack, testing strategy, roadmap) — **start here** |
| `tools/` | Project automation (e.g. `check_changelog.py` commit auditor) |
| `backend/`, `frontend/` | Application code (created as milestones progress) |

## What's *not* in this repo (on purpose)

Internal working documents — planning notes, decision logs, the ticket board (`tickets/`), project context (`context/`), and design sources (`design/`) — are **local-only by policy** and excluded via `.gitignore`. Only public docs and code are committed. If you are a teammate and need these, ask the repo owner directly.

## Stack (free-tier operating plan — usage monitored)

- **Backend:** Python 3.12 + FastAPI · **Frontend:** React + Vite + TailwindCSS
- **AI:** Gemini Flash free tier (grading, rubric decomposition, multimodal extraction)
- **Ingestion:** PyMuPDF + python-docx · **Forensics:** statcheck_python, pysprite, grim_test
- **Data:** PostgreSQL (Neon) + pgvector · private Cloudflare R2 Standard object storage
- **Hosting:** Render (API) + Vercel (web)
- **Citation APIs:** CrossRef (+ Retraction Watch), Semantic Scholar, Open Library, Google Books

The deployment is designed to stay within current free-tier allowances, but
that is an operating target rather than a provider-enforced spending cap.
Cloudflare R2 usage beyond its included Standard allowance is billable, and
budget alerts notify rather than pausing usage.

## Getting started (dev)

```bash
git clone <repo>
cd Veridical
git config commit.template .gitmessage
```

### Backend (FastAPI + Postgres/pgvector)

Requires Docker (or Python 3.12 + [uv](https://docs.astral.sh/uv/) for bare-metal dev).

```bash
docker compose up --build      # Postgres 16 + pgvector, then the API on :8000
curl http://localhost:8000/health
```

`--build` is only needed the first time and after a `pyproject.toml`/`uv.lock`
change — the `api` service bind-mounts the backend source and runs uvicorn
with `--reload`, so a plain `docker compose up` afterward serves current
code, not a stale image.

The compose Postgres is published on host port **5433** (not 5432, which is
frequently occupied by a native Postgres install) — the backend's default
`DATABASE_URL` already points there.

The compose stack defaults to **fake-LLM mode** (`VERIDICAL_FAKE_LLM=1`): the
Gemini client is swapped for a fixture-backed stub, so no API keys and no
quota are needed. To configure anything, `cp .env.example .env` and edit —
every variable is documented there. `.env` is never committed.

Bare-metal dev loop:

```bash
cd backend
uv sync                        # install deps (creates .venv)
uv run pytest                  # tests — no DB or keys required
uv run ruff check .            # lint
uv run uvicorn app.main:app --reload
```

Live-DB tests skip silently (not an error) when `DATABASE_URL` isn't set in
the shell — `uv run pytest` alone undercounts vs. CI's full run. Export it
first (matching the compose Postgres above, or CI's own value) to run the
complete suite:

```bash
DATABASE_URL=postgresql://veridical:veridical@localhost:5433/veridical uv run pytest
```

### Optional local PDF region recovery

The default ingestion path remains PyMuPDF and python-docx. An opt-in, separate
Docling environment can propose tables and figures on PDF pages whose native
table regions remain unresolved. It enriches the saved extraction; it does not
replace original text, clear unresolved reading-order records, assess figure
content, or declare a manuscript ready.

Install the worker separately from the API, using
`tools/extraction-worker-requirements.txt`. The pinned package/model identity
manifest is `backend/app/ingest/data/recovery_models.json`.
`tools/prepare_extraction_models.py --help` describes copying and verifying
those exact artifacts from an existing local Hugging Face cache. It does not
download models. Preserve the included model cards and license notices.
Set `INGEST_RECOVERY_PYTHON` and `INGEST_RECOVERY_MODELS_DIR` to absolute,
operator-owned paths; empty values leave recovery disabled.

The current adapter launches a same-host subprocess, not a remote-worker
service. A separate environment does not add RAM. Run it on a machine with
enough memory for both the API and worker, not in the small hosted API
container. Page count, threads, wall time, sampled process-tree
memory, output size and mapping work are bounded by the `INGEST_RECOVERY_*`
settings. A shared data-directory lock admits one worker at a time. Windows
Job Objects and POSIX process groups manage child-process lifetime; these are
not a sandbox for an untrusted interpreter or protection against arbitrary
filesystem/network access. Model processing uses local artifacts and is
configured offline.

Proposed cells receive original-source coordinates only after exact text and
geometry checks. Ambiguous cells, omitted content, mixed alignment that cannot
be established, and unavailable pages remain explicitly unresolved. A
source-mapped table is not a statistical or semantic validation. Identical
source/model/recipe results can be reused on re-ingestion, but their source
mappings are checked again; reopening a saved report does not run the worker.

### Reuse selection and existing Library indexes

For documents without substantive detected chapter headings, reuse comparison
uses exact non-reference body fragments. Ancillary titles such as acknowledgements
do not, by themselves, define chapters; the configurable list is
`reuse_ancillary_titles` in the heading-pattern JSON. A document with a substantive
chapter retains the chapter-based front-matter policy.

This selection applies when embeddings are computed. Existing Library passage
rows and historical reports are not automatically rebuilt or rewritten by an
upgrade. Old indexes may therefore retain earlier coverage or reference-tagging
limitations; use newly indexed entries when verifying the corrected selection.
Embedding-model identity alone does not version the source-selection recipe.
Similarity results remain non-scoring comparison leads, not originality clearance.

### Recover legacy exact-file identities

Manuscripts uploaded before the content-identity field was introduced may
have no stored SHA-256 digest. If their source objects are still available and
their stored source references resolve safely beneath the configured data
root, an operator can recover those digests in fixed, resumable batches. This
is an offline maintenance command; uploads and web requests never scan old
files.

First choose a fixed inclusive manuscript-id ceiling and preview one batch:

```bash
cd backend
uv run python -m scripts.backfill_content_hashes --through-id 120
```

Review the per-id outcomes, confirm `DATABASE_URL` and object-storage
credentials point to the intended deployment, then repeat with `--apply`.
Continue the forward sweep from the emitted `next_after_id` while
`more_eligible` is true:

```bash
uv run python -m scripts.backfill_content_hashes --through-id 120 --apply
uv run python -m scripts.backfill_content_hashes --through-id 120 --after-id 25 --apply
```

Each invocation is limited by `CONTENT_HASH_BACKFILL_BATCH_SIZE` (or an
explicit `--batch-size`) and the configurable
`CONTENT_HASH_BACKFILL_MAX_BATCH_SIZE` safety ceiling, itself capped by
`CONTENT_HASH_BACKFILL_PROCESS_CEILING`. It updates a row only if its digest is
still absent and it has not been purged. A `missing_object` or
`invalid_source_ref` result is not guessed from the filename or extracted
text: exact byte identity remains unavailable until the source is restored or
the instructor uploads it again.
Rows are processed sequentially and serialized per manuscript with purge. The
current R2 storage seam retrieves one source object into memory before the
digest is updated in configured slices; this bounds work to one object at a
time but is not a streaming network read. The command does not repopulate the
application's local manuscript cache. It is safe to rerun; already recovered
rows are no longer selected. A `partial` batch is still unresolved even if a
later forward batch returns `ok`: record its emitted `retry_after_id`, correct
or restore the source of the failure, and sweep again from that cursor. Do not
claim the fixed ceiling recovered until a complete sweep ends with no partial
outcomes and `more_eligible` false. A batch with only
`post_result_failure: true` is different: its listed manuscript outcomes did
complete, so retain that output as evidence and investigate the command/session
cleanup warning; `retry_after_id` can correctly be `null` in that case.

## Commit conventions

- Imperative summary <72 chars, reference tickets as `V-###`
- Code commits must be accompanied by a changelog entry (enforced by a local pre-commit hook running `tools/check_changelog.py`)

## License

MIT (see `LICENSE`). The statistical-forensics stack (`statcheck_python`)
is GPL-3.0 and used as an ordinary dependency, not vendored — VERIDICAL
runs it as a backend service and never redistributes it or a combined
work, so GPL-3.0's copyleft (which triggers on distribution) doesn't
reach this project's own license choice.
