import pytest

from app.config import Settings


def _bare_settings(**env: str) -> Settings:
    # _env_file=None: ignore any developer .env so tests are deterministic.
    return Settings(_env_file=None, **env)


@pytest.mark.parametrize(
    "field",
    [
        "pipeline_claim_stale_seconds",
        "pipeline_claim_renew_seconds",
        "pipeline_coordination_timeout_seconds",
        "pipeline_worker_poll_seconds",
    ],
)
@pytest.mark.parametrize("value", [0, float("inf"), float("nan")])
def test_pipeline_coordination_requires_finite_positive_timings(field, value):
    with pytest.raises(ValueError):
        _bare_settings(**{field: value})


@pytest.mark.parametrize("value", [float("inf"), float("nan")])
def test_run_deadline_must_be_finite(value):
    with pytest.raises(ValueError):
        _bare_settings(pipeline_run_deadline_seconds=value)


def test_claim_lifetime_requires_two_bounded_renewal_opportunities():
    with pytest.raises(ValueError, match="two bounded renewal attempts"):
        _bare_settings(pipeline_claim_stale_seconds=70)


@pytest.mark.parametrize(
    "field", ["pipeline_admission_lock_namespace", "pipeline_admission_lock_key"]
)
@pytest.mark.parametrize("value", [-(2**31) - 1, 2**31])
def test_admission_lock_keys_fit_postgres_signed_int32(field, value):
    with pytest.raises(ValueError):
        _bare_settings(**{field: value})


