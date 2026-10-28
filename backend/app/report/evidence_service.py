"""F8: one locked assessment aggregate for source evidence, never a new grade."""

import asyncio
import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ConflictError, GoneError, NotFoundError
from app.ingest.schemas import SectionTree
from app.models.manuscript import Manuscript
from app.models.run import CheckResult, CheckRun, Flag
from app.models.share import ShareLink
from app.report.evidence_captions import placement_caption, reader_location_caption
from app.report.evidence_images import (
    EvidenceSourceFile,
    evidence_image_admission,
    render_recorded_evidence_image,
)
from app.report.evidence_placement import EvidencePlacementOut
from app.report.evidence_references import (
    Audience,
    EvidenceReferenceSnapshot,
    EvidenceSourceReference,
    PublicEvidenceReferenceOut,
    build_evidence_reference_snapshot,
    public_evidence_reference,
)
from app.report.evidence_source import (
    BoundEvidenceSource,
    locate_bound_evidence,
    prepare_bound_evidence_source,
    read_bound_docx_image,
)
from app.report.schemas import DocumentParagraphsOut, ReportOut
from app.storage.access import read_source


class EvidenceManifestOut(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    snapshot_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    assessment_revision: int | None
    llm_mode: Literal["fake", "real", "unknown"]
    references: tuple[PublicEvidenceReferenceOut, ...]


class EvidencePageOut(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    snapshot_id: str
    placement: EvidencePlacementOut
    image_data_url: str | None
    image_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    caption: str


class EvidenceLocationOut(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    snapshot_id: str
    source_identity: str | None
    source_format: Literal["pdf", "docx"] | None
    placement: EvidencePlacementOut
    caption: str


class EvidenceDocumentOut(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    source_identity: str
    document: DocumentParagraphsOut


@dataclass(frozen=True)
class RecordedEvidenceAssessment:
    report: ReportOut
    references: EvidenceReferenceSnapshot
    llm_mode: Literal["fake", "real", "unknown"]
    snapshot_id: str


def _reading_reference(reference: EvidenceSourceReference) -> PublicEvidenceReferenceOut:
    if reference.audience == "public":
        return public_evidence_reference(reference)
    reference = EvidenceSourceReference.model_validate(reference.model_dump())
    # The private response deliberately uses the same small reading association;
    # no result IDs, source hashes, annotations or arbitrary detail cross HTTP.
    return PublicEvidenceReferenceOut(
        reference_id=reference.reference_id,
        criterion_id=reference.criterion_id,
        flag_id=reference.owner_id if reference.owner_kind == "finding" else None,
        role=reference.role,
        index=reference.index,
        quote=reference.quote,
        anchor=reference.anchor,
        source_scope=reference.source_scope,
        source_state=reference.source_state,
    )


def evidence_manifest(assessment: RecordedEvidenceAssessment) -> EvidenceManifestOut:
    return EvidenceManifestOut(
        snapshot_id=assessment.snapshot_id,
        assessment_revision=assessment.references.assessment_revision,
        llm_mode=assessment.llm_mode,
        references=tuple(_reading_reference(ref) for ref in assessment.references.references),
    )


async def authorize_evidence_run(
    session: AsyncSession,
    *,
    check_run_id: int | None = None,
    instructor_id: int | None = None,
    token: str | None = None,
) -> CheckRun:
    if token is not None and check_run_id is None and instructor_id is None:
        link = await session.scalar(
            select(ShareLink)
            .where(ShareLink.token == token)
            .execution_options(populate_existing=True)
        )
        if link is None:
            raise NotFoundError("This link doesn't exist.")
        if link.revoked_at is not None:
            raise GoneError("This link has been turned off by the instructor.")
        if link.expires_at is not None and link.expires_at <= datetime.now(UTC):
            raise GoneError("This link has expired.")
        run = await session.scalar(
            select(CheckRun)
            .where(CheckRun.id == link.check_run_id)
            .execution_options(populate_existing=True)
        )
    elif token is None and check_run_id is not None and instructor_id is not None:
        run = await session.scalar(
            select(CheckRun)
            .join(Manuscript, Manuscript.id == CheckRun.manuscript_id)
            .where(CheckRun.id == check_run_id, Manuscript.instructor_id == instructor_id)
            .execution_options(populate_existing=True)
        )
    else:
        raise ValueError("Evidence access must have one authority")
    if run is None:
        raise NotFoundError("This evidence record doesn't exist.")
    return run


async def recorded_evidence_assessment(
    session: AsyncSession, run: CheckRun, audience: Audience, settings: Settings
) -> RecordedEvidenceAssessment:
    # Local imports keep the existing report/flag assembly dependency acyclic.
    from app.flags.service import _to_flag_out
    from app.report.service import report_out_for_check_run

    # report_out holds the assessment SHARE lock. All assessment writers use
    # its UPDATE counterpart, so results and flag details below share this epoch.
    report = await report_out_for_check_run(session, run, settings)
    manuscript = await session.scalar(
        select(Manuscript)
        .where(Manuscript.id == run.manuscript_id)
        .execution_options(populate_existing=True)
    )
    membership = tuple(
        (
            await session.scalars(
                select(Flag.id)
                .join(CheckResult, CheckResult.id == Flag.check_result_id)
                .where(CheckResult.check_run_id == run.id)
                .order_by(Flag.id)
            )
        ).all()
    )
    rows = (
        await session.execute(
            select(Flag, CheckResult)
            .join(CheckResult, CheckResult.id == Flag.check_result_id)
            .where(CheckResult.check_run_id == run.id)
            .order_by(Flag.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    flags = [
        await _to_flag_out(
            session, flag, result, manuscript, run, assessment_revision=report.assessment_revision
        )
        for flag, result in rows
    ]
    references = build_evidence_reference_snapshot(
        report,
        flags,
        expected_flag_ids=membership,
        source_sha256=manuscript.content_hash,
        audience=audience,
    )
    mode = report.llm_mode if report.llm_mode in {"fake", "real", "unknown"} else "unknown"
    identity = json.dumps(
        {"references": references.model_dump(), "llm_mode": mode},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return RecordedEvidenceAssessment(
        report, references, mode, hashlib.sha256(identity).hexdigest()
    )


async def get_evidence_manifest(
    session: AsyncSession,
    settings: Settings,
    *,
    check_run_id: int | None = None,
    instructor_id: int | None = None,
    token: str | None = None,
) -> EvidenceManifestOut:
    authority = dict(check_run_id=check_run_id, instructor_id=instructor_id, token=token)
    run = await authorize_evidence_run(session, **authority)
    assessment = await recorded_evidence_assessment(
        session, run, "public" if token is not None else "private", settings
    )
    await authorize_evidence_run(session, **authority)
    return evidence_manifest(assessment)


async def get_evidence_page(
    session: AsyncSession,
    reference_id: str,
    snapshot_id: str,
    settings: Settings,
    *,
    check_run_id: int | None = None,
    instructor_id: int | None = None,
    token: str | None = None,
) -> EvidencePageOut:
    authority = dict(check_run_id=check_run_id, instructor_id=instructor_id, token=token)
    run = await authorize_evidence_run(session, **authority)
    # Reserve before assessment/source locks or executor scheduling. read_source
    # drains native work before propagating even repeated cancellation, so this
    # reservation cannot be released while its native task is still running.
    with evidence_image_admission(settings):
        return await _admitted_evidence_page(
            session, run, reference_id, snapshot_id, settings, authority
        )


async def _admitted_evidence_page(session, run, reference_id, snapshot_id, settings, authority):
    assessment = await recorded_evidence_assessment(
        session, run, "public" if authority["token"] is not None else "private", settings
    )
    if snapshot_id != assessment.snapshot_id:
        raise ConflictError("This assessment changed. Reload its evidence before continuing.")
    reference = next(
        (ref for ref in assessment.references.references if ref.reference_id == reference_id), None
    )
    if reference is None:
        raise NotFoundError("This evidence record doesn't exist.")
    manuscript = await session.get(Manuscript, run.manuscript_id)

    async def render():
        # read_source refreshes and SHARE-locks this row before copying storage
        # attributes. Native work uses no ORM/session and always closes handles.
        source = EvidenceSourceFile(manuscript.file_ref, manuscript.purged_at)
        tree = SectionTree.model_validate(
            manuscript.section_tree or {"source": "none", "nodes": []}
        )
        return await asyncio.to_thread(
            render_recorded_evidence_image,
            source,
            reference,
            tree,
            settings,
            llm_mode=assessment.llm_mode,
        )

    image = await read_source(session, manuscript, render)
    # A revoked/expired token must never receive a result completed in flight.
    # The assessment/source locks stay held until this request is finished.
    current_run = await authorize_evidence_run(session, **authority)
    current = await recorded_evidence_assessment(
        session, current_run, assessment.references.audience, settings
    )
    if current.snapshot_id != assessment.snapshot_id:
        raise ConflictError("This assessment changed. Reload its evidence before continuing.")
    return EvidencePageOut(
        snapshot_id=assessment.snapshot_id,
        image_sha256=(
            hashlib.sha256(image.image_png).hexdigest() if image.image_png is not None else None
        ),
        placement=image.placement,
        caption=placement_caption(image.placement.state, image.placement.reason),
        image_data_url=(
            "data:image/png;base64," + base64.b64encode(image.image_png).decode("ascii")
            if image.image_png is not None
            else None
        ),
    )


async def _read_bound_reader_source(
    session: AsyncSession,
    run: CheckRun,
    manuscript: Manuscript,
    expected_sha256: Callable[[], str | None],
    settings: Settings,
    *,
    consume=None,
):
    async def reader():
        source_file = EvidenceSourceFile(manuscript.file_ref, manuscript.purged_at)
        try:
            tree = SectionTree.model_validate(
                manuscript.section_tree or {"source": "none", "nodes": []}
            )
        except ValidationError:
            raise ConflictError("The saved source structure could not be read.") from None
        digest = expected_sha256()
        run_id, manuscript_id = run.id, manuscript.id

        def work():
            bound = prepare_bound_evidence_source(
                source_file, run_id, manuscript_id, digest, tree, settings
            )
            return consume(bound, tree) if consume is not None else bound

        return await asyncio.to_thread(work)

    return await read_source(session, manuscript, reader)


async def get_private_evidence_location(
    session: AsyncSession,
    check_run_id: int,
    instructor_id: int,
    reference_id: str,
    snapshot_id: str,
    settings: Settings,
) -> EvidenceLocationOut:
    authority = dict(check_run_id=check_run_id, instructor_id=instructor_id)
    run = await authorize_evidence_run(session, **authority)
    with evidence_image_admission(settings):
        assessment = await recorded_evidence_assessment(session, run, "private", settings)
        if snapshot_id != assessment.snapshot_id:
            raise ConflictError("This assessment changed. Reload its evidence before continuing.")
        reference = next(
            (ref for ref in assessment.references.references if ref.reference_id == reference_id),
            None,
        )
        if reference is None:
            raise NotFoundError("This evidence record doesn't exist.")
        bound = None
        if reference.source_state != "not_checked":
            placement = EvidencePlacementOut(
                reference_id=reference.reference_id,
                state="unavailable",
                reason=reference.source_state,
            )
        else:
            manuscript = await session.get(Manuscript, run.manuscript_id)
            bound, placement = await _read_bound_reader_source(
                session,
                run,
                manuscript,
                lambda: assessment.references.source_sha256,
                settings,
                consume=lambda source, tree: (
                    source,
                    locate_bound_evidence(source, reference, tree, settings),
                ),
            )
        current_run = await authorize_evidence_run(session, **authority)
        current = await recorded_evidence_assessment(session, current_run, "private", settings)
        if current.snapshot_id != assessment.snapshot_id:
            raise ConflictError("This assessment changed. Reload its evidence before continuing.")
        return EvidenceLocationOut(
            snapshot_id=assessment.snapshot_id,
            source_identity=bound.source_identity if bound is not None else None,
            source_format=bound.source_format if bound is not None else None,
            placement=placement,
            caption=reader_location_caption(placement.state, placement.reason),
        )


async def get_private_evidence_reader_source(
    session: AsyncSession,
    check_run_id: int,
    instructor_id: int,
    source_identity: str,
    settings: Settings,
) -> BoundEvidenceSource:
    """Read source content, independently of a later instructor grade revision.

    The requested content identity must match current owned, hash-bound source
    bytes/projection. It is not a bearer credential or an assessment judgment.
    """
    return await _private_reader_content(
        session, check_run_id, instructor_id, source_identity, settings, lambda bound: bound
    )


async def get_private_evidence_source_image(
    session: AsyncSession,
    check_run_id: int,
    instructor_id: int,
    source_identity: str,
    image_index: int,
    settings: Settings,
) -> tuple[bytes, str]:
    return await _private_reader_content(
        session,
        check_run_id,
        instructor_id,
        source_identity,
        settings,
        lambda bound: read_bound_docx_image(bound, image_index, settings),
    )


async def _private_reader_content(
    session, check_run_id, instructor_id, source_identity, settings, consume
):
    authority = dict(check_run_id=check_run_id, instructor_id=instructor_id)
    run = await authorize_evidence_run(session, **authority)
    with evidence_image_admission(settings):
        manuscript = await session.get(Manuscript, run.manuscript_id)

        def read(bound, tree):
            if source_identity != bound.source_identity:
                raise ConflictError(
                    "This source copy changed. Review its current evidence location."
                )
            return bound, consume(bound)

        bound, content = await _read_bound_reader_source(
            session, run, manuscript, lambda: manuscript.content_hash, settings, consume=read
        )
        current_run = await authorize_evidence_run(session, **authority)
        if (
            current_run.manuscript_id != bound.manuscript_id
            or source_identity != bound.source_identity
        ):
            raise ConflictError("This source copy changed. Review its current evidence location.")
        return content
