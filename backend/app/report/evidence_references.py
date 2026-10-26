"""F8/4h/4w: immutable recorded evidence identities, not placement or support proof.

The caller must assemble report, flag details and authoritative membership under
one assessment read boundary. These pure checks cannot prove transaction history.
Internal models contain private binding metadata and must never be returned over
HTTP. Public projection is an independent allowlist. Reference IDs grant no access.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.findings.policy import is_reuse_summary

if TYPE_CHECKING:
    from app.flags.schemas import FlagOut
    from app.report.schemas import ReportOut

Audience = Literal["private", "public"]
Role = Literal[
    "criterion_evidence",
    "criterion_scope",
    "finding_excerpt",
    "finding_scope",
    "agreement_intent",
    "agreement_outcome",
    "reuse_own",
    "reuse_match",
]
SourceState = Literal[
    "not_checked",
    "assessment_revision_unverified",
    "source_identity_unverified",
    "bounded_excerpt_only",
]
RenderPolicy = Literal["private_original_visibility_v1", "public_annotation_free_page_v1"]


def _reference_id(values):
    canonical = json.dumps(
        {"schema_version": "evidence-reference-v1", **values},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _source_state(revision, digest, foreign):
    return (
        "bounded_excerpt_only"
        if foreign
        else "assessment_revision_unverified"
        if revision is None
        else "source_identity_unverified"
        if digest is None
        else "not_checked"
    )


class EvidenceSourceReference(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", revalidate_instances="always"
    )

    reference_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    check_run_id: int = Field(gt=0)
    manuscript_id: int = Field(gt=0)
    assessment_revision: int | None = Field(ge=0)
    source_sha256: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    audience: Audience
    render_policy: RenderPolicy
    owner_kind: Literal["criterion", "finding"]
    owner_id: int = Field(gt=0)
    criterion_id: int | None = Field(gt=0)
    role: Role
    index: int = Field(ge=0)
    quote: str
    anchor: str | None
    source_scope: Literal["own_manuscript", "bounded_comparison_excerpt"]
    source_state: SourceState

    @model_validator(mode="after")
    def coherent_binding(self):
        expected_policy = (
            "private_original_visibility_v1"
            if self.audience == "private"
            else "public_annotation_free_page_v1"
        )
        foreign = self.source_scope == "bounded_comparison_excerpt"
        criterion = self.owner_kind == "criterion"
        if self.render_policy != expected_policy or self.source_state != _source_state(
            self.assessment_revision, self.source_sha256, foreign
        ):
            raise ValueError("Evidence audience or binding state is inconsistent")
        if criterion != (self.role in {"criterion_evidence", "criterion_scope"}) or criterion != (
            self.criterion_id is not None
        ):
            raise ValueError("Evidence owner and role are inconsistent")
        if foreign != (self.role == "reuse_match") or (foreign and self.source_sha256 is not None):
            raise ValueError("A comparison excerpt cannot acquire an original source binding")
        if self.reference_id != _reference_id(self.model_dump(exclude={"reference_id"})):
            raise ValueError("Evidence reference identity does not match its recorded values")
        return self


class EvidenceReferenceSnapshot(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", revalidate_instances="always"
    )

    schema_version: Literal["evidence-reference-v1"]
    check_run_id: int = Field(gt=0)
    manuscript_id: int = Field(gt=0)
    assessment_revision: int | None = Field(ge=0)
    source_sha256: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    audience: Audience
    references: tuple[EvidenceSourceReference, ...]

    @model_validator(mode="after")
    def coherent_members(self):
        identities = set()
        for reference in self.references:
            if (
                reference.reference_id in identities
                or any(
                    (
                        reference.check_run_id != self.check_run_id,
                        reference.manuscript_id != self.manuscript_id,
                        reference.assessment_revision != self.assessment_revision,
                        reference.audience != self.audience,
                    )
                )
                or (
                    reference.source_scope == "own_manuscript"
                    and reference.source_sha256 != self.source_sha256
                )
            ):
                raise ValueError("Evidence snapshot members do not share one identity")
            identities.add(reference.reference_id)
        return self


class PublicEvidenceReferenceOut(BaseModel):
    """Source reading association only; never private result/binding metadata."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    reference_id: str
    criterion_id: int | None
    flag_id: int | None
    role: Role
    index: int
    quote: str
    anchor: str | None
    source_scope: Literal["own_manuscript", "bounded_comparison_excerpt"]
    source_state: SourceState


def _identity(value, *, revision=False):
    if revision and value is None:
        return
    if type(value) is not int or value < (0 if revision else 1):
        raise ValueError("Recorded evidence identity is invalid")


