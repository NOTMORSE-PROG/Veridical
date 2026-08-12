"""V-028: CrossRef client — recorded responses (real shapes captured live
2026-08-06, see crossref.py's module docstring), no network in CI."""

import httpx

from app.config import get_settings
from app.external import crossref

# Real "update-to" shape, verified live 2026-08-06 against
# https://api.crossref.org/v1/works?filter=update-type:retraction
_RETRACTED_WORK = {
    "message": {
        "DOI": "10.1016/j.example.2020.103768",
        "title": ["RETRACTED: Some paper title"],
        "URL": "https://doi.org/10.1016/j.example.2020.103768",
        "update-to": [
            {
                "DOI": "10.1016/j.example.2020.103768",
                "type": "retraction",
                "label": "Retraction",
                "source": "publisher",
            }
        ],
    }
}

_CORRECTED_WORK = {
    "message": {
        "DOI": "10.1097/example.0000000000005352",
        "title": ["A perfectly fine paper"],
        "URL": "https://doi.org/10.1097/example.0000000000005352",
        "update-to": [
            {
                "DOI": "10.1097/example.0000000000005224",
                "type": "correction",
                "label": "Correction",
                "source": "publisher",
            }
        ],
    }
}

# BUG-034: the REAL 1998 Wakefield response shape, captured live
# 2026-08-13 (`curl https://api.crossref.org/works/10.1016/S0140-6736(97)11096-0`)
# — `update-to` is null; the retraction lives entirely under `updated-by`
# (CrossRef's Retraction Watch integration, `source: "retraction-watch"`
# on every entry). Its title ALSO happens to carry a "RETRACTED:" prefix
# (Elsevier/Lancet's own convention), which is why this exact DOI was
# never actually exercising the `updated-by` path before this fix — the
# title fallback silently covered for it. Kept here as the real-world
# case; `_TITLE_FALLBACK_ONLY` below isolates the fallback path alone.
_REAL_WAKEFIELD_RETRACTION = {
    "message": {
        "DOI": "10.1016/S0140-6736(97)11096-0",
        "title": [
            "RETRACTED: Ileal-lymphoid-nodular hyperplasia, non-specific colitis, "
            "and pervasive developmental disorder in children"
        ],
        "URL": "https://doi.org/10.1016/S0140-6736(97)11096-0",
        "updated-by": [
            {
                "DOI": "10.1016/s0140-6736(04)15715-2",
                "type": "correction",
                "label": "Correction",
                "source": "retraction-watch",
            },
            {
                "DOI": "10.1016/s0140-6736(10)60175-4",
                "type": "retraction",
                "label": "Retraction",
                "source": "retraction-watch",
            },
        ],
    }
}

# Isolates the title-prefix fallback: no update-to, no updated-by at all —
# the only pre-2023 shape that genuinely has nothing structured to read.
_TITLE_FALLBACK_ONLY = {
    "message": {
        "DOI": "10.9999/no-structured-metadata",
        "title": ["RETRACTED: A paper with no structured relation data"],
        "URL": "https://doi.org/10.9999/no-structured-metadata",
    }
}

# backend-critic finding (BUG-034 review): a real DOI where BOTH fields
# carry retraction data for the SAME underlying event, from different
# sources — captured live 2026-08-13,
# `curl https://api.crossref.org/works/10.1016/j.micpro.2021.104006`.
# `update-to` has a publisher-sourced retraction; `updated-by` has that
# SAME publisher retraction duplicated, an erratum, AND a distinct
# retraction-watch-sourced retraction (different DOI) with a LATER date.
# Which detail string wins must be an explicit source-priority choice,
# not an accident of concatenation order.
_DUAL_SOURCE_RETRACTION = {
    "message": {
        "DOI": "10.1016/j.micpro.2021.104006",
        "title": ["A paper retracted, then re-flagged by Retraction Watch"],
        "URL": "https://doi.org/10.1016/j.micpro.2021.104006",
        "update-to": [
            {
                "DOI": "10.1016/j.micpro.2021.104006",
                "type": "retraction",
                "label": "Retraction",
                "source": "publisher",
            }
        ],
        # Order deliberately does NOT match the real live capture (there,
        # retraction-watch happens to come last) — retraction-watch is
        # placed FIRST here specifically so this fixture can tell apart
        # "explicit source priority" from "naive last-match-wins": a
        # naive implementation would pick the trailing publisher entry
        # and get this wrong.
        "updated-by": [
            {
                "DOI": "10.1016/j.micpro.2023.104901",
                "type": "retraction",
                "label": "Retraction",
                "source": "retraction-watch",
            },
            {
                "DOI": "10.1016/j.micpro.2023.104901",
                "type": "erratum",
                "label": "Erratum",
                "source": "publisher",
            },
            {
                "DOI": "10.1016/j.micpro.2021.104006",
                "type": "retraction",
                "label": "Retraction",
                "source": "publisher",
            },
        ],
    }
}

# A retraction with NO title prefix at all and its only structured data
# under updated-by — the exact case BUG-034 found was silently missed
# before the fix (update-to empty, title doesn't say RETRACTED).
_UPDATED_BY_ONLY_NO_TITLE_PREFIX = {
    "message": {
        "DOI": "10.9999/retracted-no-title-prefix",
        "title": ["A paper whose title was never updated to say RETRACTED"],
        "URL": "https://doi.org/10.9999/retracted-no-title-prefix",
        "updated-by": [
            {
                "DOI": "10.9999/the-retraction-notice",
                "type": "retraction",
                "label": "Retraction",
                "source": "retraction-watch",
            }
        ],
    }
}

