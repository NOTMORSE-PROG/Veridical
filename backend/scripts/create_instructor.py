"""Create (or reset the password of) one instructor account, e.g. a UAT
tester on Neon prod. Unlike seed_dev, no password lives in this source:
it is read from the VERIDICAL_NEW_PASSWORD environment variable so it
never lands in the repo or in shell argv/history.

    VERIDICAL_NEW_PASSWORD='...' uv run python -m scripts.create_instructor \\
        uat.instructor@veridical.test "UAT Instructor"

Idempotent: an existing email keeps its row and data; only the password
is replaced (pass --keep-password to leave it untouched).
"""

import argparse
import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.security import hash_password
from app.config import get_settings
from app.db import sqlalchemy_url
from app.models import Instructor

PASSWORD_ENV = "VERIDICAL_NEW_PASSWORD"
MIN_PASSWORD_LENGTH = 12


async def create_instructor(
    email: str, display_name: str, password: str, *, keep_password: bool
) -> str:
    settings = get_settings()
    engine = create_async_engine(sqlalchemy_url(settings.database_url))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        existing = await session.scalar(select(Instructor).where(Instructor.email == email))
        if existing is not None:
            if keep_password:
                outcome = f"instructor already present (id={existing.id}); password unchanged"
            else:
                existing.password_hash = hash_password(password)
                await session.commit()
                outcome = f"instructor already present (id={existing.id}); password reset"
        else:
            instructor = Instructor(
                email=email, display_name=display_name, password_hash=hash_password(password)
            )
            session.add(instructor)
            await session.commit()
            outcome = f"instructor created (id={instructor.id})"
    await engine.dispose()
    return f"{outcome} on veridical_env={settings.veridical_env!r}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email")
    parser.add_argument("display_name")
    parser.add_argument("--keep-password", action="store_true")
    args = parser.parse_args()
    password = os.environ.get(PASSWORD_ENV, "")
    if not args.keep_password and len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(
            f"set {PASSWORD_ENV} to a password of at least {MIN_PASSWORD_LENGTH} characters"
        )
    print(
        asyncio.run(
            create_instructor(
                args.email.strip().lower(),
                args.display_name,
                password,
                keep_password=args.keep_password,
            )
        )
    )


if __name__ == "__main__":
    main()
