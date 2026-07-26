# Production image for the AI Content Factory API.
#
# Multi-stage: the builder stage installs from the pinned
# requirements-lock.txt (Production Hardening Sprint H1/DEP1 — reproducible
# builds, not pyproject.toml's intentionally loose >= bounds) into an
# isolated venv, which the runtime stage copies verbatim — the runtime
# image never has a C compiler or build headers in it.
#
# Does NOT install the optional "rendering"/"elevenlabs" extras (see
# requirements-lock.txt's own note) — this image runs with NullRenderer/
# SilentTTSProvider unless you rebuild with those extras added.

FROM python:3.11-slim AS builder

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements-lock.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements-lock.txt

COPY pyproject.toml .
COPY src ./src
RUN pip install --no-cache-dir --no-deps .


FROM python:3.11-slim AS runtime

# libpq5 is the psycopg2-binary runtime dependency; curl is used only by
# the HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --no-create-home app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY alembic ./alembic
COPY alembic.ini .
COPY src ./src
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /app/var/media \
    && chown -R app:app /app

USER app

EXPOSE 8000

# Uses the app's own /health endpoint (checks real DB connectivity, see
# api/main.py) rather than just "is the process alive." Reads $PORT (see
# docker-entrypoint.sh) so this still hits the port the app actually bound
# to on platforms that assign their own (e.g. Render), falling back to the
# same 8000 default used locally/in docker-compose.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/health" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["serve"]
