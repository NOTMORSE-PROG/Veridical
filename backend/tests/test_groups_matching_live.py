"""V-063 live-DB tests: `match_or_create_group_from_proposal`'s matching
rule (ticket's own DECIDED section) -- short name + member overlap ->
same group; short name alone -> a genuinely NEW, disambiguated group,
never a silent merge.
"""

import os

import pytest
from sqlalchemy import select, text

from app.groups.service import find_rule3_collision, match_or_create_group_from_proposal
from app.models.group import Group, GroupMember
from app.models.instructor import Instructor

live = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="integration: needs a live Postgres (CI service or local docker-compose)",
)
pytestmark = live

SCRATCH_DB = "veridical_groupsmatchingtest"


@pytest.fixture(scope="module")
def scratch_url():
    import asyncio

    from alembic import command
    from tests.test_schema import _admin_execute, _alembic_config, _swap_db

    base = os.environ["DATABASE_URL"]
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))
    asyncio.run(_admin_execute(base, f'CREATE DATABASE "{SCRATCH_DB}"'))
    url = _swap_db(base, SCRATCH_DB)
    command.upgrade(_alembic_config(url), "head")
    yield url
    asyncio.run(_admin_execute(base, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))


@pytest.fixture()
def session_factory(scratch_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import sqlalchemy_url

    engine = create_async_engine(sqlalchemy_url(scratch_url))
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def _clean_tables(session_factory):
    async with session_factory() as session:
        await session.execute(
            text("TRUNCATE group_member, manuscript_group, instructor RESTART IDENTITY CASCADE")
        )
        await session.commit()
    yield


@pytest.fixture()
async def instructor_id(session_factory) -> int:
    async with session_factory() as session:
        instructor = Instructor(email="matching@demo.local", display_name="Matching Test")
        session.add(instructor)
        await session.commit()
        await session.refresh(instructor)
        return instructor.id


async def test_first_submission_creates_a_group_and_records_its_members(
    session_factory, instructor_id
):
    async with session_factory() as session:
        group, matched = await match_or_create_group_from_proposal(
            session, instructor_id, "VERIDICAL", ["Condino, Mark Andrei A", "Concepcion, Marc M."]
        )
        await session.commit()

    assert matched is False
    assert group.name == "VERIDICAL"
    async with session_factory() as session:
        members = (
            await session.scalars(select(GroupMember.name).where(GroupMember.group_id == group.id))
        ).all()
    assert set(members) == {"Condino, Mark Andrei A", "Concepcion, Marc M."}


async def test_resubmission_with_reworded_subtitle_but_one_overlapping_member_matches(
    session_factory, instructor_id
):
    """The ticket's own natural experiment: different subtitle/separator,
    same short name, one member in common -- must resolve to the SAME
    group, not fork a second one."""
    async with session_factory() as session:
        first, _ = await match_or_create_group_from_proposal(
            session,
            instructor_id,
            "VERIDICAL",
            [
                "Condino, Mark Andrei A",
                "Concepcion, Marc Laurence M.",
                "Munoz, John Marvin Oric A.",
            ],
        )
        await session.commit()
        first_id = first.id

    async with session_factory() as session:
        second, matched = await match_or_create_group_from_proposal(
            session,
            instructor_id,
            "VERIDICAL",  # same short name, extracted from a differently-worded title
            ["Condino, Mark Andrei A"],  # one overlapping member is enough
        )
        await session.commit()

    assert matched is True
    assert second.id == first_id


async def test_same_short_name_zero_member_overlap_creates_a_distinct_disambiguated_group(
    session_factory, instructor_id
):
    """Rule 3: two different teams can genuinely pick the same acronym --
    must NOT be silently merged into one group."""
    async with session_factory() as session:
        first, _ = await match_or_create_group_from_proposal(
            session, instructor_id, "VERIDICAL", ["Condino, Mark Andrei A"]
        )
        await session.commit()
        first_id = first.id

    async with session_factory() as session:
        second, matched = await match_or_create_group_from_proposal(
            session, instructor_id, "VERIDICAL", ["A Completely Different Person"]
        )
        await session.commit()

    assert matched is False
    assert second.id != first_id
    assert second.name != first.name  # disambiguated, not identical
    assert second.name.startswith("VERIDICAL")

    async with session_factory() as session:
        all_groups = (
            await session.scalars(select(Group).where(Group.instructor_id == instructor_id))
        ).all()
    assert len(all_groups) == 2  # both coexist, neither overwrote the other


async def test_a_member_added_or_dropped_between_submissions_one_overlap_is_still_enough(
    session_factory, instructor_id
):
    async with session_factory() as session:
        first, _ = await match_or_create_group_from_proposal(
            session, instructor_id, "Team X", ["Alice Reyes", "Bob Santos"]
        )
        await session.commit()
        first_id = first.id

    # Bob dropped, Carol added -- Alice is the sole surviving overlap.
    async with session_factory() as session:
        second, matched = await match_or_create_group_from_proposal(
            session, instructor_id, "Team X", ["Alice Reyes", "Carol Cruz"]
        )
        await session.commit()

    assert matched is True
    assert second.id == first_id
    async with session_factory() as session:
        members = (
            await session.scalars(select(GroupMember.name).where(GroupMember.group_id == first_id))
        ).all()
    # Carol is now recorded too -- newly observed members are added, not
    # dropped just because this particular submission omitted Bob.
    assert set(members) == {"Alice Reyes", "Bob Santos", "Carol Cruz"}


async def test_no_short_name_match_at_all_creates_a_plain_new_group(session_factory, instructor_id):
    async with session_factory() as session:
        await match_or_create_group_from_proposal(session, instructor_id, "Team A", ["Someone"])
        await session.commit()

    async with session_factory() as session:
        group, matched = await match_or_create_group_from_proposal(
            session, instructor_id, "Team B", ["Someone Else"]
        )
        await session.commit()

    assert matched is False
    assert group.name == "Team B"


async def test_find_rule3_collision_none_when_no_same_named_group_exists(
    session_factory, instructor_id
):
    async with session_factory() as session:
        collision = await find_rule3_collision(session, instructor_id, "VERIDICAL", ["Someone"])
    assert collision is None


async def test_find_rule3_collision_none_when_a_member_overlaps_the_ordinary_match_case(
    session_factory, instructor_id
):
    async with session_factory() as session:
        await match_or_create_group_from_proposal(
            session, instructor_id, "VERIDICAL", ["Condino, Mark Andrei A"]
        )
        await session.commit()

    async with session_factory() as session:
        collision = await find_rule3_collision(
            session, instructor_id, "VERIDICAL", ["Condino, Mark Andrei A"]
        )
    assert collision is None  # this is a MATCH, not a collision


async def test_find_rule3_collision_finds_the_existing_group_owners_call_2026_08_19(
    session_factory, instructor_id
):
    """Owner's call (2026-08-19, after backend-critic's review): rule 3
    ("propose the match, do not apply it") must be surfaced to the
    instructor BEFORE they confirm, not only disclosed after the fact via
    a disambiguating suffix."""
    async with session_factory() as session:
        first, _ = await match_or_create_group_from_proposal(
            session, instructor_id, "VERIDICAL", ["Condino, Mark Andrei A"]
        )
        await session.commit()
        first_id = first.id

    async with session_factory() as session:
        collision = await find_rule3_collision(
            session, instructor_id, "VERIDICAL", ["A Completely Different Person"]
        )
    assert collision is not None
    assert collision.id == first_id
    assert collision.name == "VERIDICAL"


async def test_find_rule3_collision_none_when_no_members_typed_yet(session_factory, instructor_id):
    """Nothing to compare against yet (e.g. the instructor hasn't finished
    typing members) -- never a false-positive collision warning."""
    async with session_factory() as session:
        await match_or_create_group_from_proposal(
            session, instructor_id, "VERIDICAL", ["Condino, Mark Andrei A"]
        )
        await session.commit()

    async with session_factory() as session:
        collision = await find_rule3_collision(session, instructor_id, "VERIDICAL", [])
    assert collision is None


async def test_concurrent_submissions_for_the_same_team_never_split_into_two_groups(
    session_factory, instructor_id
):
    """backend-critic (V-063 review): reproduced live against a real DB --
    before the retry loop re-queried candidates from scratch on every
    attempt (not just re-suffixing), N concurrent submissions from the
    SAME team (overlapping members, no pre-existing group) could each see
    an EMPTY candidate list before any of them committed, splitting one
    team across "VERIDICAL", "VERIDICAL (2)", "VERIDICAL (3)"... instead
    of every loser's retry finding the winner's row via the overlap
    check.

    A plain `asyncio.gather` over N calls (the shape
    `test_groups_concurrency_live.py` uses) turned out NOT reliable here
    -- confirmed live by reverting the fix and re-running: it still
    passed, because a local Postgres over loopback TCP is fast enough
    that 10 real async calls mostly don't interleave at the one instant
    that matters (each call's own candidate-lookup query, before ANY of
    them has committed). A barrier forces every one of the N coroutines
    to reach their FIRST insert attempt at the same instant instead of
    hoping real scheduling does it -- proven against the pre-fix code
    (reverted locally, this test failed as expected; restored, it
    passes) before being trusted as a real regression test."""
    import asyncio

    n = 10
    barrier = asyncio.Barrier(n)

    async def submit(i: int) -> tuple[int, bool]:
        async with session_factory() as session:
            original_flush = session.flush
            waited = False

            async def gated_flush(*args, **kwargs):
                nonlocal waited
                if not waited:
                    waited = True
                    await barrier.wait()
                return await original_flush(*args, **kwargs)

            session.flush = gated_flush
            group, matched = await match_or_create_group_from_proposal(
                session, instructor_id, "VERIDICAL", ["Shared Member", f"Unique Member {i}"]
            )
            await session.commit()
            return group.id, matched

    results = await asyncio.gather(*(submit(i) for i in range(n)))
    group_ids = {group_id for group_id, _ in results}
    assert len(group_ids) == 1, f"submissions split across {len(group_ids)} groups: {results}"

    async with session_factory() as session:
        all_groups = (
            await session.scalars(select(Group).where(Group.instructor_id == instructor_id))
        ).all()
    assert len(all_groups) == 1


async def test_zero_incoming_members_never_matches_anything_by_short_name_alone(
    session_factory, instructor_id
):
    """An image-only title page proposes a short name with NO members --
    must never accidentally "match" an existing same-named group just
    because there's nothing to compare against (empty-set intersection
    is always empty, by design: no evidence, no match)."""
    async with session_factory() as session:
        first, _ = await match_or_create_group_from_proposal(
            session, instructor_id, "VERIDICAL", ["Condino, Mark Andrei A"]
        )
        await session.commit()
        first_id = first.id

    async with session_factory() as session:
        second, matched = await match_or_create_group_from_proposal(
            session, instructor_id, "VERIDICAL", []
        )
        await session.commit()

    assert matched is False
    assert second.id != first_id
