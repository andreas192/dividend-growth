# Phase 1: Foundation (Tasks 1-3)

Part of `../2026-10-05-dgi-screener.md` (read its Global Constraints and Review Focus first). Spec: `../../specs/2026-10-05-dgi-screener-design.md`.

Run every command from the repo root `/Users/rickymicky/Projects/Personal/dividend-growth`.

---

### Task 1: Project conventions and repo hygiene

The spec says this is the first task once the spec is approved. It copies and adapts investment's working conventions. No Python code yet; the verification is the hook behaving.

**Files:**
- Create: `.gitignore`, `CLAUDE.md`, `README.md` (replace the one-line stub), `docs/code-conventions.md`, `docs/token-strategy.md`, `docs/rate-limits.md`, `docs/command.md`, `.claude/settings.json`, `.claude/hooks/test-layer-guard.mjs`, `scripts/check_isolation.sh`
- Not copied (producer-owned or investment-specific): `GOLD_SCHEMA.md`, `docs/gold_interface.json`, `ONBOARDING.md`, investment's specs, plans and `.superpowers/` logs.

**Interfaces:**
- Produces: the PreToolUse hook that denies wiring tests (`CliRunner`, `run_refresh(`, `dgi.cli`, `dgi.pipeline`) outside `tests/test_cli_pipeline.py`; `scripts/check_isolation.sh baseline|verify` (extended with cluster checks in Task 24).

- [ ] **Step 1: Create the working branch**

```bash
git switch -c feat/dgi-v1
git status --short
```
Expected: `Switched to a new branch 'feat/dgi-v1'`, clean tree.

- [ ] **Step 2: Write `.gitignore`**

```gitignore
data/
.venv/
.tools/
__pycache__/
*.pyc
.pytest_cache/
.DS_Store
.env
*.duckdb
*.duckdb.wal
```

- [ ] **Step 3: Write `scripts/check_isolation.sh` and record the baseline before anything is installed**

```sh
#!/bin/sh
# Checks that work in this repo has not changed anything ../capital-trading or ../investment depends on.
#   scripts/check_isolation.sh baseline   record the current state (run before installing anything)
#   scripts/check_isolation.sh verify     re-record and diff against the baseline; exit 1 on any difference
# Read-only: it only inspects. If the only difference is in a "repo" section, check whether you were
# editing that repo yourself.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CT="$ROOT/../capital-trading"
INV="$ROOT/../investment"
BASELINE="${TMPDIR:-/tmp}/dgi-isolation-baseline.txt"

snapshot() {
  echo "## python and pip on PATH"
  which -a python3 python pip pip3 2>/dev/null
  echo "## Homebrew python packages"
  /opt/homebrew/bin/python3 -m pip freeze 2>/dev/null | sort
  echo "## miniconda base packages"
  "$HOME/miniconda3/bin/python" -m pip freeze 2>/dev/null | sort
  echo "## miniconda environments"
  ls -1 "$HOME/miniconda3/envs" 2>/dev/null
  echo "## Homebrew formulae (uv excluded)"
  HOMEBREW_NO_AUTO_UPDATE=1 brew list --versions 2>/dev/null | grep -v '^uv ' | sort
  echo "## ~/.local/bin"
  ls -1 "$HOME/.local/bin" 2>/dev/null
  echo "## shell startup files"
  shasum "$HOME/.zshrc" "$HOME/.zprofile" "$HOME/.bash_profile" 2>/dev/null
  echo "## podman containers and machine"
  podman ps -a --format '{{.Names}} {{.Image}} {{.Ports}}' 2>/dev/null | sort
  podman machine list --format '{{.Name}} running={{.Running}}' 2>/dev/null
  echo "## capital-trading repo"
  git -C "$CT" rev-parse HEAD 2>/dev/null
  git -C "$CT" status --porcelain 2>/dev/null
  shasum "$CT/requirements.txt" 2>/dev/null
  echo "## investment repo"
  git -C "$INV" rev-parse HEAD 2>/dev/null
  git -C "$INV" status --porcelain 2>/dev/null
}

case "${1:-}" in
  baseline)
    snapshot > "$BASELINE"
    echo "Baseline saved to $BASELINE ($(wc -l < "$BASELINE" | tr -d ' ') lines)"
    ;;
  verify)
    if [ ! -f "$BASELINE" ]; then
      echo "No baseline found. Run: scripts/check_isolation.sh baseline" >&2
      exit 2
    fi
    CURRENT="$(mktemp)"
    snapshot > "$CURRENT"
    if diff -u "$BASELINE" "$CURRENT"; then
      echo "OK: ../capital-trading and ../investment are unchanged."
      rm -f "$CURRENT"
    else
      echo "DIFFERENCE DETECTED (see diff above). Stop and report it." >&2
      rm -f "$CURRENT"
      exit 1
    fi
    ;;
  *)
    echo "usage: $0 baseline|verify" >&2
    exit 2
    ;;
esac
```

