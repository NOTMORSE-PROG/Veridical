"""Ingest a manuscript file for the demo instructor and print the parsed
structure — the V0 demo entry point.

Usage (from backend/, DB migrated and seeded):

    uv run python -m scripts.ingest_file path/to/manuscript.pdf
    uv run python -m scripts.ingest_file file.pdf --group-label "Group 3"
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db import sqlalchemy_url
from app.ingest.schemas import SectionNode
from app.ingest.service import ingest_manuscript, raw_store_path
from app.models import Citation, Instructor, Manuscript
from scripts.seed_dev import DEMO_EMAIL


def _print_tree(nodes: list[SectionNode], indent: int = 0) -> None:
    for node in nodes:
        anchor = f"p{node.page}" if node.page is not None else f"para {node.paragraph}"
        print(f"{'  ' * indent}- {node.title}  ({anchor})")
        _print_tree(node.children, indent + 1)


async def run(path: Path, group_label: str) -> None:
    settings = get_settings()
    engine = create_async_engine(sqlalchemy_url(settings.database_url))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    started = time.perf_counter()
    async with session_factory() as session:
        instructor = await session.scalar(select(Instructor).where(Instructor.email == DEMO_EMAIL))
        if instructor is None:
            sys.exit("Demo instructor missing — run `uv run python -m scripts.seed_dev` first.")
        manuscript = Manuscript(
            instructor_id=instructor.id, group_label=group_label, file_ref=str(path)
        )
        session.add(manuscript)
        await session.commit()
        result = await ingest_manuscript(session, manuscript, path)
        citations = (
            await session.scalars(
                select(Citation)
                .where(Citation.manuscript_id == manuscript.id)
                .order_by(Citation.order_index)
            )
        ).all()
    await engine.dispose()
    elapsed = time.perf_counter() - started

    print(f"manuscript id: {manuscript.id}   status: {manuscript.ingest_status}")
    print(
        f"pages: {result.page_count}   text blocks: {len(result.blocks)}   "
        f"images: {len(result.images)}   text chars: {result.text_chars}   "
        f"image_only: {result.image_only}"
    )
    print(f"raw store: {raw_store_path(settings, manuscript.id)}")
    vision_tables = [t for t in result.tables if t.source == "vision"]
    print(
        f"vision: {result.vision_status}   tables: {len(result.tables)} "
        f"({len(vision_tables)} from images, "
        f"{sum(1 for t in vision_tables if t.low_confidence)} low-confidence)   "
        f"equations: {len(result.equations)}"
    )
    print(f"section tree (source: {result.section_tree.source}):")
    _print_tree(result.section_tree.nodes)
    parsed = sum(1 for c in citations if c.parse_status == "parsed")
    print(f"citations: {len(citations)} ({parsed} parsed, {len(citations) - parsed} kept raw)")
    for c in citations[:5]:
        first = (c.authors or ["?"])[0]
        print(f"  [{c.parse_status}] {first} ({c.year}) — {(c.title or c.raw_text)[:60]}")
    print(f"elapsed: {elapsed:.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--group-label", default="Demo Group")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # Windows consoles vs unicode titles
    asyncio.run(run(args.file, args.group_label))


if __name__ == "__main__":
    main()
