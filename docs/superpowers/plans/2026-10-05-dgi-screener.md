# DGI Screener Implementation Plan

**Status:** tasks 1-24 and 26 implemented; Task 25 (in-cluster deploy and acceptance) pending until the `invest` cluster exists

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The plan is split into one file per phase (see "Task map"); read this index, then only the phase file that holds your task.

**Goal:** A read-only web app that ranks US dividend-growth companies by a transparent DGI score and analyzes one company at a time, built only on the investment platform's HTTP API.

**Architecture:** `dgi refresh` pulls Arrow data from the investment API (`/v1`), builds derived metrics in a DuckDB working file, scores them from `config/scoring.yaml`, checks the result and swaps it in atomically as `dgi.duckdb`. `dgi serve` is a Starlette + Jinja2 app that reads that cache read-only. Dependencies flow one way: `client -> metrics -> scoring -> cache -> web`; `schema`, `errors`, `settings` and `fsutil` are leaves any layer may import.

**Tech Stack:** Python 3.12 (uv-managed only), DuckDB, httpx, pyarrow, pydantic, typer, starlette + uvicorn + jinja2, pytest, Apache ECharts (vendored), kustomize manifests on the existing `invest` kind cluster.

**Spec:** `docs/superpowers/specs/2026-10-05-dgi-screener-design.md` (deferred work: `docs/superpowers/specs/2026-10-05-dgi-roadmap.md`). Upstream contract: `../investment/GOLD_SCHEMA.md` and `../investment/docs/superpowers/specs/2026-10-05-gold-http-api-design.md`.

## Global Constraints

Copied from the spec; every task includes them.

- Python 3.12 from `uv` only (`python-preference = "only-managed"`); packages go into this repo's `./.venv` via `uv sync` / `uv run`. Never `pip install`, `conda`, `uv tool install` or `uv pip install --system`.
- Data only through the investment HTTP API, pinned to `/v1`. Never read gold, silver or bronze files.
- The cache (`dgi.duckdb`) is derived and rebuildable; it is never the source of truth.
- Stack: DuckDB, `httpx`, `pyarrow`, `pydantic`, `typer`, `starlette` + `uvicorn` + `jinja2`, `pytest`. Charts: Apache ECharts vendored in the image, no CDN.
- Dependencies flow one way: `client -> metrics -> scoring -> cache -> web`. `web` imports `cache` only (plus `settings`); `client` is imported by `pipeline` only.
- Tests are offline; fixtures are built in code. UNIT tests live beside each module's tests; PIPELINE tests live only in `tests/test_cli_pipeline.py` (`docs/code-conventions.md`).
- Gold dividend amounts are unadjusted: re-adjust each payment to today's share basis with `split_events` before summing. Annual dividend per share is the sum per calendar year of complete years only (the current year is excluded from streak and CAGR).
- Special dividend: in a year with more payments than the ticker's usual count, the largest payment is special if it is at least `special_ratio` (1.5) times the median of that year's other payments; specials are excluded from the annual total and raise a flag.
- Streak tolerances (config defaults): a raise is more than 0.1% above the prior year, a cut is more than 1% below it.
- Banks, insurers and REITs are listed as `not_scored` with reason `financial_or_reit`; other reasons are `short_dividend_history`, `no_price`, `insufficient_data`.
- Scores use absolute bands from `config/scoring.yaml` (never percentiles); percentiles are shown alongside. `score_detail` stores value, band score and contribution of every metric. Red flags sit outside the score.
- A new cache is written to a temp file, must pass `dgi check`, then replaces the live file atomically; on failure the old cache keeps serving.
- Cluster: namespace `dgi` in kind cluster `invest`; plain kustomize manifests applied by script; no Terraform, Helm, ingress or registry. UI through `kubectl port-forward` to `127.0.0.1:8760`. Scripts pass an explicit `--kubeconfig`, refuse any context except `kind-invest`, never touch the GKE context. `kubectl` and `kind` are pinned binaries in gitignored `.tools/`.
- Nothing in `../investment` or `../capital-trading` is written or changed. Image: `python:3.12-slim`, `uv`, non-root, entrypoint `dgi`, `imagePullPolicy: Never`.
- Branch from `develop`; PRs target `develop`. Never commit `data/` or `.tools/`.

## Review Focus