_CLEAN_WORK = {
    "message": {
        "DOI": "10.1234/clean",
        "title": ["An uncontroversial paper"],
        "URL": "https://doi.org/10.1234/clean",
    }
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_lookup_doi_found_and_clean():
    def handler(request):
        return httpx.Response(200, json=_CLEAN_WORK)

    async with _client(handler) as client:
        result = await crossref.lookup_doi(client, "10.1234/clean", settings=get_settings())
    assert result.found
    assert result.title == "An uncontroversial paper"
    assert not result.retracted
    assert not result.is_correction


async def test_lookup_doi_not_found():
    def handler(request):
        return httpx.Response(404)

    async with _client(handler) as client:
        result = await crossref.lookup_doi(client, "10.9999/nope", settings=get_settings())
    assert not result.found
    assert result.provider == "crossref"


async def test_retraction_detected_from_update_to():
    def handler(request):
        return httpx.Response(200, json=_RETRACTED_WORK)

    async with _client(handler) as client:
        result = await crossref.lookup_doi(client, "x", settings=get_settings())
    assert result.retracted
    assert "Retraction" in result.retraction_detail
    assert not result.is_correction


async def test_correction_is_not_a_retraction():
    """Ticket edge case: 'a correction is info-severity not high' — the two
    must never be conflated."""

    def handler(request):
        return httpx.Response(200, json=_CORRECTED_WORK)

    async with _client(handler) as client:
        result = await crossref.lookup_doi(client, "x", settings=get_settings())
    assert result.is_correction
    assert not result.retracted


async def test_old_retraction_without_structured_metadata_still_caught():
    """A real gap found live: pre-2023 retractions can carry an empty
    update-to/relation AND no updated-by — the title-prefix fallback must
    still catch them."""

    def handler(request):
        return httpx.Response(200, json=_TITLE_FALLBACK_ONLY)

    async with _client(handler) as client:
        result = await crossref.lookup_doi(client, "x", settings=get_settings())
    assert result.retracted
    assert "RETRACTED" in result.retraction_detail


async def test_real_wakefield_retraction_detected_via_updated_by():
    """BUG-034: the real Wakefield MMR paper's structured retraction data
    lives ENTIRELY under `updated-by` (`update-to` is null) — before the
    fix, `_parse_work` never read `updated-by` at all, so this exact DOI
    was only ever caught by its ALSO-present title prefix. This asserts
    the structured path itself works, independent of the title."""

    def handler(request):
        return httpx.Response(200, json=_REAL_WAKEFIELD_RETRACTION)

    async with _client(handler) as client:
        result = await crossref.lookup_doi(
            client, "10.1016/S0140-6736(97)11096-0", settings=get_settings()
        )
    assert result.retracted
    assert "Retraction" in result.retraction_detail
    assert "retraction-watch" in result.retraction_detail


async def test_retraction_watch_source_wins_when_both_fields_populated():
    """backend-critic finding (BUG-034 review): a real DOI can have
    retraction data in BOTH `update-to` and `updated-by`, from different
    sources. The shown detail string must explicitly prefer
    retraction-watch (CrossRef's actively-maintained integration, and
    also the LATER/most-current entry in this real example) rather than
    whichever field happens to be processed last."""

    def handler(request):
        return httpx.Response(200, json=_DUAL_SOURCE_RETRACTION)

    async with _client(handler) as client:
        result = await crossref.lookup_doi(
            client, "10.1016/j.micpro.2021.104006", settings=get_settings()
        )
    assert result.retracted
    assert "retraction-watch" in result.retraction_detail
    assert result.is_correction  # the erratum entry is still recorded


async def test_retraction_under_updated_by_with_no_title_prefix_is_not_missed():
    """The actual exploit scenario BUG-034 describes: a retraction whose
    only signal is `updated-by`, with NO 'RETRACTED:' title prefix to
    fall back on. Before the fix this was a silent false negative —
    `update-to` empty, title fallback doesn't match, `updated-by` never
    read."""

    def handler(request):
        return httpx.Response(200, json=_UPDATED_BY_ONLY_NO_TITLE_PREFIX)

    async with _client(handler) as client:
        result = await crossref.lookup_doi(client, "x", settings=get_settings())
    assert result.retracted
    assert "Retraction" in result.retraction_detail


async def test_search_by_title_uses_first_result():
    def handler(request):
        assert "query.bibliographic" in str(request.url)
        return httpx.Response(200, json={"message": {"items": [_CLEAN_WORK["message"]]}})

    async with _client(handler) as client:
        result = await crossref.search_by_title(
            client, "An uncontroversial paper", settings=get_settings()
        )
    assert result.found
    assert result.title == "An uncontroversial paper"


async def test_search_by_title_no_results():
    def handler(request):
        return httpx.Response(200, json={"message": {"items": []}})

    async with _client(handler) as client:
        result = await crossref.search_by_title(
            client, "nothing matches this", settings=get_settings()
        )
    assert not result.found
