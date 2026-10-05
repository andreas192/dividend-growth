# Dividend growth screener and company analyzer

Date: 2026-10-05
Status: Draft, awaiting owner review
Depends on: `../investment` HTTP API (`GOLD_SCHEMA.md`, `docs/superpowers/specs/2026-10-05-gold-http-api-design.md`) and, for the cluster step, `../investment/docs/superpowers/specs/2026-10-05-container-deployment-design.md`
Deferred work: `2026-10-05-dgi-roadmap.md`

## Purpose

A personal, read-only web app that turns the investment platform's data into dividend growth investing (DGI) decisions:

1. A **ranked screener** of US companies by a transparent DGI score, with hard filters.
2. A **company analyzer** that shows one company from a DGI angle (dividend record, safety, growth and quality, valuation) in the style of Dividendology-type dashboards.

Success:

- `dgi refresh` against the real local API produces a cache within the memory budget, and a second run with unchanged upstream data does nothing.
- The screener ranks the scored universe, and well-known long-streak names (KO, JNJ, PG) show plausible streaks.
- Every scored company has a page whose score can be explained metric by metric.
- Banks, insurers and REITs appear as "not scored" with a stated reason, never with a misleading payout ratio.
- The app runs as its own namespace (`dgi`) in the `invest` kind cluster without changing anything `../investment` or `../capital-trading` runs.

### Understanding (confirmed by the owner)

Stated by the owner: use the investment API as the only data source; produce a list of companies to invest in; allow analysis of one company from a DGI perspective; follow the Dividendology dashboard style; cache what is specific to this project; run in the same cluster as the investment project.

Decided in discussion: the list is a ranked screener only (no watchlist or holdings); REITs, banks and insurers are excluded and flagged in v1; approach A (refresh job plus web app with a derived-metrics cache).

Assumptions: single user, no login; long-term buy-and-hold; same Mac and Podman machine (about 3.8 GB shared with capital-trading); free data only; the Dividendology channel could not be read, so "style" means the usual DGI dashboard set (yield, growth rates, streak, payout, safety, valuation against own history) and can be refined from example screenshots.

## Decisions

| Topic | Decision |
|---|---|
| Stack | Python 3.12 managed by `uv` (uv-managed interpreter only), DuckDB, `httpx`, `pyarrow`, `pydantic`, `typer`, `starlette` + `uvicorn` + `jinja2`, `pytest`. Charts: Apache ECharts, vendored in the image (no CDN). |
| Data access | Only through the investment HTTP API, pinned to `/v1`. Never gold, silver or bronze files. |
| Cache | Own DuckDB file `dgi.duckdb`, derived and rebuildable. Never the source of truth. |
| Cluster | Namespace `dgi` in the kind cluster `invest`. Plain Kubernetes manifests with kustomize, applied by script. No Terraform. |
| UI access | `kubectl port-forward` to `127.0.0.1:8760` via `scripts/open.sh`. No host port mapping (that is fixed at cluster creation). |
| Repo | `github.com/andreas192/dividend-growth`, branch `develop`. |

## Architecture

```
investment API (/v1, Arrow)  ->  dgi refresh  ->  cache (dgi.duckdb)  ->  dgi serve (web UI)
   in-cluster: api.invest.svc:8750    CronJob              rebuildable            Deployment
```

One repo, one image, four commands:

| Command | Does |
|---|---|
| `dgi refresh` | Runs the pull-and-metrics stage when needed and the score stage after it. `--force` ignores the keys. |
| `dgi serve` | Starlette app over the cache, read-only. |
| `dgi status` | Cache upstream hash, build time, row counts, unscored count by reason. |
| `dgi check` | Cache quality checks (run by `refresh` before a new cache goes live). |

Layout:

```
config/scoring.yaml            pillar weights, metric bands, hard-filter defaults, tolerances
src/dgi/cli.py, pipeline.py    wiring only (no unit spec)
src/dgi/settings.py, report.py, fsutil.py
src/dgi/client/                the only HTTP code: paging, Arrow, dependency check
src/dgi/metrics/               SQL (DuckDB): dividend_annual, fundamentals_annual, metrics_current, flags
src/dgi/scoring/               config model, bands, pillars, coverage, hard filters
src/dgi/cache/                 schema, atomic swap, meta, quality checks
src/dgi/web/                   routes, templates, static (vendored ECharts), JSON series endpoints
tests/                         offline, fixtures built in code
deploy/k8s/                    kustomize base: namespace, PVC, configMapGenerator, web, refresh
scripts/                       install_tools.sh, build_image.sh, deploy.sh, open.sh, check_isolation.sh
docs/                          code-conventions.md, token-strategy.md, rate-limits.md, specs, plans
```

