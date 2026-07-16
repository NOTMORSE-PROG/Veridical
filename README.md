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
# backend/frontend setup instructions land with milestone V0 (see FEATURES.md §8 roadmap)
```

## Commit conventions

- Imperative summary <72 chars, reference tickets as `V-###`
- Code commits must be accompanied by a changelog entry (enforced by a local pre-commit hook running `tools/check_changelog.py`)
