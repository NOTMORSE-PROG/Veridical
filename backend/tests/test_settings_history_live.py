"""Settings history must use the same ownership boundary as the audit reader."""

from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.models.audit import AuditLog
from app.models.instructor import Instructor
from app.settings.service import get_settings_view
from tests import test_audit_live as audit_fixtures
from tests import test_llm_queue as queue_fixtures

# Reuse the isolated migrated audit database, never the running application's DB.
pytestmark = audit_fixtures.live
llm_scratch_url = queue_fixtures.llm_scratch_url
session_factory = queue_fixtures.session_factory
_clean_tables = audit_fixtures._clean_tables


@pytest.mark.parametrize(
    ("event", "fake", "version", "expected_source", "expected_version"),
    [
        ("llm_call", False, " provider-version ", "generation", "provider-version"),
        ("llm_cache_hit", False, "cached-version", "cache", "cached-version"),
        ("llm_call", True, "fixture-is-not-provider", "test", None),
        ("llm_call", None, None, "unknown", None),
        ("llm_cache_hit", None, None, "unknown", None),
        ("llm_call", False, "  ", "generation", None),
        ("llm_call", False, {"untrusted": "value"}, "generation", None),
    ],
)
async def test_settings_history_projects_only_recorded_technical_metadata(
    session_factory, event, fake, version, expected_source, expected_version
):
    async with session_factory() as session:
        instructor = Instructor(email="projection@test.local", display_name="Projection")
        session.add(instructor)
        await session.commit()
        run = await audit_fixtures._make_run(session, instructor)
        payload = {
            "prompt_type": "semantic_grading",
            "model": "requested-legacy-id",
            "model_version": version,
            "response": {"model_version": "model-json-cannot-supply-version"},
        }
        if fake is not None:
            payload["fake_llm"] = fake
        session.add(
            AuditLog(
                event_type=event, check_run_id=run.id, prompt_version="recorded-v1", payload=payload
            )
        )
        await session.commit()
        view = await get_settings_view(session, Settings(), instructor)
        recorded = view.prompt_versions[0]
        assert recorded.requested_model == "requested-legacy-id"
        assert recorded.model_version == expected_version
        assert recorded.response_source == expected_source
        assert "model-json-cannot" not in view.model_dump_json()


async def test_settings_history_uses_owned_run_not_another_accounts_newer_call(session_factory):
    async with session_factory() as session:
        mine = Instructor(email="settings-mine@test.local", display_name="Mine")
        theirs = Instructor(email="settings-theirs@test.local", display_name="Theirs")
        session.add_all([mine, theirs])
        await session.commit()
        my_run = await audit_fixtures._make_run(session, mine)
        their_run = await audit_fixtures._make_run(session, theirs)
        session.add(
            AuditLog(
                event_type="llm_call",
                check_run_id=my_run.id,
                prompt_version="owned-v1",
                payload={"prompt_type": "semantic_grading", "model": "owned-model"},
            )
        )
        await session.commit()
        session.add(
            AuditLog(
                event_type="llm_cache_hit",
                check_run_id=their_run.id,
                prompt_version="foreign-v2",
                payload={
                    "prompt_type": "semantic_grading",
                    "model": "foreign-model",
                    "prompt": "Private source must not be returned.",
                },
            )
        )
        await session.commit()

        own_view = await get_settings_view(session, Settings(), mine)
        foreign_view = await get_settings_view(session, Settings(), theirs)
        assert len(own_view.prompt_versions) == 1
        assert own_view.prompt_versions[0].prompt_version == "owned-v1"
        assert own_view.prompt_versions[0].model == "owned-model"
        assert foreign_view.prompt_versions[0].prompt_version == "foreign-v2"
        assert set(own_view.prompt_versions[0].model_dump()) == {
            "prompt_type",
            "prompt_version",
            "model",
            "observed_at",
            "requested_model",
            "model_version",
            "response_source",
        }
        assert "Private source" not in foreign_view.model_dump_json()


async def test_settings_history_omits_unattributed_rows_even_with_payload_owner_hint(
    session_factory,
):
    async with session_factory() as session:
        instructor = Instructor(email="settings-empty@test.local", display_name="Empty")
        session.add(instructor)
        await session.flush()
        session.add(
            AuditLog(
                event_type="llm_call",
                prompt_version="unattributed-v1",
                payload={
                    "prompt_type": "rubric_decomposition",
                    "model": "unknown-owner-model",
                    "context": {"instructor_id": instructor.id},
                },
            )
        )
        await session.commit()

        view = await get_settings_view(session, Settings(), instructor)
        assert view.prompt_versions == []


async def test_settings_history_direct_attribution_never_overrides_foreign_run(session_factory):
    async with session_factory() as session:
        mine = Instructor(email="settings-direct@test.local", display_name="Mine")
        theirs = Instructor(email="settings-run@test.local", display_name="Theirs")
        session.add_all([mine, theirs])
        await session.commit()
        my_run = await audit_fixtures._make_run(session, mine)
        their_run = await audit_fixtures._make_run(session, theirs)
        session.add_all(
            [
                AuditLog(
                    event_type="llm_call",
                    manuscript_id=my_run.manuscript_id,
                    prompt_version="direct-v1",
                    payload={"prompt_type": "direct_source", "model": "owned-model"},
                ),
                AuditLog(
                    event_type="llm_call",
                    manuscript_id=my_run.manuscript_id,
                    check_run_id=their_run.id,
                    prompt_version="foreign-v1",
                    payload={"prompt_type": "semantic_grading", "model": "foreign-model"},
                ),
            ]
        )
        await session.commit()

        own_view = await get_settings_view(session, Settings(), mine)
        assert [row.prompt_version for row in own_view.prompt_versions] == ["direct-v1"]
        foreign_view = await get_settings_view(session, Settings(), theirs)
        assert [row.prompt_version for row in foreign_view.prompt_versions] == ["foreign-v1"]


async def test_settings_history_breaks_timestamp_ties_by_record_id(session_factory):
    async with session_factory() as session:
        instructor = Instructor(email="settings-tie@test.local", display_name="Tie")
        session.add(instructor)
        await session.commit()
        run = await audit_fixtures._make_run(session, instructor)
        same_time = datetime(2026, 10, 18, tzinfo=UTC)
        for version in ("first", "second"):
            session.add(
                AuditLog(
                    event_type="llm_call",
                    check_run_id=run.id,
                    created_at=same_time,
                    prompt_version=version,
                    payload={"prompt_type": "semantic_grading", "model": "owned-model"},
                )
            )
            await session.flush()
        await session.commit()

        view = await get_settings_view(session, Settings(), instructor)
        assert len(view.prompt_versions) == 1
        assert view.prompt_versions[0].prompt_version == "second"
