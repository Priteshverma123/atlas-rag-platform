# One image, many roles (api | gateway | worker | mcp | mockllm | migrate): selected by command.
# Multi-stage: dependencies are resolved from uv.lock in a builder stage; the runtime stage is
# slim, non-root, and contains no build tooling.

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9.30 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# 1) deps only (cached layer as long as the lock file is unchanged)
COPY pyproject.toml uv.lock* README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project $( [ -f uv.lock ] && echo --frozen )
# 2) project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev $( [ -f uv.lock ] && echo --frozen )
# Optional heavy parser (PyTorch). Build with: --build-arg WITH_DOCLING=1
ARG WITH_DOCLING=0
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$WITH_DOCLING" = "1" ]; then uv pip install "docling>=2.15"; fi
# Pre-fetch the tiktoken encoding so containers never need internet access at runtime.
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken
RUN mkdir -p /opt/tiktoken && /app/.venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" || true

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 atlas && useradd --system --uid 10001 --gid atlas --home /app atlas
WORKDIR /app
COPY --from=builder --chown=atlas:atlas /app/.venv /app/.venv
COPY --from=builder --chown=atlas:atlas /opt/tiktoken /opt/tiktoken
COPY --chown=atlas:atlas src ./src
COPY --chown=atlas:atlas config ./config
COPY --chown=atlas:atlas migrations ./migrations
COPY --chown=atlas:atlas alembic.ini ./
COPY --chown=atlas:atlas evals/corpus ./evals/corpus
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken ATLAS_CONFIG_DIR=/app/config
USER atlas
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "atlas.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers"]
