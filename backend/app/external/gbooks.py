"""Google Books client (F5.6): book existence fallback when Open Library
has no match. Always `content_checkable=False` — same reasoning as
`openlibrary.py`.

**LIVE-TESTED 2026-08-06 (RESEARCH.md §2), contradicts the "no key needed"
assumption both the ticket and the original research note carried**: an
unauthenticated request got a real 429 with `quota_limit_value: '0'` —
keyless access is effectively non-functional today, not just rate-limited.
This client still WORKS without a key (never raises just because one is
missing) — it degrades the same way any other `api_down` source does, via
the normal retry→`ApiDownError` path — but a caller (V-029) should expect
the keyless path to fail immediately in practice and fall back to
`openlibrary.py` for the actual coverage, per the ticket's own edge case.
`google_books_daily_quota` gates a LOCAL counter once a real key exists,
independent of whatever Google's per-key quota actually is.
"""

from datetime import UTC, datetime

from app.config import Settings
from app.external.http import get_json
from app.external.schemas import VerificationResult
from app.rate_governor import RateGovernor

_governor: RateGovernor | None = None
# In-process daily counter (module-level, like the pattern this ticket's
# own edge case describes) — reset when the calendar day (UTC) rolls over.
# Not DB-persisted: unlike the LLM quota (which must survive a Render
# restart, V-009), losing this counter on a redeploy just means a few
# extra calls against a provider that's free either way, not a real cost.
_quota_state: dict[str, int | str] = {"day": "", "used": 0}


def _get_governor(settings: Settings) -> RateGovernor:
    global _governor
    if _governor is None:
        # No documented per-second rate for Google Books; a conservative
        # 2 rps avoids tripping additional per-second throttling on top of
        # the daily quota this client already tracks itself.
        _governor = RateGovernor(rpm=120)
    return _governor


def _quota_available(settings: Settings, *, now: datetime | None = None) -> bool:
    today = (now or datetime.now(UTC)).date().isoformat()
    if _quota_state["day"] != today:
        _quota_state["day"] = today
        _quota_state["used"] = 0
    return int(_quota_state["used"]) < settings.google_books_daily_quota


def _record_call(*, now: datetime | None = None) -> None:
    today = (now or datetime.now(UTC)).date().isoformat()
    if _quota_state["day"] != today:
        _quota_state["day"] = today
        _quota_state["used"] = 0
    _quota_state["used"] = int(_quota_state["used"]) + 1


def quota_exhausted_today(settings: Settings) -> bool:
    """V-029 checks this BEFORE calling, to skip straight to Open Library
    instead of spending a doomed retry loop on a request the local counter
    already knows is over budget."""
    return not _quota_available(settings)


async def search_title(client, title: str, *, settings: Settings) -> VerificationResult:
    if not _quota_available(settings):
        return VerificationResult(
            found=False, provider="gbooks", raw={"reason": "daily_quota_exhausted"}
        )
    governor = _get_governor(settings)
    params = {"q": f"intitle:{title}"}
    if settings.google_books_api_key:
        params["key"] = settings.google_books_api_key
    data = await get_json(
        client,
        "https://www.googleapis.com/books/v1/volumes",
        provider="gbooks",
        governor=governor,
        max_retries=settings.external_max_retries,
        retry_base_seconds=settings.external_retry_base_seconds,
        params=params,
    )
    _record_call()
    items = (data or {}).get("items") or []
    if not items:
        return VerificationResult(found=False, provider="gbooks")
    info = items[0].get("volumeInfo") or {}
    return VerificationResult(
        found=True,
        provider="gbooks",
        title=info.get("title"),
        content_checkable=False,
        url=info.get("infoLink"),
        raw=items[0],
    )
