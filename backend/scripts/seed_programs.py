"""CLI entrypoint for `app.groups.service.seed_default_programs` (V-062) --
adds any name in `Settings.default_programs` not already in the `program`
table. The real implementation lives in `app/` (the app's own boot
lifespan calls it directly); this script is a by-hand equivalent for an
environment where restarting the app isn't the way you'd want to trigger
it, e.g. Neon prod between deploys.

    uv run python -m scripts.seed_programs
"""

import asyncio

from app.groups.service import seed_default_programs

if __name__ == "__main__":
    added = asyncio.run(seed_default_programs())
    print(f"programs seeded: {added}" if added else "no new programs (already seeded)")