Inputs and conditions the spec implies but does not spell out, most likely to bite first. Each line has a test in the task named in brackets.

1. **A payer that stopped paying** (last payment before last year, or a gap year): streak and no-cut streak are 0, the cut flag is raised, no crash and no stale streak. [Task 8, `test_dividends.py`]
2. **Non-positive earnings or FCF while dividends are paid**: payout is capped at 999% (not NULL, not a division error), P/E is NULL, the red flag fires. [Task 9, `test_fundamentals.py`; Task 13, `test_flags.py`]
3. **Empty or partial API data**: an empty Arrow page, a resource with zero rows, an API that has no prices yet, `gold_built_at` null. Refresh must finish or fail with a named `error:`, never a traceback. [Task 6, `test_client_stage.py`; Task 16, `test_cache_checks.py`; Task 17 PIPELINE]
4. **Cache replaced while the web app runs, and leftovers from a crashed refresh**: the web app closes the old DuckDB connection and reopens (DuckDB caches instances per path inside one process, so reconnecting without closing keeps serving the old file); a stale `.work` or `.new` file is removed at the next refresh. [Task 15, `test_cache_build.py`; Task 18, `test_cache_handle.py`]
5. **Malformed or hostile URL input**: `min_streak=abc`, `page=0`, `sort=ticker;drop table`, an unknown or lower-case ticker, a ticker with slashes, a company name that is markup or a spreadsheet formula. Answer 422 or 404, never 500, never SQL text, never raw markup. [Task 18, `test_cache_screener.py`; Task 21, `test_web_screener.py` and `test_web_app.py`]

## Spec clarifications decided in this plan

The spec left these open or assumed something the API does not do. They are decisions the owner can overturn; each is applied in the task shown.

1. **Upstream change key is `content_hash` plus `gold_built_at`.** The API's `/health.content_hash` is the hash of the *interface* (schema), not of the data, so it does not change when data is refreshed. The cache stores both as `upstream_key` (Task 4, Task 15).
2. **Payout ratios use dollars:** dividends paid / net income and dividends paid / FCF, capped at 999%, set to the cap when the denominator is not positive and dividends are paid. This avoids mixing a split-adjusted dividend per share with an EPS reported on another share basis (Task 9).
3. **TTM basis:** `ttm` when four consecutive quarters of net income, operating cash flow and capex exist and end after the latest fiscal year; EPS is then TTM net income / latest quarterly diluted shares, because gold never derives a Q4 EPS (Task 10).
4. **Per-share and share-count values from filings are restated to today's share basis** with the splits whose ex-date is after the filing date. This is what keeps EPS, FCF per share and share-count trends free of fake jumps (Task 9).
5. **Yield versus its own history is scored once, in the dividend pillar.** The valuation pillar gets the margin of safety from the Gordon fair value, plus P/E, P/FCF and FCF yield (Task 11, 14).
6. **Stage 1 reruns when the `metrics:` section of `scoring.yaml` changes** (tolerances change streaks), in addition to the metrics code version. All other `scoring.yaml` edits rerun only the score stage (Task 15).
7. **Two cache tables beyond the spec's list:** `dividend_payment` (split-adjusted payments, needed for the daily yield chart) and `price_yearend` (needed for yield history). `schema.py` sits at the package top level so every layer shares one DDL without breaking the one-way dependency rule (Task 3).
8. **Cache file compaction:** stage 1 stages API data in the working file, then copies only the persisted tables into a fresh file, so staging space never reaches the PVC (Task 15).
9. **The web app gets daily prices through an injected source** built by `pipeline`, so `client` stays imported by `pipeline` only (Tasks 20 and 22). `tests/test_architecture.py` enforces the dependency rule (Task 17).
10. **Excluded SIC ranges** are 6000-6199 (banks, credit), 6300-6411 (insurance) and 6798 (REITs) in `config/scoring.yaml`; asset managers (62xx) stay scored. The owner can change the ranges.
11. **Missing `dividends_paid` is estimated** as the year's regular dividend per share times diluted shares, and marked (`dividends_estimated`). Found on real data: JNJ reports none, so its FCF payout was missing (Task 9).
12. **The streak is bounded by the data's dividend history** (about 1970 for old companies), so KO and JNJ show `55+` against real records of about 63 years; the UI adds a plus to a streak that reaches the start of the history (Tasks 20-21).
13. **`/health` always answers 200** (`{"status": "no_cache"}` before the first refresh) so Kubernetes readiness passes and the "no data yet" page is reachable; deprecation notices from the contract check are stored in the cache meta and shown in the footer (Tasks 15, 21).
14. **The company page's daily charts are optional**: without a price source, or with the API down, the page keeps its annual charts and says so (Task 21).

