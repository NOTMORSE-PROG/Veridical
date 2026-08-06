FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /srv/veridical
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/srv/veridical/.venv/bin:$PATH"

# Dependencies first so code edits don't bust the layer cache.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
# D-021: the app runs its own migrations at boot in production (auto-migrate
# on startup, keyed off VERIDICAL_ENV=prod) -- alembic.ini and the versions/
# directory must actually be in the image for that, not just app/. Found
# live: the first deploy of that feature crashed the container because
# neither was ever copied here, so Config(alembic.ini) pointed at a file
# that didn't exist.
COPY alembic.ini ./
COPY alembic ./alembic

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