Dependencies flow one way: `client -> metrics -> scoring -> cache -> web`. `web` imports `cache` only; `client` is imported by `pipeline` only.

## Metrics and scoring

Inputs, all present in gold: `dividend_events`, `split_events`, `price_daily` / `price_daily_adjusted`, `company`, and the statement concepts `eps_diluted`, `dividends_per_share`, `dividends_paid`, `cash_from_operations`, `capex`, `revenue`, `net_income`, `operating_income`, `depreciation_amortization`, `interest_expense`, debt concepts, `current_assets`, `current_liabilities`, `total_equity`, `shares_diluted_weighted`.

### Pillars and metrics

| Pillar (default weight) | Metrics |
|---|---|
| Dividend record (30%) | Consecutive years of increases (streak), no-cut streak, 1/3/5/10-year dividend CAGR, yield vs own 5-year average yield, payment frequency |
| Safety (30%) | Payout on EPS and on FCF (current and 5-year average), interest coverage, net debt / EBITDA, current ratio, years with positive earnings in the last 10 |
| Growth and quality (20%) | 5-year CAGR of revenue, EPS and FCF per share; ROE; operating-margin stability; share-count trend |
| Valuation (20%) | P/E, P/FCF, FCF yield, yield vs history, Gordon-growth fair-value range (growth capped, required return in config) shown as a range with a sensitivity grid |

### Scoring rules

- Each metric maps to 0-100 through bands in `config/scoring.yaml`, adapted from `../dividend-growth-analysis/utils/valuation.py` and owner-edited. Absolute bands, not percentiles, so scores do not shift when the universe changes. Percentiles are displayed alongside.
- Missing metrics lower the pillar's **coverage**. The pillar score is a weighted mean over present metrics. A company below the minimum coverage (config) is "insufficient data", not ranked.
- Hard filters (min streak 5, max FCF payout 100%, min market cap $1B, yield range; all defaults in config) are applied separately from the score. The user can change them in the UI.
- Red flags sit outside the score and are always visible: dividend cut in the last 5 years, payout over 100% on EPS or FCF, negative FCF, a Yahoo dividend that looks wrong (a payment at or above the previous close), a detected special dividend.
- The score cannot be a black box: `score_detail` stores the value, band score and pillar contribution of every metric.

### Dividend handling (known pitfalls)

- Gold dividend amounts are **unadjusted**. `metrics/` re-adjusts each payment to today's share basis using `split_events` before summing, so splits never create fake cuts or raises.
- Annual dividend per share is the sum of split-adjusted payments per **calendar year**. Only complete years count; the current year is excluded from streak and CAGR.
- Special dividends: in a year with more payments than the ticker's usual count, the largest payment is special if it is at least 1.5 times the median of that year's other payments. Specials are excluded from the annual total and raise a flag.
- Streak rules and tolerances (a raise is more than `raise_tolerance` above the prior year, a cut is more than `cut_tolerance` below it; defaults 0.1% and 1%) are in config and unit-tested with a cut, a flat year, a split, a special dividend and a partial year.
- Price multiples use TTM figures when four consecutive quarters exist and the latest fiscal year otherwise. The basis is stored per company and shown in the UI.

### Universe

Scored: companies with SIC outside banks, insurers and REITs, at least 5 years of dividend history, and a price. Everything else is listed with `scoring_status = not_scored` and a reason (`financial_or_reit`, `short_dividend_history`, `no_price`, `insufficient_data`). Support for financial companies depends on the investment project and is on the roadmap.

## Data flow and cache

### Refresh, two stages