## Task map

| Phase file | Tasks | Delivers |
|---|---|---|
| `dgi-screener/01-foundation.md` | 1-3 | Conventions, package skeleton, settings, cache schema |
| `dgi-screener/02-client.md` | 4-6 | API client, contract check, pull specs and staging |
| `dgi-screener/03-metrics.md` | 7-10 | Dividend, fundamentals and current metrics (SQL + streak code) |
| `dgi-screener/04-scoring.md` | 11-14 | Scoring config, bands, pillars, fair value, flags, score stage |
| `dgi-screener/05-cache-pipeline.md` | 15-17 | Meta, atomic swap, checks, `refresh` / `status` / `check` commands |
| `dgi-screener/06-web.md` | 18-22 | Cache queries, web support modules, web app (screener, company page), `serve` |
| `dgi-screener/07-acceptance-local.md` | 23 | Real-API acceptance, memory measurement, spec update (gate for deploy) |
| `dgi-screener/08-deploy.md` | 24-26 | Image, scripts and manifests; in-cluster acceptance (needs investment's cluster); docs and PR |

Tasks run in order. Task 23 is the gate for the deployment phase: if the measured refresh peak exceeds 1 GiB, stop and redesign (spec resource rule). Task 24 is files and offline checks and can follow Task 23 directly; Tasks 25-26 need investment's cluster `invest`, which does not exist yet (`../investment/deploy/` is absent) and is not this project's to create.

## A dry run on the real data

Before this plan was finalized, the design was run end to end on a scratch copy of this code against the local investment API (and, for the web layer, rendered in a headless browser). It refreshed 6,716 companies in about 15 s with a 690 MB peak, scored about 926, and surfaced the two data issues behind clarifications 11 and 12. The plan's code blocks for Tasks 1-22 were then extracted from these files and run: they reproduce the verified implementation byte for byte and its test suite passes (about 350 tests). The expected numbers are in `dgi-screener/07-acceptance-local.md`.

## File structure (what each file is responsible for)

```
CLAUDE.md, README.md, pyproject.toml, .python-version, .gitignore, Dockerfile, .dockerignore
config/scoring.yaml                    weights, bands, filters, tolerances, sector map (owner-edited)
src/dgi/errors.py                      typed errors; the CLI guard maps DgiError to exit 1
src/dgi/settings.py                    Settings (env), API_VERSION
src/dgi/fsutil.py                      atomic_replace, file_sha256, text_sha256
src/dgi/schema.py                      table DDL (single source of truth), arrow schemas, insert_rows
src/dgi/report.py                      guard, describe_refresh, describe_status, print_checks, exposure_warning
src/dgi/results.py                     RefreshResult (shared by pipeline and report)
src/dgi/cli.py, pipeline.py            wiring only (refresh, status, check, serve)
src/dgi/client/                        http.py (ApiClient), contract.py, pulls.py, stage.py, series.py: the only HTTP code
src/dgi/metrics/                       params.py, sqlrun.py, dividends.py, __init__.py (build_metrics), sql/*.sql
src/dgi/scoring/                       config.py, bands.py, pillars.py, valuation.py, flags.py, stage.py
src/dgi/cache/                         meta.py, build.py, checks.py, status.py, handle.py, screener.py, company.py
src/dgi/web/                           app.py, series.py, format.py, templates/, static/ (charts.js, app.css, vendor/echarts)
tests/                                 offline; fake_api.py, sample_gold.py, cache_fixtures.py, web_fixtures.py build data in code
deploy/k8s/                            kustomize base: namespace, PVC, web, service, refresh CronJob
scripts/                               install_tools.sh, vendor_echarts.sh, image_tag.sh, build_image.sh, deploy.sh, delete.sh, open.sh, lib.sh, check_isolation.sh
docs/                                  code-conventions.md, token-strategy.md, rate-limits.md, command.md, acceptance-local.md, specs, plans
```
