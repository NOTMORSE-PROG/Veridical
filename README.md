# VERIDICAL

**An AI-assisted quality and integrity assurance platform for academic research** — it checks a CS/IT capstone manuscript against *whatever required format the Capstone Instructor uploads*, runs four integrity checks on the paper itself (internal agreement, citation integrity, statistical forensics, originality/reuse), and produces a readiness report: **Ready / Conditionally Ready / Not Ready**. The Capstone Instructor always makes the final call — VERIDICAL is a decision-support system, never an auto-approver.

A capstone project of the Technological Institute of the Philippines (BSIT), by Condino · Concepcion · Munoz.

## What's in this repo

| Path | What it is |
|---|---|
| `FEATURES.md` | The finalized feature specification (modules F1–F9, user flows, tech stack, testing strategy, roadmap) — **start here** |
| `tools/` | Project automation (e.g. `check_changelog.py` commit auditor) |
| `backend/`, `frontend/` | Application code (created as milestones progress) |

## What's *not* in this repo (on purpose)

Internal working documents — planning notes, decision logs, the ticket board (`tickets/`), project context (`context/`), and design sources (`design/`) — are **local-only by policy** and excluded via `.gitignore`. Only public docs and code are committed. If you are a teammate and need these, ask the repo owner directly.

## Stack (all free-tier — total cost ₱0.00)

- **Backend:** Python 3.12 + FastAPI · **Frontend:** React + Vite + TailwindCSS
- **AI:** Gemini Flash free tier (grading, rubric decomposition, multimodal extraction)
- **Ingestion:** PyMuPDF + python-docx · **Forensics:** statcheck_python, pysprite, grim_test
- **DB:** PostgreSQL (Neon) + pgvector · **Hosting:** Render (API) + Vercel (web)
- **Citation APIs:** CrossRef (+ Retraction Watch), Semantic Scholar, Open Library, Google Books

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

## Commit conventions

- Imperative summary <72 chars, reference tickets as `V-###`
- Code commits must be accompanied by a changelog entry (enforced by a local pre-commit hook running `tools/check_changelog.py`)

## License

MIT (see `LICENSE`). The statistical-forensics stack (`statcheck_python`)
is GPL-3.0 and used as an ordinary dependency, not vendored — VERIDICAL
runs it as a backend service and never redistributes it or a combined
work, so GPL-3.0's copyleft (which triggers on distribution) doesn't
reach this project's own license choice.
