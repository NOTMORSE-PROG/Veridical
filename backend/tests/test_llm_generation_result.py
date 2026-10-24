"""Provider metadata cannot be inferred from grading JSON or requested names."""

import pytest

from app.llm.result import GenerationResult, generation_result


def test_legacy_payload_cannot_supply_provider_metadata():
    payload = {"model_version": "inside-untrusted-json", "payload": {"grade": "example"}}
    result = generation_result(payload)
    assert result.payload is payload
    assert result.model_version is None


def test_reported_version_is_retained_without_rewriting_the_payload():
    payload = {"grade": "example"}
    original = GenerationResult(payload=payload, model_version="provider-reported-version")
    assert generation_result(original) is original
    assert original.payload is payload
    assert original.model_version == "provider-reported-version"


@pytest.mark.parametrize("value", [None, "", " \n", 3, False, [], {}])
def test_absent_or_invalid_version_stays_unrecorded(value):
    assert GenerationResult(payload={"grade": "example"}, model_version=value).model_version is None


@pytest.mark.parametrize("payload", [[{"grade": "example"}], "text", 3, False, None])
def test_normalization_preserves_payload_shape_for_existing_validators(payload):
    result = generation_result(payload)
    assert result.payload is payload
    assert result.model_version is None
