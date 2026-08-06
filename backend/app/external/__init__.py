"""External citation-verification clients (F5.6, V-028): CrossRef,
Semantic Scholar, Open Library, Google Books — each behind its own
per-provider rate limit (`app.ratelimit.RateGovernor`) and all sharing one
cache (`app.external.cache`, keyed by DOI/ISBN/normalized-title) so a
re-run of the same manuscript spends zero network calls (ticket AC).

Only `app/external/` (and `app/checks/citations/`, which never makes a
network call itself) may import `httpx` for these providers — same
single-module discipline `app/llm/` already applies to the Gemini SDK
(CODING.md §2).
"""