1. **Pull and metrics.** Runs when the upstream `content_hash`, the contract version or the metrics code version differs from the cache `meta`.
   - `client` checks `/health` and `/contracts/v1`. Needed resources and columns must exist with expected types; additive changes pass, removals or retypes fail with a clear error; a `sunset` contract or 410 fails.
   - Bulk pulls as Arrow with filters, streamed in batches into DuckDB staging tables so memory stays bounded: `company`; `dividend_events`; `split_events`; annual and recent-quarter statement concepts from the `*_latest` views for about 11 years; latest prices and year-end closes from narrow `trade_date` windows (never the full price table).
   - SQL builds `dividend_annual`, `fundamentals_annual`, `metrics_current` and the data-quality flags. Staging tables are dropped.
2. **Score.** Runs after stage 1 and whenever the scoring-config hash changes. Reads only the cache and writes `scores`, `score_detail` and `flags`. Editing `scoring.yaml` never needs an API pull.

### Cache tables (`dgi.duckdb`)

`meta` (upstream hash, upstream build time, contract version, metrics version, scoring hash, built_at), `company_dim`, `dividend_annual`, `fundamentals_annual`, `metrics_current`, `scores`, `score_detail`, `flags`. Pillar scores are stored, so re-weighting in the UI is a weighted sum.

### Safety

- A new cache is written to a temp file, must pass `dgi check`, then replaces the live file atomically. On failure the old cache keeps serving.
- Checks: row counts have not collapsed against the previous cache; no duplicate (company, year); streak never exceeds available history; yield outliers flagged; scored share of the universe is plausible.
- The web app reopens the cache when the file is swapped. With no cache it shows a "run `dgi refresh`" page.

### Drill-down caching

Annual series come from the cache. Daily price and the full-resolution yield-vs-history chart fetch one ticker from the API on demand, held in an in-memory cache keyed by (upstream hash, ticker) so it invalidates itself. If the API is unreachable the charts fall back to annual series with a notice.

### Errors

API unreachable or 503 (gold drift): refresh exits non-zero, the old cache stays, the CronJob shows the failure. Contract deprecated: warning in `/health` and the UI footer. Contract sunset or required column missing: refresh fails.

## Views

### Screener (`/`)

- Filter bar: min streak, yield range, max payout on EPS and FCF, min market cap, min score, sector group, a toggle to show "not scored" with reasons. State is in the URL.
- Pillar-weight sliders, applied server-side to stored pillar scores.
- Table: rank, ticker, name, sector, price, yield, streak, 5-year DGR, FCF payout, pillar scores, total, red flags. Sortable, server-side paged, CSV export.
- Top strip: universe size, scored, passing filters, median yield.
- Chart: yield vs 5-year DGR scatter for the filtered set, size by score, click opens the company.

### Company (`/company/{ticker}`)

- Header with name, sector, price, score badge, pillar bars, red flags in words.
- KPI tiles: yield, 5-year DGR, streak, payout on EPS and FCF, fair-value range and margin of safety.
- Charts: annual dividend per share with growth; yield over time against its 5-year average; payout ratios over time; EPS, FCF per share and dividend per share together; net debt / EBITDA and interest coverage; share count trend.
- Valuation panel: P/E, P/FCF, fair-value sensitivity grid (required return by growth).
- "Why this score": every metric with value, band score, contribution.

### Other

`/methodology` renders the bands and weights from `config/scoring.yaml`. `/health` shows cache hash, build time, counts and contract status. Chart design follows the `dataviz` skill; light and dark themes.

## Deployment

