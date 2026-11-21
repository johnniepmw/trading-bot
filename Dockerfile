# syntax=docker/dockerfile:1
# The bot runs as a Cloud Run job (ADR-0011) and is stdlib-only so far.
# When you add runtime dependencies, add a build stage:
#   COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
#   RUN uv sync --frozen --no-dev
# and copy the resulting .venv into this stage.
# Pinned by digest; Dependabot (docker) proposes updates.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src
WORKDIR /app

# Security updates for OS packages the base image is behind on; drop pip, which the bot
# never uses at runtime and whose vendored libraries (msgpack, ...) carry CVEs.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/* \
 && rm -rf /usr/local/lib/python3.13/site-packages/pip* \
           /usr/local/lib/python3.13/ensurepip/_bundled \
           /usr/local/bin/pip* \
 && useradd --create-home --uid 10001 app
# Root-owned: the runtime user can read the code and flag registry but not change them.
COPY flags.json ./
COPY src ./src

ARG GIT_SHA=dev
ENV APP_VERSION=${GIT_SHA}

USER app
CMD ["python", "-m", "tradingbot.app", "smoke"]
