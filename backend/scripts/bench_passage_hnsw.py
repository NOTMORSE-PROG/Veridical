"""V-072 research: real HNSW build/query latency at passage-level (F7.4)
archive row counts. Uses a scratch table in the target DB (never the real
archive tables), dropped at the end regardless of outcome. Committed so
this ticket's own "measured, resolved" research claim is re-runnable, not
just a paragraph of prose (`professor` review, 2026-08-20, V-072.md).

Usage (from backend/):

    uv run python -m scripts.bench_passage_hnsw

Requires DATABASE_URL pointed at a real Postgres with pgvector — never run
against a production database (this drops its own scratch table, but a
CREATE INDEX at these row counts is real, if brief, load).
"""

import asyncio
import random
import time

import asyncpg

from app.config import get_settings

_SCRATCH_TABLE = "bench_passage_hnsw_scratch"


def _unit_vector(dim: int) -> list[float]:
    vec = [random.gauss(0, 1) for _ in range(dim)]
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


async def main() -> None:
    settings = get_settings()
    dim = settings.embedding_dim
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f"DROP TABLE IF EXISTS {_SCRATCH_TABLE}")
        await conn.execute(
            f"CREATE TABLE {_SCRATCH_TABLE} (id bigserial PRIMARY KEY, embedding vector({dim}))"
        )

        # Row counts span this ticket's own worst-case archive-size
        # projection (V-072.md: "2,000-10,000 rows for one defense season").
        for n in (2000, 5000, 10000):
            current = await conn.fetchval(f"SELECT count(*) FROM {_SCRATCH_TABLE}")
            to_add = n - current
            if to_add > 0:
                rows = [(str(_unit_vector(dim)),) for _ in range(to_add)]
                await conn.executemany(
                    f"INSERT INTO {_SCRATCH_TABLE} (embedding) VALUES ($1::vector)", rows
                )

            await conn.execute(f"DROP INDEX IF EXISTS ix_{_SCRATCH_TABLE}_embedding")
            t0 = time.perf_counter()
            await conn.execute(
                f"CREATE INDEX ix_{_SCRATCH_TABLE}_embedding ON {_SCRATCH_TABLE} "
                "USING hnsw (embedding vector_cosine_ops)"
            )
            build_s = time.perf_counter() - t0

            times = []
            for _ in range(20):
                qvec = str(_unit_vector(dim))
                t0 = time.perf_counter()
                await conn.fetchrow(
                    f"SELECT id, embedding <=> $1::vector AS dist FROM {_SCRATCH_TABLE} "
                    f"ORDER BY embedding <=> $1::vector LIMIT 1",
                    qvec,
                )
                times.append(time.perf_counter() - t0)
            times.sort()
            mean_ms = sum(times) / len(times) * 1000
            p95_ms = times[int(len(times) * 0.95)] * 1000
            print(
                f"n={n:6d}  index_build={build_s:6.2f}s  "
                f"query_mean={mean_ms:6.2f}ms  query_p95={p95_ms:6.2f}ms"
            )

        # The real workload shape: one query per own-passage (a manuscript
        # might have ~100-300 passages) against the worst-case archive,
        # sequential round trips -- the naive per-passage-query design
        # `query_similar_passages` actually uses.
        n_own_passages = 200
        t0 = time.perf_counter()
        for _ in range(n_own_passages):
            qvec = str(_unit_vector(dim))
            await conn.fetchrow(
                f"SELECT id, embedding <=> $1::vector AS dist FROM {_SCRATCH_TABLE} "
                f"ORDER BY embedding <=> $1::vector LIMIT 1",
                qvec,
            )
        total_s = time.perf_counter() - t0
        print(
            f"\nSequential {n_own_passages} per-passage queries against "
            f"n=10000 archive: {total_s:.2f}s total ({total_s / n_own_passages * 1000:.2f}ms/query)"
        )
    finally:
        await conn.execute(f"DROP TABLE IF EXISTS {_SCRATCH_TABLE}")
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