def build_evidence_reference_snapshot(
    report: "ReportOut",
    flags: Sequence["FlagOut"],
    *,
    expected_flag_ids: Sequence[int],
    source_sha256: str | None,
    audience: Audience,
) -> EvidenceReferenceSnapshot:
    """Copy one coherent aggregate, retaining unplaceable entries explicitly."""
    if audience not in {"private", "public"}:
        raise ValueError("Evidence audience is invalid")
    for value in (report.check_run_id, report.manuscript_id):
        _identity(value)
    revision = report.assessment_revision
    _identity(revision, revision=True)
    for value in expected_flag_ids:
        _identity(value)
    expected = set(expected_flag_ids)
    if len(expected) != len(expected_flag_ids):
        raise ValueError("Authoritative evidence membership has duplicate flags")
    seen = set()
    for flag in flags:
        _identity(flag.id)
        _identity(flag.check_run_id)
        _identity(flag.check_result_id)
        _identity(flag.assessment_revision, revision=True)
        if (
            flag.id in seen
            or flag.check_run_id != report.check_run_id
            or flag.assessment_revision != revision
        ):
            raise ValueError("Recorded evidence flags do not share one assessment")
        seen.add(flag.id)
    if seen != expected:
        raise ValueError("Recorded evidence membership is incomplete or foreign")
    seen_results = set()
    for row in report.results:
        _identity(row.check_result_id)
        _identity(row.criterion_id)
        if row.check_result_id in seen_results:
            raise ValueError("Recorded evidence has duplicate criterion results")
        seen_results.add(row.check_result_id)

    digest = (
        source_sha256
        if isinstance(source_sha256, str) and re.fullmatch(r"[a-f0-9]{64}", source_sha256)
        else None
    )
    policy = (
        "private_original_visibility_v1"
        if audience == "private"
        else "public_annotation_free_page_v1"
    )
    references = []

    def append(owner_kind, owner_id, criterion_id, role, index, quote, anchor, *, foreign=False):
        if not isinstance(quote, str) or (anchor is not None and not isinstance(anchor, str)):
            raise ValueError("Recorded evidence text is invalid")
        state = _source_state(revision, digest, foreign)
        values = dict(
            check_run_id=report.check_run_id,
            manuscript_id=report.manuscript_id,
            assessment_revision=revision,
            source_sha256=None if foreign else digest,
            audience=audience,
            render_policy=policy,
            owner_kind=owner_kind,
            owner_id=owner_id,
            criterion_id=criterion_id,
            role=role,
            index=index,
            quote=quote,
            anchor=anchor,
            source_scope="bounded_comparison_excerpt" if foreign else "own_manuscript",
            source_state=state,
        )
        references.append(EvidenceSourceReference(reference_id=_reference_id(values), **values))

    for row in report.results:
        if row.evidence:
            for index, evidence in enumerate(row.evidence):
                append(
                    "criterion",
                    row.check_result_id,
                    row.criterion_id,
                    "criterion_evidence",
                    index,
                    evidence.quote,
                    evidence.anchor,
                )
        else:
            append(
                "criterion",
                row.check_result_id,
                row.criterion_id,
                "criterion_scope",
                0,
                "",
                row.anchor,
            )
    for flag in sorted(flags, key=lambda value: value.id):
        if flag.check_kind == "internal_agreement" and flag.agreement_pair is not None:
            pair = flag.agreement_pair
            append(
                "finding",
                flag.id,
                None,
                "agreement_intent",
                0,
                pair.intent_text,
                pair.intent_anchor,
            )
            append(
                "finding",
                flag.id,
                None,
                "agreement_outcome",
                1,
                pair.outcome_text,
                pair.outcome_anchor,
            )
        elif flag.check_kind == "originality_reuse" and flag.passage_pair is not None:
            append(
                "finding",
                flag.id,
                None,
                "reuse_own",
                0,
                flag.passage_pair.own_excerpt,
                flag.page_anchor,
            )
            append(
                "finding",
                flag.id,
                None,
                "reuse_match",
                1,
                flag.passage_pair.matched_excerpt,
                None,
                foreign=True,
            )
        elif flag.check_kind == "originality_reuse" and is_reuse_summary(flag.ai_verdict_summary):
            append("finding", flag.id, None, "finding_scope", 0, "", flag.page_anchor)
        else:
            append(
                "finding",
                flag.id,
                None,
                "finding_excerpt",
                0,
                flag.evidence_excerpt,
                flag.page_anchor,
            )
    return EvidenceReferenceSnapshot(
        schema_version="evidence-reference-v1",
        check_run_id=report.check_run_id,
        manuscript_id=report.manuscript_id,
        assessment_revision=revision,
        source_sha256=digest,
        audience=audience,
        references=tuple(references),
    )


def public_evidence_reference(reference: EvidenceSourceReference) -> PublicEvidenceReferenceOut:
    # Pydantic's unchecked model_copy(update=...) must not cross this boundary.
    reference = EvidenceSourceReference.model_validate(reference.model_dump())
    if (
        reference.audience != "public"
        or reference.render_policy != "public_annotation_free_page_v1"
    ):
        raise ValueError("A private evidence reference cannot become a public image reference")
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
