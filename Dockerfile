# One image for `dgi serve` and `dgi refresh`. Built with Podman; loaded into kind, never pushed.
FROM ghcr.io/astral-sh/uv:0.12 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
# The project pins uv-managed Python for development; in the image the base image's Python is the one to use.
ENV UV_PYTHON_PREFERENCE=only-system UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY config ./config
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin dgi
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" HOME=/tmp DGI_DATA_DIR=/data DGI_SCORING_CONFIG=/app/config/scoring.yaml PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8760
ENTRYPOINT ["dgi"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8760"]
