"""Pairs the agreement check could not decide reach the shared evidence as the
manuscript's own pages, with nothing but their quote and location."""

import os

import pytest
from sqlalchemy import select

from app.config import Settings
from app.models.enums import CheckKind
from app.models.run import CheckResult
from app.report import evidence_service as service
from app.share.service import create_share_link
from tests.test_evidence_reader_live import FIXTURES, source_run
from tests.test_optional_grade_comments_live import factory as factory
from tests.test_optional_grade_comments_live import scratch_url as scratch_url

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="owned Postgres required")


@pytest.mark.asyncio
async def test_shared_manifest_places_an_undecided_pair_on_its_own_page(factory, tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    owner, run, *_ = await source_run(
        factory, FIXTURES / "native.pdf", "A STUDY OF THINGS", "page 1", settings
    )
    async with factory() as session:
        agreement = await session.scalar(
            select(CheckResult).where(
                CheckResult.check_run_id == run,
                CheckResult.kind == CheckKind.internal_agreement,
            )
        )
        agreement.detail = {
            **(agreement.detail or {}),
            "undetermined": {
                "count": 1,
                "compared_count": 3,
                "pairs": [
                    {
                        "intent_text": "A STUDY OF THINGS",
                        "intent_anchor": "page 1",
                        "outcome_text": "A STUDY OF THINGS",
                        "outcome_anchor": "page 1",
                        "reason": "PRIVATE_UNDECIDED_REASON",
                    }
                ],
            },
        }
        await session.commit()
    async with factory() as session:
        link = await create_share_link(session, run, owner, None)
    async with factory() as session:
        manifest = await service.get_evidence_manifest(session, settings, token=link.token)
    undecided = [ref for ref in manifest.references if ref.role.startswith("undecided_")]
    assert [(ref.role, ref.index, ref.flag_id, ref.criterion_id) for ref in undecided] == [
        ("undecided_intent", 0, None, None),
        ("undecided_outcome", 0, None, None),
    ]
    assert "PRIVATE_UNDECIDED_REASON" not in manifest.model_dump_json()
    async with factory() as session:
        page = await service.get_evidence_page(
            session, undecided[0].reference_id, manifest.snapshot_id, settings, token=link.token
        )
    assert page.placement.page == 1 and page.image_data_url is not None
