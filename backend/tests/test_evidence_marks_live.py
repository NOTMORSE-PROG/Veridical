"""The evidence page says where its drawn mark is, as fractions of the image,
so a screen can open the page at the excerpt. A position only."""

import base64
import os
import struct

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.report import evidence_service as service
from app.share.service import create_share_link
from tests.test_evidence_reader_live import FIXTURES, source_run
from tests.test_optional_grade_comments_live import factory as factory
from tests.test_optional_grade_comments_live import scratch_url as scratch_url

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="owned Postgres required")


def _image_size(page) -> tuple[int, int]:
    png = base64.b64decode(page.image_data_url.split(",", 1)[1])
    return struct.unpack(">II", png[16:24])


@pytest.mark.asyncio
async def test_a_located_excerpt_comes_back_with_its_mark_position(factory, tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    owner, run, _, criterion, *_ = await source_run(
        factory, FIXTURES / "native.pdf", "A STUDY OF THINGS", "page 1", settings
    )
    async with factory() as session:
        manifest = await service.get_evidence_manifest(
            session, settings, check_run_id=run, instructor_id=owner
        )
    reference = next(ref for ref in manifest.references if ref.criterion_id == criterion)
    async with factory() as session:
        page = await service.get_evidence_page(
            session,
            reference.reference_id,
            manifest.snapshot_id,
            settings,
            check_run_id=run,
            instructor_id=owner,
        )
    assert page.placement.state == "text_layer_match" and page.image_data_url
    assert len(page.marks) >= 1
    width, height = _image_size(page)
    for mark in page.marks:
        assert 0 <= mark.x < 1 and 0 <= mark.y < 1
        assert 0 < mark.width <= 1 and 0 < mark.height <= 1
        assert mark.x + mark.width <= 1.0001 and mark.y + mark.height <= 1.0001
        # A heading on page 1 sits in the top half of the image.
        assert mark.y * height < height / 2
    assert set(page.marks[0].model_dump()) == {"x", "y", "width", "height"}

    async with factory() as session:
        link = await create_share_link(session, run, owner, None)
    async with factory() as session:
        shared = await service.get_evidence_manifest(session, settings, token=link.token)
    shared_ref = next(ref for ref in shared.references if ref.criterion_id == criterion)
    async with factory() as session:
        shared_page = await service.get_evidence_page(
            session, shared_ref.reference_id, shared.snapshot_id, settings, token=link.token
        )
    assert len(shared_page.marks) >= 1


def test_a_mark_is_a_position_and_nothing_else():
    mark = service.EvidenceMarkOut(x=0.1, y=0.2, width=0.3, height=0.05)
    with pytest.raises(ValidationError):
        service.EvidenceMarkOut.model_validate({**mark.model_dump(), "text": "anything"})
    with pytest.raises(ValidationError):
        service.EvidenceMarkOut(x=1.5, y=0.2, width=0.3, height=0.05)
