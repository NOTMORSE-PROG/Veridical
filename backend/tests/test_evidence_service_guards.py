"""Evidence boundary controls; stubbed SQL/native work, no PDF or external I/O."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as Row

import pytest

from app.config import Settings
from app.errors import ConflictError, GoneError, NotFoundError, RateLimitedError
from app.report import evidence_service as service
from app.report.evidence_images import evidence_image_admission
from app.report.evidence_placement import EvidencePlacementOut, ReferencePageImage
from app.report.evidence_references import build_evidence_reference_snapshot
from app.report.evidence_source import BoundEvidenceSource
from app.storage.access import read_source


def assessment(audience="private", *, mode="real", identity="a" * 64):
    report = Row(
        check_run_id=7,
        manuscript_id=8,
        assessment_revision=3,
        results=[
            Row(
                check_result_id=11,
                criterion_id=21,
                anchor=None,
                evidence=[Row(quote="Recorded full quotation.", anchor="page 2")],
            )
        ],
    )
    references = build_evidence_reference_snapshot(
        report, (), expected_flag_ids=(), source_sha256="b" * 64, audience=audience
    )
    return service.RecordedEvidenceAssessment(report, references, mode, identity)


class StubSession:
    def __init__(self, scalar_values=()):
        self.scalar_values = iter(scalar_values)
        self.refreshes = []

    async def scalar(self, statement):
        assert statement.get_execution_options().get("populate_existing") is True
        return next(self.scalar_values)

    async def get(self, model, identity):
        return Row(
            id=identity,
            content_hash="b" * 64,
            file_ref="PRIVATE_STORAGE_KEY",
            purged_at=None,
            section_tree={"source": "none", "nodes": []},
        )

    async def refresh(self, value, **kwargs):
        self.refreshes.append(kwargs)

    async def execute(self, statement):
        return None


def install_bound_stubs(monkeypatch, current, **changes):
    calls = install_snapshot_stubs(monkeypatch, current, **changes)

    def prepared(file, run_id, manuscript_id, digest, tree, settings):
        calls.append("bound-source")
        assert digest == "b" * 64
        return BoundEvidenceSource(
            run_id, manuscript_id, "pdf", "d" * 64, digest, pdf_bytes=b"pinned PDF bytes"
        )

    def located(bound, reference, tree, settings):
        calls.append("bound-location")
        assert bound.source_identity == "d" * 64
        return EvidencePlacementOut(reference_id=reference.reference_id, state="page_only", page=2)

    monkeypatch.setattr(service, "prepare_bound_evidence_source", prepared)
    monkeypatch.setattr(service, "locate_bound_evidence", located)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", ["snapshot", "reference"])
async def test_bound_location_rejects_stale_or_foreign_reference_before_source(monkeypatch, wrong):
    current = assessment()
    calls = install_bound_stubs(monkeypatch, current)
    with pytest.raises(ConflictError if wrong == "snapshot" else NotFoundError):
        await service.get_private_evidence_location(
            StubSession(),
            7,
            9,
            "c" * 64 if wrong == "reference" else current.references.references[0].reference_id,
            "c" * 64 if wrong == "snapshot" else current.snapshot_id,
            Settings(_env_file=None),
        )
    assert calls == ["authorize", "snapshot"]


@pytest.mark.asyncio
async def test_bound_location_returns_only_fresh_location_and_content_identity(monkeypatch):
    current = assessment()
    calls = install_bound_stubs(monkeypatch, current)
    session = StubSession()
    out = await service.get_private_evidence_location(
        session,
        7,
        9,
        current.references.references[0].reference_id,
        current.snapshot_id,
        Settings(_env_file=None),
    )
    assert out.source_identity == "d" * 64 and out.source_format == "pdf"
    assert out.snapshot_id == current.snapshot_id and out.placement.page == 2
    assert "b" * 64 not in out.model_dump_json() and "pdf_bytes" not in out.model_dump_json()
    assert session.refreshes == [{"with_for_update": {"read": True}}]
    assert calls == [
        "authorize",
        "snapshot",
        "bound-source",
        "bound-location",
        "authorize",
        "snapshot",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["authority", "assessment"])
async def test_bound_location_withheld_after_authority_or_assessment_change(monkeypatch, change):
    current = assessment()
    calls = install_bound_stubs(
        monkeypatch,
        current,
        deny_after=change == "authority",
        after=assessment(identity="c" * 64) if change == "assessment" else None,
    )
    with pytest.raises(GoneError if change == "authority" else ConflictError):
        await service.get_private_evidence_location(
            StubSession(),
            7,
            9,
            current.references.references[0].reference_id,
            current.snapshot_id,
            Settings(_env_file=None),
        )
    assert "bound-location" in calls


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["d" * 64, "e" * 64])
async def test_bound_reader_identity_is_checked_without_requiring_old_grade_epoch(
    monkeypatch, identity
):
    calls = install_bound_stubs(monkeypatch, assessment())
    if identity != "d" * 64:
        with pytest.raises(ConflictError, match="source copy changed"):
            await service.get_private_evidence_reader_source(
                StubSession(),
                7,
                9,
                identity,
                Settings(_env_file=None),
            )
    else:
        out = await service.get_private_evidence_reader_source(
            StubSession(),
            7,
            9,
            identity,
            Settings(_env_file=None),
        )
        assert out.pdf_bytes == b"pinned PDF bytes"
    assert calls == (
        ["authorize", "bound-source", "authorize"]
        if identity == "d" * 64
        else ["authorize", "bound-source"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["d" * 64, "e" * 64])
async def test_bound_image_rejects_wrong_reader_before_consuming_extraction(monkeypatch, identity):
    calls = install_bound_stubs(monkeypatch, assessment())

    def image(bound, index, settings):
        calls.append("image")
        assert bound.source_identity == identity and index == 0
        return b"verified source image", "image/png"

    monkeypatch.setattr(service, "read_bound_docx_image", image)
    if identity == "d" * 64:
        out = await service.get_private_evidence_source_image(
            StubSession(), 7, 9, identity, 0, Settings(_env_file=None)
        )
        assert out == (b"verified source image", "image/png")
        assert calls == ["authorize", "bound-source", "image", "authorize"]
    else:
        with pytest.raises(ConflictError):
            await service.get_private_evidence_source_image(
                StubSession(), 7, 9, identity, 0, Settings(_env_file=None)
            )
        assert calls == ["authorize", "bound-source"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["prepare", "locate", "image"])
async def test_bound_source_service_drains_both_native_stages_on_repeated_cancel(
    monkeypatch, stage
):
    current = assessment()
    calls = install_bound_stubs(monkeypatch, current)
    settings = Settings(_env_file=None, evidence_source_slots=1)
    entered, release = threading.Event(), threading.Event()
    if stage == "image":
        monkeypatch.setattr(service, "read_bound_docx_image", lambda *args: (b"image", "image/png"))
    target = {
        "prepare": "prepare_bound_evidence_source",
        "locate": "locate_bound_evidence",
        "image": "read_bound_docx_image",
    }[stage]
    original = getattr(service, target)

    def blocked(*args):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args)

    monkeypatch.setattr(service, target, blocked)
    session = StubSession()
    task = asyncio.create_task(
        service.get_private_evidence_source_image(session, 7, 9, "d" * 64, 0, settings)
        if stage == "image"
        else service.get_private_evidence_location(
            session,
            7,
            9,
            current.references.references[0].reference_id,
            current.snapshot_id,
            settings,
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert session.refreshes == [{"with_for_update": {"read": True}}]
        with pytest.raises(RateLimitedError), evidence_image_admission(settings):
            pass
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls.count("authorize") == 1
    with evidence_image_admission(settings):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authority",
    [
        {},
        {"token": "secret", "instructor_id": 9},
        {"token": "secret", "check_run_id": 7},
        {"check_run_id": 7},
        {"instructor_id": 9},
    ],
)
async def test_ambiguous_or_missing_authority_is_rejected_before_query(authority):
    with pytest.raises(ValueError):
        await service.authorize_evidence_run(StubSession(), **authority)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values,error",
    [
        ([None], NotFoundError),
        ([Row(revoked_at=datetime.now(UTC), expires_at=None)], GoneError),
        ([Row(revoked_at=None, expires_at=datetime.now(UTC) - timedelta(seconds=1))], GoneError),
        ([Row(revoked_at=None, expires_at=None, check_run_id=7), None], NotFoundError),
    ],
)
async def test_public_missing_revoked_expired_and_missing_run_fail_closed(values, error):
    with pytest.raises(error):
        await service.authorize_evidence_run(StubSession(values), token="local-token")


def install_snapshot_stubs(monkeypatch, current, *, after=None, deny_after=False):
    calls = []

    async def authorized(session, **authority):
        calls.append("authorize")
        if deny_after and calls.count("authorize") > 1:
            raise GoneError("This link has been turned off by the instructor.")
        return Row(id=7, manuscript_id=8)

    async def recorded(session, run, audience, settings):
        assert audience == current.references.audience
        calls.append("snapshot")
        return after if after is not None and calls.count("snapshot") > 1 else current

    def rendered(*args, **kwargs):
        calls.append("native")
        ref = args[1]
        return ReferencePageImage(
            EvidencePlacementOut(reference_id=ref.reference_id, state="page_only", page=2), b"PNG"
        )

    monkeypatch.setattr(service, "authorize_evidence_run", authorized)
    monkeypatch.setattr(service, "recorded_evidence_assessment", recorded)
    monkeypatch.setattr(service, "render_recorded_evidence_image", rendered)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", ["snapshot", "reference"])
async def test_stale_snapshot_and_nonmember_cannot_trigger_source_work(monkeypatch, wrong):
    current = assessment()
    calls = install_snapshot_stubs(monkeypatch, current)
    with pytest.raises(ConflictError if wrong == "snapshot" else NotFoundError):
        await service.get_evidence_page(
            StubSession(),
            "c" * 64 if wrong == "reference" else current.references.references[0].reference_id,
            "c" * 64 if wrong == "snapshot" else current.snapshot_id,
            Settings(_env_file=None),
            check_run_id=7,
            instructor_id=9,
        )
    assert calls == ["authorize", "snapshot"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["token", "snapshot"])
async def test_completed_native_result_is_withheld_after_authority_or_epoch_change(
    monkeypatch, change
):
    current = assessment("public")
    calls = install_snapshot_stubs(
        monkeypatch,
        current,
        deny_after=change == "token",
        after=assessment("public", identity="c" * 64) if change == "snapshot" else None,
    )
    with pytest.raises(GoneError if change == "token" else ConflictError):
        await service.get_evidence_page(
            StubSession(),
            current.references.references[0].reference_id,
            current.snapshot_id,
            Settings(_env_file=None),
            token="local-token",
        )
    assert "native" in calls and calls.index("native") < len(calls) - 1


@pytest.mark.asyncio
async def test_successful_public_result_uses_only_admitted_ref_and_snapshot(monkeypatch):
    current = assessment("public")
    calls = install_snapshot_stubs(monkeypatch, current)
    session = StubSession()
    result = await service.get_evidence_page(
        session,
        current.references.references[0].reference_id,
        current.snapshot_id,
        Settings(_env_file=None),
        token="local-token",
    )
    assert (
        result.snapshot_id == current.snapshot_id
        and result.image_data_url == "data:image/png;base64,UE5H"
    )
    assert calls == ["authorize", "snapshot", "native", "authorize", "snapshot"]
    assert session.refreshes == [{"with_for_update": {"read": True}}]
    payload = service.evidence_manifest(current).model_dump_json()
    assert "PRIVATE" not in payload and "source_sha256" not in payload and "owner_id" not in payload
    assert "check_result_id" not in payload and '"criterion_id":21' in payload


@pytest.mark.asyncio
async def test_repeated_cancellation_drains_native_work_before_source_and_slot_release():
    settings = Settings(_env_file=None, evidence_source_slots=1)
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    def native():
        with evidence_image_admission(settings):
            entered.set()
            try:
                assert release.wait(timeout=5)
                return "completed"
            finally:
                closed.set()

    session = StubSession()
    work = asyncio.create_task(
        read_source(session, Row(purged_at=None), lambda: asyncio.to_thread(native))
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        work.cancel()
        await asyncio.sleep(0)
        work.cancel()
        await asyncio.sleep(0)
        assert not work.done() and not closed.is_set()
        with pytest.raises(RateLimitedError), evidence_image_admission(settings):
            pass
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
    assert closed.is_set()
    with evidence_image_admission(settings):
        pass


@pytest.mark.asyncio
async def test_aggregate_passes_actual_revision_and_checks_separate_flag_membership(monkeypatch):
    from app.flags import service as flags_service
    from app.report import service as report_service

    current = assessment()
    current.report.llm_mode = "real"
    seen_revisions = []
    lock_order = []
    flag = Row(id=31)
    result = Row(id=41)

    async def report_out(*args):
        lock_order.append("assessment_share_lock")
        return current.report

    async def flag_out(*args, assessment_revision):
        seen_revisions.append(assessment_revision)
        return Row(
            id=31,
            check_run_id=7,
            check_result_id=41,
            assessment_revision=assessment_revision,
            check_kind="statistical_forensics",
            agreement_pair=None,
            passage_pair=None,
            ai_verdict_summary=None,
            evidence_excerpt="Saved statistical statement.",
            page_anchor="page 3",
        )

    class AggregateSession:
        membership = (31,)

        async def scalar(self, statement):
            assert lock_order == ["assessment_share_lock"]
            return Row(content_hash="b" * 64)

        async def scalars(self, statement):
            return Row(all=lambda: self.membership)

        async def execute(self, statement):
            assert statement.get_execution_options().get("populate_existing") is True
            return Row(all=lambda: [(flag, result)])

    monkeypatch.setattr(report_service, "report_out_for_check_run", report_out)
    monkeypatch.setattr(flags_service, "_to_flag_out", flag_out)
    session = AggregateSession()
    value = await service.recorded_evidence_assessment(
        session, Row(id=7, manuscript_id=8), "private", Settings(_env_file=None)
    )
    assert seen_revisions == [3]
    assert len(value.references.references) == 2
    lock_order.clear()
    current.report.llm_mode = "fake"
    changed = await service.recorded_evidence_assessment(
        session, Row(id=7, manuscript_id=8), "private", Settings(_env_file=None)
    )
    assert changed.snapshot_id != value.snapshot_id
    lock_order.clear()
    session.membership = (31, 32)
    with pytest.raises(ValueError, match="incomplete or foreign"):
        await service.recorded_evidence_assessment(
            session, Row(id=7, manuscript_id=8), "private", Settings(_env_file=None)
        )


@pytest.mark.asyncio
async def test_actual_page_service_keeps_reservation_during_repeated_cancel(monkeypatch):
    current = assessment()
    calls = install_snapshot_stubs(monkeypatch, current)
    settings = Settings(_env_file=None, evidence_source_slots=1)
    entered = threading.Event()
    release = threading.Event()

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return ReferencePageImage(
            EvidencePlacementOut(reference_id=args[1].reference_id, state="page_only", page=2),
            b"PNG",
        )

    monkeypatch.setattr(service, "render_recorded_evidence_image", blocked)
    task = asyncio.create_task(
        service.get_evidence_page(
            StubSession(),
            current.references.references[0].reference_id,
            current.snapshot_id,
            settings,
            check_run_id=7,
            instructor_id=9,
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        with pytest.raises(RateLimitedError), evidence_image_admission(settings):
            pass
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls == ["authorize", "snapshot"]
    with evidence_image_admission(settings):
        pass


@pytest.mark.asyncio
async def test_service_rejects_before_executor_queue_and_source_fence(monkeypatch):
    current = assessment()
    calls = install_snapshot_stubs(monkeypatch, current)
    settings = Settings(_env_file=None, evidence_source_slots=1)
    occupied = threading.Event()
    release = threading.Event()
    queued = asyncio.Event()
    loop = asyncio.get_running_loop()

    def occupy():
        occupied.set()
        assert release.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        blocker = loop.run_in_executor(executor, occupy)
        assert await asyncio.to_thread(occupied.wait, 5)

        async def queued_thread(function, *args, **kwargs):
            queued.set()
            return await loop.run_in_executor(executor, lambda: function(*args, **kwargs))

        monkeypatch.setattr(service.asyncio, "to_thread", queued_thread)
        first_session = StubSession()
        task = asyncio.create_task(
            service.get_evidence_page(
                first_session,
                current.references.references[0].reference_id,
                current.snapshot_id,
                settings,
                check_run_id=7,
                instructor_id=9,
            )
        )
        try:
            await asyncio.wait_for(queued.wait(), timeout=5)
            other_session = StubSession()
            with pytest.raises(RateLimitedError):
                await service.get_evidence_page(
                    other_session,
                    current.references.references[0].reference_id,
                    current.snapshot_id,
                    settings,
                    check_run_id=7,
                    instructor_id=9,
                )
            assert calls == ["authorize", "snapshot", "authorize"]
            assert len(first_session.refreshes) == 1 and other_session.refreshes == []
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            with pytest.raises(RateLimitedError), evidence_image_admission(settings):
                pass
        finally:
            release.set()
            await blocker
            with pytest.raises(asyncio.CancelledError):
                await task
    with evidence_image_admission(settings):
        pass