def test_defaults_require_no_env(monkeypatch):
    for var in ("VERIDICAL_ENV", "VERIDICAL_FAKE_LLM", "DATABASE_URL", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    s = _bare_settings()
    assert s.veridical_env == "dev"
    assert s.veridical_fake_llm is False
    assert s.database_url.startswith("postgresql://")
    assert s.gemini_api_key is None


def test_fake_llm_flag_parses_1_as_true(monkeypatch):
    monkeypatch.setenv("VERIDICAL_FAKE_LLM", "1")
    assert _bare_settings().veridical_fake_llm is True


def test_env_overrides_apply(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@example:5432/x")
    monkeypatch.setenv("VERIDICAL_ENV", "prod")
    s = _bare_settings()
    assert s.database_url == "postgresql://u:p@example:5432/x"
    assert s.veridical_env == "prod"


def test_blank_patterns_file_env_means_unset(monkeypatch):
    """.env.example ships INGEST_PATTERNS_FILE= (blank); that must read as
    None, not Path('.') — found live: ingestion crashed with a PermissionError
    trying to read the current directory as the patterns file."""
    from app.config import get_settings

    monkeypatch.setenv("INGEST_PATTERNS_FILE", "")
    get_settings.cache_clear()
    assert get_settings().ingest_patterns_file is None


def test_blank_cors_origins_means_no_origins():
    assert _bare_settings().cors_allowed_origins_list == []


def test_cors_origins_split_on_comma_and_trimmed(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://a.example, https://b.example ,")
    s = _bare_settings()
    assert s.cors_allowed_origins_list == ["https://a.example", "https://b.example"]


def test_manuscript_list_page_size_bounds_are_configurable():
    settings = _bare_settings(
        manuscript_list_default_page_size="25",
        manuscript_list_max_page_size="75",
    )

    assert settings.manuscript_list_default_page_size == 25
    assert settings.manuscript_list_max_page_size == 75


@pytest.mark.parametrize(
    ("default_page_size", "max_page_size"),
    (("0", "200"), ("50", "0"), ("201", "200")),
)
def test_manuscript_list_page_size_bounds_reject_invalid_values(default_page_size, max_page_size):
    with pytest.raises(ValueError, match="manuscript_list_default_page_size"):
        _bare_settings(
            manuscript_list_default_page_size=default_page_size,
            manuscript_list_max_page_size=max_page_size,
        )


def test_audit_list_page_size_bounds_are_configurable():
    settings = _bare_settings(
        audit_list_default_page_size="25",
        audit_list_max_page_size="75",
        audit_list_max_page="100",
    )

    assert settings.audit_list_default_page_size == 25
    assert settings.audit_list_max_page_size == 75
    assert settings.audit_list_max_page == 100


@pytest.mark.parametrize(
    ("default_page_size", "max_page_size"),
    (("0", "200"), ("50", "0"), ("201", "200")),
)
def test_audit_list_page_size_bounds_reject_invalid_values(default_page_size, max_page_size):
    with pytest.raises(ValueError, match="audit_list_default_page_size"):
        _bare_settings(
            audit_list_default_page_size=default_page_size,
            audit_list_max_page_size=max_page_size,
        )


def test_audit_list_max_page_rejects_non_positive_values():
    with pytest.raises(ValueError, match="audit_list_max_page"):
        _bare_settings(audit_list_max_page="0")


def test_content_hash_recovery_bounds_are_configurable():
    settings = _bare_settings(
        content_hash_read_chunk_bytes="4096",
        content_hash_read_max_chunk_bytes="8192",
        content_hash_backfill_batch_size="7",
        content_hash_backfill_max_batch_size="11",
        content_hash_backfill_process_ceiling="12",
    )

    assert settings.content_hash_read_chunk_bytes == 4096
    assert settings.content_hash_read_max_chunk_bytes == 8192
    assert settings.content_hash_backfill_batch_size == 7
    assert settings.content_hash_backfill_max_batch_size == 11
    assert settings.content_hash_backfill_process_ceiling == 12


@pytest.mark.parametrize(
    "field",
    (
        "content_hash_read_chunk_bytes",
        "content_hash_read_max_chunk_bytes",
        "content_hash_backfill_batch_size",
        "content_hash_backfill_max_batch_size",
        "content_hash_backfill_process_ceiling",
    ),
)
def test_content_hash_recovery_bounds_reject_non_positive_values(field):
    with pytest.raises(ValueError, match=field):
        _bare_settings(**{field: "0"})


def test_content_hash_recovery_default_batch_cannot_exceed_maximum():
    with pytest.raises(ValueError, match="content_hash_backfill_batch_size"):
        _bare_settings(
            content_hash_backfill_batch_size="8",
            content_hash_backfill_max_batch_size="7",
        )


def test_content_hash_recovery_read_chunk_cannot_exceed_maximum():
    with pytest.raises(ValueError, match="content_hash_read_chunk_bytes"):
        _bare_settings(
            content_hash_read_chunk_bytes="8193",
            content_hash_read_max_chunk_bytes="8192",
        )


def test_content_hash_recovery_batch_maximum_cannot_exceed_process_ceiling():
    with pytest.raises(ValueError, match="content_hash_backfill_max_batch_size"):
        _bare_settings(
            content_hash_backfill_batch_size="10",
            content_hash_backfill_max_batch_size="101",
            content_hash_backfill_process_ceiling="100",
        )


@pytest.mark.asyncio
async def test_bug_280_settings_prompt_version_query_breaks_timestamp_ties_by_id_desc():
    """BUG-280: `get_settings_view` orders `DISTINCT ON (prompt_type)` by
    `created_at DESC, id DESC` so two audit rows sharing a timestamp pick the
    newest row deterministically."""
    from sqlalchemy.dialects import postgresql

    from app.models.instructor import Instructor
    from app.settings.service import get_settings_view

    captured_sql: list[str] = []

    class FakeScalarResult:
        def all(self):
            return []

    class FakeSession:
        async def scalars(self, stmt):
            compiled = str(
                stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            captured_sql.append(compiled)
            return FakeScalarResult()

    instructor = Instructor(
        id=42,
        email="tie@test.local",
        display_name="Tie",
        gemini_api_key_encrypted=None,
    )
    await get_settings_view(FakeSession(), _bare_settings(), instructor)
    assert len(captured_sql) == 1
    expected_order = (
        "ORDER BY audit_log.payload ->> 'prompt_type', "
        "audit_log.created_at DESC, audit_log.id DESC"
    )
    assert expected_order in captured_sql[0]


@pytest.mark.asyncio
async def test_bug_207_pipeline_list_check_runs_breaks_timestamp_ties_by_id_desc():
    """BUG-207: `list_check_runs` orders by `created_at DESC, id DESC` so runs
    sharing a timestamp have a deterministic primary-key tie-breaker."""
    from sqlalchemy.dialects import postgresql

    from app.pipeline.service import list_check_runs

    captured_sql: list[str] = []

    class FakeScalarResult:
        def all(self):
            return []

    class FakeSession:
        async def scalars(self, stmt):
            compiled = str(
                stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            captured_sql.append(compiled)
            return FakeScalarResult()

    await list_check_runs(FakeSession(), instructor_id=7)
    assert len(captured_sql) == 1
    assert "ORDER BY check_run.created_at DESC, check_run.id DESC" in captured_sql[0]


