# ==============================================================================
# Enterprise Multi-Stage Dockerfile for LangGraph SRE Incident Commander
# ==============================================================================

# Build Stage
FROM python:3.11-slim AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir --prefix=/install .

# Production Stage
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="langgraph-sre-incident-commander" \
      org.opencontainers.image.description="Multi-agent SRE incident commander powered by LangGraph" \
      org.opencontainers.image.authors="cibi-dev" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/src"

# Copy installed dependencies from builder
COPY --from=builder /install /usr/local

# Create non-root user for DevSecOps compliance
RUN groupadd -r commander && useradd -r -g commander -u 1001 -m -d /app commander_user

# Copy application code
COPY --chown=commander_user:commander src/ ./src/
COPY --chown=commander_user:commander pyproject.toml README.md ./

USER commander_user

ENTRYPOINT ["sre-commander"]
CMD ["INC-001", "API Gateway 5xx spike and latency degradation", "p1_critical"]