```bash
chmod +x scripts/check_isolation.sh
scripts/check_isolation.sh baseline
scripts/check_isolation.sh verify
```
Expected: `Baseline saved to ... (N lines)`, then `OK: ../capital-trading and ../investment are unchanged.` If `../investment` has uncommitted edits the baseline records them; that is fine as long as they do not change during the work.

- [ ] **Step 4: Write `docs/code-conventions.md`**

````markdown
# Coding standard: minimal, unit-testable functions and respected test layers

This applies to every **new or adapted** function in this repo, whether written
by a person or by an AI assistant. CLAUDE.md points here; treat it as binding.
When a rule here and older code disagree, follow this file and leave the old
code alone unless you are already changing it.

## 1. Functions are minimal

- One function does one thing and is named for that thing. If you need "and"
  to describe it, split it.
- Aim for a body you can read without scrolling. A branch, a loop, or a call
  to an external system is a reason to consider extracting.
- Inputs are explicit parameters. No reaching into module globals,
  `os.environ` or `Settings.from_env()` from inside a domain function. The CLI
  command (or `pipeline.py`) reads those and passes plain values or a
  `Settings` in.
- I/O dependencies are injected, never created inside the function, so it can
  be tested offline: the `httpx` transport (`transport=`), the clock (`today=`,
  `now=`), paths (`Settings`), and DuckDB connections.
