# syntax=docker/dockerfile:1
# The bot runs as a Cloud Run job (ADR-0011) and is stdlib-only so far.
# When you add runtime dependencies, add a build stage:
#   COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
#   RUN uv sync --frozen --no-dev
# and copy the resulting .venv into this stage.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src
WORKDIR /app

RUN useradd --create-home --uid 10001 app
COPY --chown=app:app flags.json ./
COPY --chown=app:app src ./src

ARG GIT_SHA=dev
ENV APP_VERSION=${GIT_SHA}

USER app
CMD ["python", "-m", "tradingbot.app", "smoke"]