- One `Dockerfile` (`python:3.12-slim`, `uv`, non-root, entrypoint `dgi`, default `serve --host 0.0.0.0 --port 8760`). Tag is a hash of `Dockerfile`, `pyproject.toml`, `uv.lock`, `src/` and `config/`. Built with Podman, loaded into the cluster with `kind load image-archive`, `imagePullPolicy: Never`, no registry.
- Namespace `dgi` objects: PVC for the cache (rebuildable, on the cluster's default storage class); ConfigMap generated from `config/scoring.yaml` (a change rolls the pods and needs no image rebuild); Deployment `web` (one replica, `Recreate`, cache mounted read-only, readiness on `/health`, small memory limit); Service `web` (ClusterIP); CronJob `refresh` (`0 7 * * *`, `Europe/Bucharest`, `concurrencyPolicy: Forbid`, deadline, memory limit from measurement). `DGI_INVEST_API_URL=http://api.invest.svc.cluster.local:8750`.
- Scripts always pass an explicit `--kubeconfig` (default: investment's cluster kubeconfig, read only) and refuse to run unless the context is `kind-invest`. They never use the current kubectl context and never touch the GKE context. `kubectl` and `kind` are pinned binaries in gitignored `.tools/`, installed by `scripts/install_tools.sh` with checksum verification; no Homebrew change.
- `scripts/check_isolation.sh` (adapted from investment's) also confirms the `invest` cluster's other namespaces and `../investment` are unchanged. It runs after apply and delete.
- **Local first.** Until the cluster exists the app runs on the Mac against `invest serve` (`uv run dgi refresh --api http://127.0.0.1:8750`, `uv run dgi serve`), with the cache in gitignored `data/`. Deploy tasks come last in the plan and are gated on investment's cluster spikes passing.
- Resource rule (same as investment): measure the refresh peak and the web memory before fixing limits. If the refresh peak exceeds 1 GiB, stop and redesign (for example, smaller pulls) before writing manifests.

## Project conventions (copied and adapted from `../investment`)

Done as the first task after this spec is approved.

| Source | Result here |
|---|---|
| `CLAUDE.md` | New file on the same skeleton: Podman rule, isolation from capital-trading and from `../investment`, one-way dependencies, API-only data access with the pinned `/v1`, derived and rebuildable cache, dividend pitfalls above, REIT and financial exclusion, cluster rules, offline tests, superpowers workflow, out-of-scope list. |
| `docs/code-conventions.md` | Sections 1-3 kept (minimal functions, wiring has no unit spec, UNIT vs PIPELINE). Examples renamed to this project. Route handlers stay thin and are unit-tested with Starlette's test client over a temp cache. |
| `.claude/hooks/test-layer-guard.mjs`, `.claude/settings.json` | Same hook, patterns changed to `dgi.cli` / `dgi.pipeline`; plugin setting kept. |
| `docs/token-strategy.md` | Nearly verbatim; spec references changed. |
| `docs/rate-limits.md` | Shortened: the only upstream is the local API; bulk pulls, bounded page sizes, no concurrent hammering; external-source limits stay in investment's docs. |
| `scripts/check_isolation.sh` | Adapted as above. |
| `.gitignore`, `pyproject.toml`, `.python-version` | Same conventions (`uv`, uv-managed Python 3.12, hatchling). |
| `.claude/settings.local.json` | Command-permission allowlist only; no environment values (no user agents are needed here). |

Not copied: `GOLD_SCHEMA.md` and `docs/gold_interface.json` (producer-owned; this repo links to them and records the contract hash it was built against), `README.md` and `ONBOARDING.md` (a new README is written), investment's specs, plans and `.superpowers/sdd/` logs.

## Testing

All offline; fixtures built in code. Layers follow `docs/code-conventions.md`.

- **UNIT:** client paging, cursors, Arrow decoding and the dependency check (fake transport); split-adjusted dividend totals, special-dividend detection, streak and CAGR edge cases; every metric formula; score bands, coverage, pillar weighting, hard filters; flags; cache atomic swap and each check; each web route over a temp cache with Starlette's test client; every raise.
- **PIPELINE (`tests/test_cli_pipeline.py` only):** `refresh` on a fake transport then one traced value through the web app; a second `refresh` is a no-op; API down exits non-zero and keeps the old cache.
- **Acceptance against the real local API:** refresh within budget; KO, JNJ, PG streaks plausible; REITs and banks "not scored" with reasons; no-op on unchanged upstream; in the cluster, the CronJob completes, the UI answers through port-forward, and the isolation check passes.

## Out of scope

Everything in the roadmap file, plus authentication or TLS, non-US companies, Terraform, a container registry, Helm, ingress, cloud clusters, and any write to `../investment`.

## Open decisions (with the default used until the owner says otherwise)

1. **Service name.** The in-cluster API address assumes Service `api` in namespace `invest` from investment's draft spec. Verified when that cluster exists; the address is a setting.
2. **UI exposure.** `kubectl port-forward` now. A host port mapping would need investment's `cluster.tf` changed and the cluster recreated, which is a separate decision for the owner.
3. **Starting bands and weights.** Adapted from the old `valuation.py` and reviewed by the owner in the plan stage; they are config, not code.
4. **Sector grouping.** Derived from SIC codes by a small mapping in config.
