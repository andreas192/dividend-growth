# Dividend Growth Screener

Personal, read-only web app that turns the investment platform's data into dividend growth investing (DGI) decisions: a ranked screener and a company analyzer. The only data source is the investment HTTP API (`../investment`). This project never reads gold, silver or bronze files and never writes to `../investment`.

Full design: `docs/superpowers/specs/2026-10-05-dgi-screener-design.md` (not auto-loaded). Read it before making architectural changes. If code and spec disagree, flag it and update the spec rather than silently diverging. Deferred ideas: `docs/superpowers/specs/2026-10-05-dgi-roadmap.md`. Plan: `docs/superpowers/plans/2026-10-05-dgi-screener.md` (an index; one file per phase).

## Status

Implemented per the plan in `docs/superpowers/plans/` (`2026-10-05-dgi-screener.md` and its phase files) and accepted locally against the real API (`docs/acceptance-local.md`); the Kubernetes deployment artifacts are built and tested offline but not yet run in a cluster (plan Task 25 is pending until the `invest` cluster exists). All commands (setup, app, tests, isolation, cluster, git and PRs) live in `docs/command.md`; look them up there instead of guessing, and add new ones there, not here. The upstream contract is `../investment/GOLD_SCHEMA.md` and `../investment/docs/superpowers/specs/2026-10-05-gold-http-api-design.md`; this repo records the contract it was built against and never copies those files.

## Stack

- Python 3.12, managed with `uv` (uv-managed interpreter only)
- DuckDB for the cache and all transforms (SQL-first), `httpx` for the API client, `pyarrow` for Arrow pages, `pydantic` for settings and config models, `typer` for the CLI, `pytest` for tests
- `starlette` + `uvicorn` + `jinja2` for the web UI; charts with Apache ECharts, vendored in the image (no CDN)
- Local Kubernetes: namespace `dgi` in the existing kind cluster `invest`, plain kustomize manifests, no Terraform, no Helm

## Containers (Podman)

- Anything containerized runs with **Podman**, never Docker Desktop or a native install. `docker` is an alias for `podman` on this machine. Write Dockerfiles to work under Podman.
- The image is built locally with Podman and loaded into the cluster with `kind load image-archive`; there is no registry.

## Isolation

From `../capital-trading` (shares this machine; shared Python interpreter; Podman containers on ports 3000, 3100, 4040, 9090, 12345):
- Install Python packages only into this repo's `./.venv` via `uv sync` / `uv run`. Never `pip install`, `conda install`, `uv pip install --system`, or `uv tool install`.
- Don't modify Homebrew Python, miniconda, or shell startup files. Never run `podman machine` commands or stop, restart, or remove existing containers. Never write to `../capital-trading`.

From `../investment` (the producer): never write to it, never read its data directory, never change its cluster objects. Only its HTTP API is used.

Cluster rules: scripts always pass an explicit `--kubeconfig` (default: investment's cluster kubeconfig, read only) and refuse to run unless the context is `kind-invest`. They never use the current kubectl context and never touch the GKE context. `kubectl` and `kind` are pinned binaries in gitignored `.tools/`, installed by `scripts/install_tools.sh` with checksum verification; no Homebrew change. This project owns only namespace `dgi`.

After installing anything or touching the cluster, run `scripts/check_isolation.sh verify` (baseline first with `scripts/check_isolation.sh baseline`).

## Architecture

```
investment API (/v1, Arrow)  ->  dgi refresh  ->  cache (dgi.duckdb)  ->  dgi serve (web UI)
```

Dependencies flow one way: `client -> metrics -> scoring -> cache -> web`. `schema`, `errors`, `settings`, `fsutil` are leaves. `web` imports `cache` only (plus `settings`); `client` is imported by `pipeline` only.

Layout:

```
config/scoring.yaml        pillar weights, metric bands, hard-filter defaults, tolerances, sector map (owner-edited)
src/dgi/cli.py, pipeline.py   wiring only (no unit spec)
src/dgi/errors.py, settings.py, fsutil.py, schema.py, report.py, results.py
src/dgi/client/            the only HTTP code: paging, Arrow, contract check, pull specs, staging
src/dgi/metrics/           SQL (DuckDB) and streak code: dividend, fundamentals and current metrics
src/dgi/scoring/           config model, bands, pillars, fair value, flags, score stage
src/dgi/cache/             meta, atomic swap, quality checks, read-only queries for the web app
src/dgi/web/               routes, templates, static (vendored ECharts)
tests/                     offline, fixtures built in code
deploy/k8s/                kustomize base
scripts/                   install_tools.sh, vendor_echarts.sh, image_tag.sh, build_image.sh, deploy.sh, delete.sh, open.sh, lib.sh, check_isolation.sh
docs/                      code-conventions.md, token-strategy.md, rate-limits.md, command.md, specs, plans
```

## Rules to follow

1. **Data only through the API**, pinned to `/v1`. A removed or retyped column fails the refresh with a clear error; additive changes pass. Never read gold, silver or bronze files.
2. **The cache is derived and rebuildable.** `dgi.duckdb` is never a source of truth. A new cache is written to a temp file, must pass `dgi check`, then replaces the live file atomically. On failure the old cache keeps serving.
3. **Scores are transparent.** Absolute bands from `config/scoring.yaml`, never percentiles (percentiles are shown alongside). `score_detail` holds value, band score and contribution of every metric. Red flags sit outside the score and are always visible. Missing metrics lower coverage; below minimum coverage a company is "insufficient data", not ranked.
4. **Dividend pitfalls.** Gold dividend amounts are unadjusted: re-adjust each payment to today's share basis with `split_events` before summing. Annual dividend per share is the sum per calendar year of complete years only. Special dividends are excluded from the annual total and flagged. Per-share values from filings are restated to today's basis with the splits after the filing date. Streak tolerances come from config and are unit-tested with a cut, a flat year, a split, a special dividend and a partial year.
5. **Banks, insurers and REITs are not scored in v1.** They are listed as `not_scored` with a reason, never with a misleading payout ratio. Other reasons: `short_dividend_history`, `no_price`, `insufficient_data`.
6. **Editing `config/scoring.yaml` never needs an API pull** (except its `metrics:` section, which changes how history is read).
7. **Resource rule:** measure the refresh peak and the web memory before fixing Kubernetes limits. If the refresh peak exceeds 1 GiB, stop and redesign before writing manifests.
8. **The UI is read-only.** No writes anywhere; no authentication or TLS (loopback and port-forward only).

## Testing

- Code and test conventions (minimal functions, UNIT vs PIPELINE test layers) are binding: see `docs/code-conventions.md`. A `PreToolUse` hook (`.claude/hooks/test-layer-guard.mjs`) enforces the layer split.
- Tests run offline. Fixtures are built in code (`tests/fake_api.py`, `tests/sample_gold.py`). Never hit the network or the real API in tests.
- Web tests use Starlette's test client over a temp cache; no sockets.
- Acceptance against the real local API is a separate, manual step (plan Task 23).

## Out of scope (do not add without asking)

Everything in the roadmap file, plus authentication or TLS, non-US companies, Terraform, a container registry, Helm, ingress, cloud clusters, and any write to `../investment`.

## Working conventions

- Follow the superpowers workflow: brainstorm, then spec, then plan, then implement with TDD.
- Keep modules small and single-purpose. If a file grows large, split it.
- Never commit anything under `data/` or `.tools/`.