- Errors are explicit. Raise a typed exception (a `DgiError` subclass from
  `dgi/errors.py`; the CLI's `guard` turns those into exit code 1) at the point
  where the condition is detected. No bare `except`, no `except Exception:
  pass`, no returning `None` to mean "failed". `None` is only for "legitimately
  absent" (for example a company with no price).
- Type-hint every signature. Inputs and outputs that cross a function boundary
  are a named type (`pydantic` model, `dataclass`, or a `pyarrow` schema), not
  a bare `dict` or tuple.
- Transforms are SQL (DuckDB). A function that builds a query takes its inputs
  as parameters and returns a result. Streak, CAGR and scoring arithmetic that
  does not belong in SQL is a pure function.

## 2. Functions are unit-testable, and the wiring is not the unit

`cli.py` commands and `pipeline.py` are wiring: read settings, call functions
in order, print or return the result. They have **no unit spec** of their own.
All logic lives in extracted functions, each with its own test beside the
others in `tests/`.

A build step splits into fetch, validate, stage, transform, score, persist,
check and format. Each part that exists is its own function in the module that
owns it (`stage_pull`, `build_metrics`, `score_cache`, `run_cache_checks`,
`swap_in`). Output formatting lives in `report.py`, not in `cli.py`, so it can
be unit tested without importing the CLI. Route handlers stay thin and are
unit-tested with Starlette's test client over a temp cache.

Rules that follow from this:

- **Every build step returns a named result** (`MetricsResult`, `ScoreResult`,
  `RefreshResult`, `CheckResult`), not `print`ed text or an untyped dict. The
  CLI formats it.
- **Every function that raises owns the unit tests for every one of those
  raises.** If a command contains `if not x: raise ...`, that branch is logic.
  Move it into a function and test it there.
- A CLI command with an `if` beyond "nothing to do, say so" has not finished
  being decomposed.
- When you **adapt** an existing function, extract the part you touch into a
  tested function if it is not already one. Do not refactor the parts you are
  not touching.
- Extraction respects the dependency rule in CLAUDE.md.

## 3. Test layers are respected, never duplicated

The repo has two test layers. Each proves one thing. A scenario appears in
exactly one of them. All tests run offline (CLAUDE.md "Testing").

### UNIT (`tests/test_<module>.py`)

Proves branches inside a single function or module: the full validation
matrix, every raised error, edge values. Inputs are hand-built. `tmp_path` and
an in-memory or temp DuckDB are fine; the network and the real `data/` are
not. This is the **only** layer where "comprehensive" is the goal.

### PIPELINE (`tests/test_cli_pipeline.py` only)

Proves the wiring works end to end against a fake API transport and a temp
data dir: CLI commands are registered, `refresh` chains the stages, a second
run is a no-op, the cache is readable by the web app. Write only:

1. Happy path, asserting the shape of the result and one traced value.
2. Idempotence: a second run changes nothing.
3. At most one clean-failure test per command (non-zero exit, `error:` on
   stderr) for a missing precondition.

Do **not** enumerate data-level cases here. Streak edge cases, payout caps,
band interpolation, filter parsing: those are branches inside a function and
are already covered by that function's UNIT test.

### Litmus test

If a test would still pass with `cli.py` and `pipeline.py` replaced by stubs
that return the same exit code, it is a UNIT test and belongs beside the
function it proves.

### In plans and PRs

Tag each new test UNIT or PIPELINE and name the function or seam it proves. A
PIPELINE row that reads like a parsing or validation rule is in the wrong layer.

## Worked example

The `check` command is wiring only; each helper has its own test:

```python
@app.command()
def check() -> None:
    settings = Settings.from_env()
    results = guard(lambda: run_check(settings))
    print_checks(results)
    if not all(r.passed for r in results):
        raise typer.Exit(code=1)
```

Tests that result:

| Layer    | Test                                                     | Proves                          |
| -------- | -------------------------------------------------------- | ------------------------------- |
| UNIT     | each check in `cache/checks.py` on good and bad tables   | every `passed=False` branch     |
| UNIT     | `guard` turns `DgiError` into `typer.Exit(1)`            | the error-to-exit-code branch   |
| UNIT     | `print_checks` formats PASS and FAIL lines               | output format                   |
| PIPELINE | `refresh` on a fake API, then one value through the web app | commands, stages, cache wired |
| PIPELINE | second `refresh` is a no-op                              | idempotence                     |
| PIPELINE | `refresh` with the API down exits non-zero               | failure reaches the shell       |
````

- [ ] **Step 5: Write `docs/token-strategy.md` (investment's file with two references changed)**

```bash
cp ../investment/docs/token-strategy.md docs/token-strategy.md
sed -i '' \
  -e 's|Reference only the spec and `GOLD_SCHEMA.md`\.|Reference only the spec and `../investment/GOLD_SCHEMA.md`.|' \
  -e 's|Never print bronze or silver files\. Use DuckDB queries with `LIMIT`\.|Never print the cache or API payloads whole. Use DuckDB queries with `LIMIT`.|' \
  docs/token-strategy.md
grep -n 'GOLD_SCHEMA\|bronze\|silver\|Never print' docs/token-strategy.md
```
Expected: exactly two matching lines, showing `../investment/GOLD_SCHEMA.md` and `Never print the cache or API payloads whole.`

- [ ] **Step 6: Write `docs/rate-limits.md` (shortened)**

```markdown
# Rate limits

The only upstream here is the local investment API (`../investment`, `invest serve`). It is our own server, not a rate-limited source; external-source limits (SEC EDGAR, Yahoo) stay in `../investment/docs/rate-limits.md`.

## Rules

- Pull in bulk, never per ticker: Arrow pages of up to `page_limit` rows (default 100,000, setting `DGI_PAGE_LIMIT`), cursor-paged, one request at a time. No concurrent hammering of the API.
- The only per-ticker request is the company page's daily price series, held in an in-memory cache keyed by (upstream key, ticker).
- A bulk pull never asks for the whole `price_daily` table: latest prices come from a narrow `trade_date` window and year-end closes from December windows.
- If the API answers 503, `dgi refresh` stops and keeps the old cache; it never retries in a tight loop (the CronJob schedule is the retry).

## Tests

Paging and error mapping are covered with a fake transport (`tests/test_client_http.py`). No real sleeps and no network.
```

- [ ] **Step 7: Write `docs/command.md`**

````markdown
# Commands

Every command assumes the repo root as working directory. Run Python only through `uv run` (CLAUDE.md "Isolation").

## Setup (once)

```bash
uv python install 3.12 --no-bin
uv sync                                  # deps into ./.venv only
scripts/check_isolation.sh baseline      # before installing anything new
```

## App (local, against `invest serve`)

```bash
(cd ../investment && uv run invest serve)   # in another terminal: API on 127.0.0.1:8750
uv run dgi refresh                          # pull, build metrics, score, check, swap; --force ignores the keys
uv run dgi status                           # cache upstream key, build time, counts, not-scored by reason
uv run dgi check                            # quality checks on the live cache
uv run dgi serve                            # web UI on 127.0.0.1:8760
```

Settings (env): `DGI_INVEST_API_URL` (default `http://127.0.0.1:8750`), `DGI_DATA_DIR` (default `data`), `DGI_SCORING_CONFIG` (default `config/scoring.yaml`), `DGI_HOST`, `DGI_PORT`.

## Tests

```bash
uv run pytest                              # whole offline suite
uv run pytest tests/test_dividends.py -q   # one file
uv run pytest tests/test_dividends.py -k streak
```

## Isolation

```bash
scripts/check_isolation.sh baseline   # before installing
scripts/check_isolation.sh verify     # after; exit 1 on any difference
```

## Git and PRs

- Branch from `develop`, open PRs against `develop` (`gh pr create --base develop`). `main` is the main branch.
- Never commit anything under `data/` or `.tools/`.

## Cluster (added in Tasks 24-26)

```bash
scripts/install_tools.sh      # pinned kubectl and kind into .tools/
scripts/build_image.sh        # podman build, kind load
scripts/deploy.sh             # kustomize apply into namespace dgi
scripts/open.sh               # port-forward web to 127.0.0.1:8760
```
````

- [ ] **Step 8: Write the test-layer guard hook and `.claude/settings.json`**

`.claude/hooks/test-layer-guard.mjs`:

```javascript
#!/usr/bin/env node
// PreToolUse hook (Write|Edit). Enforces docs/code-conventions.md at the
// moment a test file is written, so the rule reaches every model regardless
// of what it read earlier.
//
//  - Denies CLI/pipeline wiring tests (CliRunner, run_refresh, dgi.cli,
//    dgi.pipeline) outside tests/test_cli_pipeline.py: wiring has no unit
//    spec, logic gets a UNIT test.
//  - Injects a pointer to the PIPELINE-layer contract when tests/test_cli_pipeline.py is touched.

import { readFileSync } from "node:fs";

const input = JSON.parse(readFileSync(0, "utf8"));
const file = input?.tool_input?.file_path ?? "";
const text = input?.tool_input?.content ?? input?.tool_input?.new_string ?? "";

const PIPELINE_TEST = /(^|\/)tests\/test_cli_pipeline\.py$/;
const ANY_TEST = /(^|\/)tests\/[^/]+\.py$/;
const WIRING = /\bCliRunner\b|\brun_refresh\s*\(|from dgi\.(cli|pipeline) import|import dgi\.(cli|pipeline)\b|from dgi import (cli|pipeline)\b/;

const out = (hookSpecificOutput) =>
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        ...hookSpecificOutput,
      },
    }),
  );

if (PIPELINE_TEST.test(file)) {
  out({
    additionalContext:
      "Follow docs/code-conventions.md section 3, PIPELINE layer: happy path, idempotence, at most one clean-failure per command. " +
      "No data-level cases; those belong in the function's UNIT test.",
  });
} else if (ANY_TEST.test(file) && WIRING.test(text)) {
  out({
    permissionDecision: "deny",
    permissionDecisionReason:
      "docs/code-conventions.md: cli.py and pipeline.py are wiring and get no unit spec. " +
      "Test the function directly in a UNIT test beside its module's tests.",
  });
}
```

`.claude/settings.json`:

```json
{
  "enabledPlugins": {
    "ponytail@ponytail": true
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "node \"$CLAUDE_PROJECT_DIR/.claude/hooks/test-layer-guard.mjs\""
          }
        ]
      }
    ]
  }
}
```

`.claude/settings.local.json` (a command allowlist only, no environment values: no user agents are needed here; the file is ignored by git globally on this machine, so it is not committed):

```json
{
  "permissions": {
    "allow": [
      "Bash(uv sync *)",
      "Bash(uv run *)",
      "Bash(scripts/check_isolation.sh verify *)",
      "Bash(git add *)",
      "Bash(git commit *)"
    ]
  }
}
```

- [ ] **Step 9: Write `CLAUDE.md`**

````markdown
# Dividend Growth Screener

Personal, read-only web app that turns the investment platform's data into dividend growth investing (DGI) decisions: a ranked screener and a company analyzer. The only data source is the investment HTTP API (`../investment`). This project never reads gold, silver or bronze files and never writes to `../investment`.

Full design: `docs/superpowers/specs/2026-10-05-dgi-screener-design.md` (not auto-loaded). Read it before making architectural changes. If code and spec disagree, flag it and update the spec rather than silently diverging. Deferred ideas: `docs/superpowers/specs/2026-10-05-dgi-roadmap.md`. Plan: `docs/superpowers/plans/2026-10-05-dgi-screener.md` (an index; one file per phase).

## Status

Under construction per the plan. All commands (setup, app, tests, isolation, cluster, git and PRs) live in `docs/command.md`; look them up there instead of guessing, and add new ones there, not here. The upstream contract is `../investment/GOLD_SCHEMA.md` and `../investment/docs/superpowers/specs/2026-10-05-gold-http-api-design.md`; this repo records the contract it was built against and never copies those files.

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
src/dgi/errors.py, settings.py, fsutil.py, schema.py, report.py
src/dgi/client/            the only HTTP code: paging, Arrow, contract check, pull specs, staging
src/dgi/metrics/           SQL (DuckDB) and streak code: dividend, fundamentals and current metrics
src/dgi/scoring/           config model, bands, pillars, fair value, flags, score stage
src/dgi/cache/             meta, atomic swap, quality checks, read-only queries for the web app
src/dgi/web/               routes, templates, static (vendored ECharts)
tests/                     offline, fixtures built in code
deploy/k8s/                kustomize base
scripts/                   install_tools.sh, vendor_echarts.sh, build_image.sh, deploy.sh, open.sh, check_isolation.sh
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
````

- [ ] **Step 10: Write `README.md`**

````markdown
# dividend-growth

A personal, read-only dividend growth investing (DGI) screener and company analyzer. It ranks US companies by a transparent score and shows one company from a DGI angle (dividend record, safety, growth and quality, valuation). It reads only the `../investment` platform's HTTP API.

```
investment API  ->  dgi refresh  ->  dgi.duckdb cache  ->  dgi serve (http://127.0.0.1:8760)
```

- Design: `docs/superpowers/specs/2026-10-05-dgi-screener-design.md`
- Commands: `docs/command.md`
- Scoring is configured in `config/scoring.yaml` (weights, bands, filters). The `/methodology` page shows the active values.
- Banks, insurers and REITs are listed as "not scored" in v1.

This is research tooling, not investment advice.
````

- [ ] **Step 11: Verify the hook**

```bash
echo '{"tool_input":{"file_path":"tests/test_x.py","content":"from dgi.cli import app"}}' | node .claude/hooks/test-layer-guard.mjs
echo '{"tool_input":{"file_path":"tests/test_x.py","content":"from dgi.metrics import x"}}' | node .claude/hooks/test-layer-guard.mjs
echo '{"tool_input":{"file_path":"tests/test_cli_pipeline.py","content":"x"}}' | node .claude/hooks/test-layer-guard.mjs
```
Expected: the first prints JSON containing `"permissionDecision":"deny"`; the second prints nothing; the third prints JSON containing `additionalContext`.

- [ ] **Step 12: Commit**

```bash
git add .gitignore CLAUDE.md README.md docs/code-conventions.md docs/token-strategy.md docs/rate-limits.md docs/command.md .claude scripts docs/superpowers/plans
git commit -m "chore: project conventions, hook, isolation script and plan"
```
(`.claude/settings.local.json` is globally gitignored; do not add it.)

---

### Task 2: Package skeleton, settings, errors, fsutil, CLI guard

> **Superseded in part by Task 27 (`09-hardening.md`):** `errors.py` also defines `STORAGE_ERRORS`, and `guard` catches only those DuckDB errors (not every `duckdb.Error`).

**Files:**
- Create: `pyproject.toml`, `.python-version`, `src/dgi/__init__.py`, `src/dgi/errors.py`, `src/dgi/settings.py`, `src/dgi/fsutil.py`, `src/dgi/report.py`, `src/dgi/cli.py`
- Test: `tests/test_settings.py`, `tests/test_fsutil.py`, `tests/test_report.py`

**Interfaces:**
- Produces:
  - `dgi.errors`: `DgiError(RuntimeError)`; subclasses `ConfigError`, `ApiUnavailable`, `ApiError`, `ContractError`, `CacheCheckError`, `CacheMissing`.
  - `dgi.settings`: `API_VERSION = "v1"`; `Settings(api_url, data_dir, scoring_path, host, port, page_limit, http_timeout)` with property `cache_path -> Path` and classmethod `from_env(env: Mapping[str, str] | None = None) -> Settings`.
  - `dgi.fsutil`: `atomic_replace(src: Path, dst: Path) -> None`, `file_sha256(path: Path) -> str`, `text_sha256(text: str) -> str`.
  - `dgi.report`: `guard(fn: Callable[[], T]) -> T`.
  - `dgi.cli`: `app = typer.Typer(...)`.

- [ ] **Step 1: Write `.python-version`, `pyproject.toml` and install**

`.python-version`:
```
3.12
```

`pyproject.toml`:
```toml
[project]
name = "dgi"
version = "0.1.0"
description = "Dividend growth screener and company analyzer over the investment platform API"
requires-python = ">=3.12"
dependencies = [
    "duckdb>=1.1",
    "httpx>=0.27",
    "jinja2>=3.1",
    "pyarrow>=17",
    "pydantic>=2.7",
    "pyyaml>=6",
    "starlette>=1.7.0",
    "typer>=0.12",
    "uvicorn>=0.54.0",
]

[dependency-groups]
dev = ["pytest>=8", "httpx2>=2"]  # httpx2: what Starlette's TestClient now asks for

[project.scripts]
dgi = "dgi.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/dgi"]

[tool.uv]
# Use only the uv-managed Python, never Homebrew's or miniconda's.
python-preference = "only-managed"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

```bash
mkdir -p src/dgi tests
touch src/dgi/__init__.py
uv python install 3.12 --no-bin
uv sync
scripts/check_isolation.sh verify
```
Expected: `uv sync` creates `.venv` and `uv.lock`; verify prints `OK`.

- [ ] **Step 2: Write the failing tests**

`tests/test_settings.py` (UNIT: `Settings`):
```python
from pathlib import Path

from dgi.settings import API_VERSION, Settings


def test_defaults():
    s = Settings()
    assert s.api_url == "http://127.0.0.1:8750"
    assert s.cache_path == Path("data/dgi.duckdb")
    assert s.port == 8760
    assert API_VERSION == "v1"


def test_from_env_overrides():
    s = Settings.from_env(
        {"DGI_INVEST_API_URL": "http://api.invest.svc.cluster.local:8750", "DGI_DATA_DIR": "/data", "DGI_PORT": "9000", "DGI_HOST": "0.0.0.0"}
    )
    assert s.api_url == "http://api.invest.svc.cluster.local:8750"
    assert s.cache_path == Path("/data/dgi.duckdb")
    assert s.port == 9000
    assert s.host == "0.0.0.0"


def test_from_env_ignores_unrelated_variables():
    assert Settings.from_env({"PATH": "/usr/bin"}) == Settings()
```

`tests/test_fsutil.py` (UNIT: `fsutil`):
```python
from dgi.fsutil import atomic_replace, file_sha256, text_sha256


def test_atomic_replace_moves_source_over_destination(tmp_path):
    src, dst = tmp_path / "new", tmp_path / "live"
    dst.write_text("old")
    src.write_text("new")
    atomic_replace(src, dst)
    assert dst.read_text() == "new"
    assert not src.exists()


def test_text_sha256_is_the_known_digest():
    assert text_sha256("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_file_sha256_matches_text_sha256(tmp_path):
    path = tmp_path / "f.yaml"
    path.write_text("abc")
    assert file_sha256(path) == text_sha256("abc")
```

`tests/test_report.py` (UNIT: `guard`):
```python
import pytest
import typer

from dgi.errors import ApiUnavailable, DgiError
from dgi.report import guard


def test_guard_returns_the_value():
    assert guard(lambda: 41 + 1) == 42


def test_guard_turns_dgi_errors_into_exit_1_with_error_on_stderr(capsys):
    def boom():
        raise ApiUnavailable("cannot reach the API")

    with pytest.raises(typer.Exit) as exc:
        guard(boom)
    assert exc.value.exit_code == 1
    assert "error: cannot reach the API" in capsys.readouterr().err


def test_guard_lets_other_exceptions_through():
    def bug():
        raise KeyError("bug")

    with pytest.raises(KeyError):
        guard(bug)


def test_every_error_type_is_a_dgi_error():
    from dgi import errors

    for name in ("ConfigError", "ApiUnavailable", "ApiError", "ContractError", "CacheCheckError", "CacheMissing"):
        assert issubclass(getattr(errors, name), DgiError)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_settings.py tests/test_fsutil.py tests/test_report.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dgi.settings'` (and the others).

- [ ] **Step 4: Write the implementation**

`src/dgi/errors.py`:
```python
"""Typed errors. The CLI's guard turns any DgiError into `error: ...` on stderr and exit code 1."""


class DgiError(RuntimeError):
    pass


class ConfigError(DgiError):
    """config/scoring.yaml is missing or invalid."""


class ApiUnavailable(DgiError):
    """The investment API cannot be reached or answers 503 (gold drift or missing)."""


class ApiError(DgiError):
    """The investment API answered something this client does not expect (4xx, bad body)."""


class ContractError(DgiError):
    """The API contract is retired, or a required resource or column is missing or retyped."""


class CacheCheckError(DgiError):
    """A newly built cache failed its quality checks; the old cache keeps serving."""


class CacheMissing(DgiError):
    """There is no cache file yet (or it cannot be opened); run `dgi refresh`."""
```

`src/dgi/settings.py`:
```python
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel

API_VERSION = "v1"

_ENV_NAMES = {
    "api_url": "DGI_INVEST_API_URL",
    "data_dir": "DGI_DATA_DIR",
    "scoring_path": "DGI_SCORING_CONFIG",
    "host": "DGI_HOST",
    "port": "DGI_PORT",
    "page_limit": "DGI_PAGE_LIMIT",
}


class Settings(BaseModel):
    api_url: str = "http://127.0.0.1:8750"
    data_dir: Path = Path("data")
    scoring_path: Path = Path("config/scoring.yaml")
    host: str = "127.0.0.1"
    port: int = 8760
    page_limit: int = 100_000
    http_timeout: float = 120.0

    @property
    def cache_path(self) -> Path:
        return self.data_dir / "dgi.duckdb"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env
        return cls(**{field: source[name] for field, name in _ENV_NAMES.items() if name in source})
```

`src/dgi/fsutil.py`:
```python
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def atomic_replace(src: Path, dst: Path) -> None:
    """Rename src over dst (same filesystem), so readers see the old or the new file, never a mix."""
    os.replace(src, dst)


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
```

`src/dgi/report.py`:
```python
"""CLI output formatting and the error-to-exit-code guard. No business logic."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import typer

from dgi.errors import DgiError

T = TypeVar("T")


def guard(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except DgiError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
```

`src/dgi/cli.py` (commands are registered by the tasks that implement them):
```python
"""Command line entry point. Wiring only: no unit spec (docs/code-conventions.md)."""

from __future__ import annotations

import typer

app = typer.Typer(help="Dividend growth screener over the investment platform API.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Dividend growth screener over the investment platform API."""
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_settings.py tests/test_fsutil.py tests/test_report.py`
Expected: PASS (10 passed).

Run: `uv run dgi --help`
Expected: usage text, exit 0.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .python-version src tests
git commit -m "feat: package skeleton with settings, errors, fsutil and CLI guard"
```

---

### Task 3: Cache schema (one DDL for every layer)

`schema.py` is the single source of truth for table columns. Builders create tables from it and insert with `INSERT ... BY NAME`, so a column is added in one place. It sits at the package top level so `metrics`, `scoring` and `cache` can all import it without breaking the one-way rule.

**Files:**
- Create: `src/dgi/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces:
  - `COLUMN_DEFS: dict[str, list[tuple[str, str]]]` table name -> ordered `(column, duckdb type)`.
  - `PERSISTED_TABLES: tuple[str, ...]` all tables that live in the cache file (every key of `COLUMN_DEFS`).
  - `columns(name: str) -> list[str]`, `ddl(name: str) -> str`, `create_tables(con, names: Iterable[str] | None = None) -> None`, `recreate_tables(con, names: Iterable[str]) -> None` (drop if exists, then create), `table_columns(con, name: str) -> list[str]`, `arrow_schema(name: str) -> pa.Schema`, `insert_rows(con, name: str, records: Sequence[Mapping[str, Any]]) -> int`.
- Consumes: nothing.

- [ ] **Step 1: Write the failing tests**

`tests/test_schema.py` (UNIT: `schema`):
```python
import datetime as dt

import duckdb
import pytest

from dgi import schema


def test_every_table_has_columns_and_ddl():
    assert set(schema.PERSISTED_TABLES) == set(schema.COLUMN_DEFS)
    for name in schema.COLUMN_DEFS:
        assert schema.columns(name)
        assert schema.ddl(name).startswith(f"CREATE TABLE {name} (")


def test_create_tables_matches_declared_columns():
    con = duckdb.connect()
    schema.create_tables(con)
    for name in schema.COLUMN_DEFS:
        assert schema.table_columns(con, name) == schema.columns(name)


def test_recreate_tables_replaces_an_existing_table_and_creates_a_missing_one():
    con = duckdb.connect()
    schema.create_tables(con, ["flags"])
    con.execute("INSERT INTO flags VALUES ('KO', 'x', 'red', 'text')")
    schema.recreate_tables(con, ["flags", "scores"])
    assert con.execute("SELECT count(*) FROM flags").fetchone() == (0,)
    assert schema.table_columns(con, "scores") == schema.columns("scores")


def test_create_tables_can_create_a_subset():
    con = duckdb.connect()
    schema.create_tables(con, ["scores"])
    assert schema.table_columns(con, "scores")
    assert schema.table_columns(con, "flags") == []


def test_insert_rows_fills_named_columns_and_leaves_the_rest_null():
    con = duckdb.connect()
    schema.create_tables(con, ["dividend_annual"])
    n = schema.insert_rows(con, "dividend_annual", [{"ticker": "KO", "year": 2024, "dps": 1.94, "complete": True}])
    assert n == 1
    assert con.execute("SELECT ticker, year, dps, n_payments, complete FROM dividend_annual").fetchall() == [
        ("KO", 2024, 1.94, None, True)
    ]


def test_insert_rows_converts_dates_and_accepts_no_rows():
    con = duckdb.connect()
    schema.create_tables(con, ["dividend_payment"])
    schema.insert_rows(con, "dividend_payment", [{"ticker": "KO", "ex_date": dt.date(2024, 3, 14), "year": 2024, "amount_adj": 0.485, "is_special": False}])
    assert schema.insert_rows(con, "dividend_payment", []) == 0
    assert con.execute("SELECT count(*) FROM dividend_payment").fetchone() == (1,)


def test_insert_rows_rejects_an_unknown_table():
    with pytest.raises(KeyError):
        schema.insert_rows(duckdb.connect(), "nope", [])
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_schema.py`
Expected: FAIL with `ImportError: cannot import name 'schema'`.

- [ ] **Step 3: Write `src/dgi/schema.py`**

```python
"""Table definitions for the cache (dgi.duckdb): the single source of truth for column names and types."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import duckdb
import pyarrow as pa

_D, _V, _I, _B, _DATE = "DOUBLE", "VARCHAR", "INTEGER", "BOOLEAN", "DATE"

_FUNDAMENTALS = (
    "revenue net_income operating_income eps_diluted shares_diluted interest_expense cfo capex dividends_paid "
    "depreciation_amortization fcf equity current_assets current_liabilities cash short_term_investments debt "
    "net_debt ebitda op_margin payout_earnings payout_fcf fcf_per_share"
).split()

COLUMN_DEFS: dict[str, list[tuple[str, str]]] = {
    "company_dim": [("ticker", _V), ("cik", "BIGINT"), ("name", _V), ("sic", _V), ("sic_description", _V)],
    "dividend_payment": [("ticker", _V), ("ex_date", _DATE), ("year", _I), ("amount_adj", _D), ("is_special", _B)],
    "dividend_annual": [
        ("ticker", _V), ("year", _I), ("dps", _D), ("n_payments", _I), ("special_total", _D), ("complete", _B),
    ],
    "price_yearend": [("ticker", _V), ("year", _I), ("close_adj", _D)],
    "fundamentals_annual": [("ticker", _V), ("fiscal_year", _I), ("period_end", _DATE)] + [(c, _D) for c in _FUNDAMENTALS]
    + [("dividends_estimated", _B)],
    "metrics_current": [
        ("ticker", _V), ("price", _D), ("price_date", _DATE), ("market_cap", _D), ("dividend_ttm", _D),
        ("div_yield", _D), ("div_yield_avg_5y", _D), ("yield_vs_avg", _D), ("streak", _I), ("no_cut_streak", _I),
        ("dgr_1", _D), ("dgr_3", _D), ("dgr_5", _D), ("dgr_10", _D), ("years_history", _I), ("payment_frequency", _I),
        ("cut_years_5y", _I), ("irregular_payments", _B), ("special_count_5y", _I), ("suspect_dividend_count", _I),
        ("latest_fy", _I), ("fy_period_end", _DATE), ("basis", _V), ("eps_basis", _D), ("fcf_ps_basis", _D),
        ("payout_earnings", _D), ("payout_earnings_5y", _D), ("payout_fcf", _D), ("payout_fcf_5y", _D),
        ("interest_coverage", _D), ("net_debt_ebitda", _D), ("current_ratio", _D), ("positive_earnings_years", _I),
        ("rev_cagr_5", _D), ("eps_cagr_5", _D), ("fcf_ps_cagr_5", _D), ("roe", _D), ("op_margin_std", _D),
        ("share_trend_5", _D), ("pe", _D), ("p_fcf", _D), ("fcf_yield", _D), ("fcf_latest", _D), ("net_income_latest", _D),
    ],
    "scores": [
        ("ticker", _V), ("status", _V), ("reason", _V), ("sector_group", _V), ("dividend", _D), ("safety", _D),
        ("growth", _D), ("valuation", _D), ("coverage", _D), ("total", _D), ("fair_value_low", _D),
        ("fair_value_mid", _D), ("fair_value_high", _D), ("margin_of_safety", _D),
    ],
    "score_detail": [
        ("ticker", _V), ("metric", _V), ("pillar", _V), ("value", _D), ("band_score", _D), ("weight", _D),
        ("contribution", _D), ("percentile", _D),
    ],
    "flags": [("ticker", _V), ("code", _V), ("severity", _V), ("text", _V)],
    "meta": [
        ("upstream_key", _V), ("upstream_built_at", _V), ("contract_version", _V), ("metrics_version", _V),
        ("scoring_hash", _V), ("built_at", _V), ("contract_warnings", _V),
    ],
}

PERSISTED_TABLES: tuple[str, ...] = tuple(COLUMN_DEFS)

_ARROW_TYPES = {
    "VARCHAR": pa.string(), "DOUBLE": pa.float64(), "INTEGER": pa.int32(), "BIGINT": pa.int64(),
    "DATE": pa.date32(), "BOOLEAN": pa.bool_(),
}


def columns(name: str) -> list[str]:
    return [c for c, _ in COLUMN_DEFS[name]]


def ddl(name: str) -> str:
    body = ", ".join(f"{c} {t}" for c, t in COLUMN_DEFS[name])
    return f"CREATE TABLE {name} ({body})"


def create_tables(con: duckdb.DuckDBPyConnection, names: Iterable[str] | None = None) -> None:
    for name in names if names is not None else COLUMN_DEFS:
        con.execute(ddl(name))


def recreate_tables(con: duckdb.DuckDBPyConnection, names: Iterable[str]) -> None:
    for name in names:
        con.execute(f"DROP TABLE IF EXISTS {name}")
        con.execute(ddl(name))


def table_columns(con: duckdb.DuckDBPyConnection, name: str) -> list[str]:
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position", [name]
    ).fetchall()
    return [r[0] for r in rows]


def arrow_schema(name: str) -> pa.Schema:
    return pa.schema([(c, _ARROW_TYPES[t]) for c, t in COLUMN_DEFS[name]])


def insert_rows(con: duckdb.DuckDBPyConnection, name: str, records: Sequence[Mapping[str, Any]]) -> int:
    """Insert dict rows by column name; columns a record omits become NULL. Returns the row count."""
    table = pa.Table.from_pylist(list(records), schema=arrow_schema(name))
    con.register("_insert_rows", table)
    try:
        con.execute(f"INSERT INTO {name} BY NAME SELECT * FROM _insert_rows")
    finally:
        con.unregister("_insert_rows")
    return table.num_rows
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_schema.py`
Expected: PASS (7 passed). If `pa.Table.from_pylist` rejects a `date` for `date32` or a missing key, fix the conversion in `insert_rows` (the tests pin this behavior).

- [ ] **Step 5: Commit**

```bash
git add src/dgi/schema.py tests/test_schema.py
git commit -m "feat: cache schema as the single source of table definitions"
```
