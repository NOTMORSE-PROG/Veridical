"""MuPDF's process-wide cache of decoded images and fonts."""

import pymupdf


def release_native_cache() -> None:
    """Empty MuPDF's store of decoded images and fonts.

    MuPDF keeps up to 256 MiB of them across requests, which alone is enough
    to push a long, image-heavy manuscript past a small server's memory.
    Items still in use elsewhere are kept; only cached copies are dropped."""
    pymupdf.TOOLS.store_shrink(100)
