"""Private reader identity controls. Stubbed storage, no PDF authoring or SQL."""

import hashlib
from types import SimpleNamespace as Row

import pytest

from app.config import Settings
from app.errors import ConflictError
from app.ingest.schemas import ExtractionResult, SectionTree, TextBlock
from app.report import evidence_source as source
from app.report.evidence_images import EvidenceSourceFile
from app.report.evidence_references import build_evidence_reference_snapshot

TREE = SectionTree(source="none", nodes=[])
RAW = b"retained DOCX bytes; format detection is stubbed in these storage controls"
DIGEST = hashlib.sha256(RAW).hexdigest()


def extraction(*, witness=DIGEST, text="Recorded manuscript text."):
    return ExtractionResult(
        page_count=0,
        anchor_kind="paragraph",
        image_only=False,
        text_chars=len(text),
        section_tree=TREE,
        blocks=[TextBlock(paragraph=2, text=text, max_font_size=12, bold_ratio=0)],
        images=[],
        docx_source_sha256=witness,
    )


def reference(*, digest=DIGEST, anchor="paragraph 2", audience="private", revision=3):
    report = Row(
        check_run_id=7,
        manuscript_id=8,
        assessment_revision=revision,
        results=[
            Row(
                check_result_id=11,
                criterion_id=21,
                anchor=None,
                evidence=[Row(quote="Recorded manuscript text.", anchor=anchor)],
            )
        ],
    )
    return build_evidence_reference_snapshot(
        report,
        (),
        expected_flag_ids=(),
        source_sha256=digest,
        audience=audience,
    ).references[0]


def install_storage(monkeypatch, tmp_path, extracted=None):
    original = tmp_path / "original.docx"
    original.write_bytes(RAW)
    saved = tmp_path / "extraction.json"
    saved.write_text((extracted or extraction()).model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(source, "manuscript_source_file", lambda *args: (original, "docx"))
    monkeypatch.setattr(source, "ensure_local_file", lambda *args: saved)
    monkeypatch.setattr(source, "get_storage", lambda *args: object())
    return original, saved


def bound(settings=None):
    return source.prepare_bound_evidence_source(
        EvidenceSourceFile("private-key", None),
        7,
        8,
        DIGEST,
        TREE,
        settings or Settings(_env_file=None),
    )


def test_reader_identity_binds_projected_text_but_not_grade_revision(monkeypatch, tmp_path):
    _, saved = install_storage(monkeypatch, tmp_path)
    first = bound()
    assert first.document.paragraphs[0].text == "Recorded manuscript text."
    assert bound().source_identity == first.source_identity
    assert reference(revision=3).reference_id != reference(revision=4).reference_id
    saved.write_text(
        extraction(text="Changed saved projection.").model_dump_json(), encoding="utf-8"
    )
    assert bound().source_identity != first.source_identity


@pytest.mark.parametrize("witness", [None, "a" * 64])
def test_unbound_docx_extract_is_not_usable_for_bound_navigation(monkeypatch, tmp_path, witness):
    install_storage(monkeypatch, tmp_path, extraction(witness=witness))
    with pytest.raises(ConflictError, match="could not be linked"):
        bound()


@pytest.mark.parametrize("digest", [None, "A" * 64, "invalid"])
def test_missing_or_malformed_source_digest_denied_before_storage(monkeypatch, digest):
    monkeypatch.setattr(
        source,
        "manuscript_source_file",
        lambda *args: pytest.fail("No storage read without recorded identity"),
    )
    with pytest.raises(ConflictError, match="identity is unavailable"):
        source.prepare_bound_evidence_source(
            EvidenceSourceFile("private-key", None),
            7,
            8,
            digest,
            TREE,
            Settings(_env_file=None),
        )


def test_changed_original_bytes_and_oversized_or_malformed_extract_fail_closed(
    monkeypatch, tmp_path
):
    original, saved = install_storage(monkeypatch, tmp_path)
    original.write_bytes(b"replacement source")
    with pytest.raises(ConflictError, match="no longer matches"):
        bound()
    original.write_bytes(RAW)
    with pytest.raises(ConflictError, match="reading limit"):
        bound(Settings(_env_file=None, evidence_source_max_extraction_bytes=1))
    saved.write_bytes(b"invalid retained JSON")
    with pytest.raises(ConflictError, match="could not be read"):
        bound()


def test_source_location_is_paragraph_only_and_checks_exact_owner_copy(monkeypatch, tmp_path):
    install_storage(monkeypatch, tmp_path)
    current = bound()
    placed = source.locate_bound_evidence(current, reference(), TREE, Settings(_env_file=None))
    assert placed.state == "paragraph_only" and placed.paragraph == 2 and placed.boxes == ()
    absent = source.locate_bound_evidence(
        current, reference(anchor="paragraph 40"), TREE, Settings(_env_file=None)
    )
    assert absent.state == "unavailable"
    for ref in [reference(digest="a" * 64), reference(audience="public")]:
        with pytest.raises(ConflictError, match="different source"):
            source.locate_bound_evidence(current, ref, TREE, Settings(_env_file=None))
