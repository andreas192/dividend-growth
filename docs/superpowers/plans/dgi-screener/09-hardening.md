# Phase 9: v1 hardening (Tasks 27-31)

Part of `../2026-10-05-dgi-screener.md`. **Status: done** (branch `fix/dgi-hardening`, merged to `develop` as #2, commit `7c096ac`). Phases 1-8 are the original build; this phase records what changed afterwards, and **where a code block here and one in Tasks 2, 11, 14-18, 21 or 24 disagree, this phase wins.** Each affected task carries a note pointing here. The repo, not the older blocks, is the source of truth for the final code.

Spec change made with it: "Errors" in `../../specs/2026-10-05-dgi-screener-design.md` now says a scoring-only edit is rescored from the cache when the API is unreachable (Task 28). Origin of the items: `docs/next-steps.md`, section 3.

Tasks 27-31 are independent of each other; both 27 and 28 edit `pipeline.py` (different functions), so apply them in order.

---

### Task 27: Narrow DuckDB error handling (cache errors versus SQL bugs)

Before: `guard`, `read_status` and `run_check` called every `duckdb.Error` a cache problem, so a SQL bug in our own code showed up as "cache unreadable, run `dgi refresh --force`". Now only storage failures (I/O, serialization, memory, dead database, permission) are cache errors; programming, binder, catalog and data errors show a traceback.

**Files:**
- Modify: `src/dgi/errors.py` (adds `STORAGE_ERRORS`), `src/dgi/cache/handle.py` (adds `open_readonly`), `src/dgi/cache/status.py`, `src/dgi/pipeline.py` (`run_check`), `src/dgi/report.py` (`guard`)
- Test: `tests/test_cache_handle.py`, `tests/test_cache_status.py`, `tests/test_report.py`

**Interfaces:**
- Consumes: `dgi.cache.checks.check_schema(con)`, `dgi.errors.CacheMissing`.
- Produces:
  - `dgi.errors.STORAGE_ERRORS = (duckdb.OperationalError, duckdb.FatalException, duckdb.PermissionException)`.
  - `dgi.cache.handle.open_readonly(path: Path) -> Iterator[duckdb.DuckDBPyConnection]` (context manager): a missing file is `CacheMissing("no cache at ...; run `dgi refresh`")`; a file that cannot be opened is `CacheMissing("cannot open the cache ...; run `dgi refresh --force`")`; a storage error raised inside the block is `CacheMissing("the cache at ... is unreadable: ...; run `dgi refresh --force`")`; any other exception passes through unchanged; the connection is always closed.
  - `read_status(path)` raises `CacheMissing("... does not match this version (...); run `dgi refresh --force`")` when `check_schema` fails (a cache from an older schema), instead of failing later with a binder error.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_cache_handle.py` (imports: `pytest`, `from dgi.cache.handle import CacheHandle, open_readonly`, `from dgi.errors import CacheMissing`):
```python
def test_open_readonly_says_to_refresh_when_there_is_no_cache(tmp_path):
    with pytest.raises(CacheMissing, match="run `dgi refresh`"):
        with open_readonly(tmp_path / "dgi.duckdb"):
            pass


def test_open_readonly_turns_a_file_that_is_not_a_database_into_cache_missing(tmp_path):
    corrupt = tmp_path / "dgi.duckdb"
    corrupt.write_bytes(b"garbage" * 200)
    with pytest.raises(CacheMissing, match="cannot open.*refresh --force"):
        with open_readonly(corrupt):
            pass


def test_open_readonly_turns_a_storage_failure_while_reading_into_cache_missing(tmp_path):
    path = persist(cache_with({"AAA": ("2080", {})}), tmp_path / "dgi.duckdb")
    with pytest.raises(CacheMissing, match="unreadable.*refresh --force"):
        with open_readonly(path):
            raise duckdb.IOException("disk read failed")


def test_open_readonly_lets_a_sql_bug_through_as_itself(tmp_path):
    path = persist(cache_with({"AAA": ("2080", {})}), tmp_path / "dgi.duckdb")
    with pytest.raises(duckdb.CatalogException):
        with open_readonly(path) as con:
            con.execute("SELECT nope FROM no_such_table")
    with pytest.raises(duckdb.BinderException):
        with open_readonly(path) as con:
            con.execute("SELECT no_such_column FROM company_dim")


def test_open_readonly_closes_the_connection(tmp_path):
    path = persist(cache_with({"AAA": ("2080", {})}), tmp_path / "dgi.duckdb")
    with open_readonly(path) as con:
        pass
    with pytest.raises(duckdb.ConnectionException):
        con.execute("SELECT 1")
```

Add to `tests/test_cache_status.py`:
```python
def test_a_cache_with_the_wrong_columns_is_cache_missing_not_a_sql_error(tmp_path):
    from dgi import schema
    path = tmp_path / "old.duckdb"
    con = duckdb.connect(str(path))
    schema.create_tables(con)
    con.execute("ALTER TABLE scores DROP COLUMN status")
    con.close()
    with pytest.raises(CacheMissing, match="does not match.*refresh --force"):
        read_status(path)
```

Add to `tests/test_report.py`:
```python
def test_guard_lets_a_sql_bug_through_instead_of_calling_it_a_cache_error():
    import duckdb

    def bug():
        raise duckdb.BinderException("column nope not found")

    with pytest.raises(duckdb.BinderException):
        guard(bug)
```

- [x] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_cache_handle.py tests/test_cache_status.py tests/test_report.py`
Expected: FAIL (`cannot import name 'open_readonly'`; the guard test fails because `duckdb.Error` was caught).

- [x] **Step 3: Write the implementation**

`src/dgi/errors.py` (top of file):
```python
"""Typed errors. The CLI's guard turns any DgiError into `error: ...` on stderr and exit code 1."""

import duckdb

# DuckDB failures that come from the file or the machine (I/O, serialization, memory, a dead database), not from our SQL.
# Programming, data and internal errors are bugs: they are not caught, so they show a traceback.
STORAGE_ERRORS = (duckdb.OperationalError, duckdb.FatalException, duckdb.PermissionException)
```

`src/dgi/cache/handle.py` (new function above `CacheHandle`; imports `from dgi.errors import STORAGE_ERRORS, CacheMissing`):
```python
@contextmanager
def open_readonly(path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """A read-only connection for the CLI. A missing, unopenable or unreadable file is `CacheMissing` with a way out; a SQL bug is not caught."""
    if not path.exists():
        raise CacheMissing(f"no cache at {path}; run `dgi refresh`")
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:  # opening runs no SQL of ours: any failure is about the file
        raise CacheMissing(f"cannot open the cache at {path}: {exc}; run `dgi refresh --force`") from exc
    try:
        yield con
    except STORAGE_ERRORS as exc:
        raise CacheMissing(f"the cache at {path} is unreadable: {exc}; run `dgi refresh --force`") from exc
    finally:
        con.close()
```

`src/dgi/cache/status.py` (`read_status` becomes):
```python
def read_status(path: Path) -> CacheStatus:
    with open_readonly(path) as con:
        shape = check_schema(con)
        if not shape.passed:
            raise CacheMissing(f"the cache at {path} does not match this version ({shape.detail}); run `dgi refresh --force`")
        return read_status_from(con)
```
(imports: `from dgi.cache.checks import check_schema, row_counts`, `from dgi.cache.handle import open_readonly`.)

`src/dgi/pipeline.py`:
```python
def run_check(settings: Settings) -> list[CheckResult]:
    with open_readonly(settings.cache_path) as con:
        return run_cache_checks(con, None)
```

`src/dgi/report.py` (`guard`: `except duckdb.Error` becomes `except STORAGE_ERRORS`; drop `import duckdb`; import `STORAGE_ERRORS` from `dgi.errors`):
```python
    except STORAGE_ERRORS as exc:
        _fail(f"cache or database error: {str(exc).splitlines()[0]}", exc)
```

- [x] **Step 4: Run to verify they pass, then the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [x] **Step 5: Commit** (part of `7c096ac`)

---

### Task 28: Rescore from the cache when the API is unreachable

Before: every `dgi refresh` asked `/health` first, so a scoring-only edit failed when the API was down although it needs no upstream data. Now that one case is served from the cache and says so. `--force`, a metrics change, a missing cache and an unchanged config still fail or stay "up to date" as before.

**Files:**
- Modify: `src/dgi/cache/meta.py` (adds `Upstream`, `plan_offline`), `src/dgi/pipeline.py` (`choose_plan`, `build_candidate`, `run_refresh`)
- Test: `tests/test_cache_meta.py`, `tests/test_cli_pipeline.py` (PIPELINE layer)

**Interfaces:**
- Consumes: `fetch_contract`, `ApiClient.health()`, `plan_refresh` (Task 15), `ApiUnavailable`.
- Produces:
  - `Upstream(key, built_at: str | None, contract_version, contract_warnings: str)` (frozen dataclass) with `Upstream.of_cache(meta)`: what the source says about itself, taken from the cache when the API cannot be asked.
  - `plan_offline(meta | None, metrics_version, scoring_hash) -> RefreshPlan | None`: `RefreshPlan(False, True, "scoring config changed")` only when a cache exists, its `metrics_version` matches and its `scoring_hash` differs; otherwise `None`.
  - `pipeline.choose_plan(client, previous, version, scoring_hash, force) -> tuple[Upstream, RefreshPlan, list[str]]` (the list is the warnings to print). `build_candidate` takes `upstream: Upstream` in place of `health` and `contract`.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_cache_meta.py` (import `Upstream, plan_offline`):
```python
@pytest.mark.parametrize(
    "meta, metrics_version, scoring_hash, expected",
    [
        (make_meta(), M, "s2", RefreshPlan(False, True, "scoring config changed")),
        (make_meta(), M, S, None),             # nothing to do: freshness cannot be confirmed without the API
        (make_meta(), "2:abc", "s2", None),    # a metrics change needs a pull
        (None, M, "s2", None),                 # no cache to rescore
    ],
)
def test_plan_offline_only_rescores_a_cache_whose_scoring_config_changed(meta, metrics_version, scoring_hash, expected):
    assert plan_offline(meta, metrics_version, scoring_hash) == expected


def test_upstream_of_a_cache_is_what_it_was_built_from():
    meta = make_meta(contract_warnings="contract v1 is deprecated")
    assert Upstream.of_cache(meta) == Upstream(meta.upstream_key, meta.upstream_built_at, "v1", "contract v1 is deprecated")
```

Add to `tests/test_cli_pipeline.py` (PIPELINE; `gold.down = True` makes the fake API unreachable):
```python
def test_a_scoring_edit_rescores_from_the_cache_when_the_api_is_down(env, gold):
    runner.invoke(cli.app, ["refresh"], env=env)
    scoring = Path(env["DGI_SCORING_CONFIG"])
    scoring.write_text(scoring.read_text().replace("min_streak: 5", "min_streak: 7"))
    gold.down = True
    result = runner.invoke(cli.app, ["refresh"], env=env)
    assert result.exit_code == 0, result.output
    assert "rescored: scoring config changed" in result.output and "warning: the API is unreachable" in result.output
    assert read_meta(Path(env["DGI_DATA_DIR"]) / "dgi.duckdb").upstream_key == "hash-1|2026-10-04T06:00:00+00:00"
```

- [x] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_cache_meta.py tests/test_cli_pipeline.py`
Expected: FAIL (`cannot import name 'plan_offline'`; the pipeline test exits 1 with `error: cannot reach the API`).

- [x] **Step 3: Write the implementation**

`src/dgi/cache/meta.py` (below `RefreshPlan`, and `plan_offline` after `plan_refresh`):
```python
@dataclass(frozen=True)
class Upstream:
    """What the data source says about itself: asked of the API, or taken from the cache when the API cannot be asked."""
    key: str
    built_at: str | None
    contract_version: str
    contract_warnings: str

    @classmethod
    def of_cache(cls, meta: CacheMeta) -> Upstream:
        return cls(meta.upstream_key, meta.upstream_built_at, meta.contract_version, meta.contract_warnings)


def plan_offline(meta: CacheMeta | None, metrics_version: str, scoring_hash: str) -> RefreshPlan | None:
    """The plan when the API cannot be asked: only a scoring-config change can be served from the cache alone, else None."""
    if meta is None or meta.metrics_version != metrics_version or meta.scoring_hash == scoring_hash:
        return None
    return RefreshPlan(False, True, "scoring config changed")
```

`src/dgi/pipeline.py`:
```python
def choose_plan(
    client: ApiClient, previous: CacheMeta | None, version: str, scoring_hash: str, force: bool
) -> tuple[Upstream, RefreshPlan, list[str]]:
    """Ask the API what changed. If it cannot be asked, a scoring-only edit is still rescored from the cache; anything else fails."""
    try:
        health = client.health()
        contract = fetch_contract(client)
    except ApiUnavailable as exc:
        plan = None if force else plan_offline(previous, version, scoring_hash)
        if plan is None or previous is None:
            raise
        notice = f"the API is unreachable, so upstream was not checked; rescored the cached data ({str(exc).splitlines()[0]})"
        return Upstream.of_cache(previous), plan, [notice]
    upstream = Upstream(health.upstream_key, health.gold_built_at, contract.version, "; ".join(contract.warnings))
    return upstream, plan_refresh(previous, upstream.key, upstream.contract_version, version, scoring_hash, force), contract.warnings
```
`run_refresh` now begins `cfg = load_scoring_config(...)`, `live = ...`, `previous = read_meta(live)`, `version = ...`, `scoring_hash = ...`, then `upstream, plan, warnings = choose_plan(client, previous, version, scoring_hash, force)`; it returns `warnings` in the `RefreshResult` and passes `upstream` to `build_candidate`, which writes `next_meta(plan, previous, upstream.key, upstream.built_at, upstream.contract_version, upstream.contract_warnings, version, scoring_hash, now)`. Imports in `pipeline.py` drop `Health`, `ContractStatus`, `CacheMissing` and gain `Upstream`, `plan_offline`, `ApiUnavailable`.

- [x] **Step 4: Run to verify they pass, then the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [x] **Step 5: Commit** (part of `7c096ac`)

---

### Task 29: Web: error pages carry the footer, the filter form keeps `size=`

**Files:**
- Modify: `src/dgi/web/app.py` (`page_footer`, `not_found`, `bad_query`), `src/dgi/web/templates/screener.html`
- Test: `tests/test_web_app.py`, `tests/test_web_screener.py`

**Interfaces:**
- Consumes: `footer(meta, scoring_hash)` and `read_meta_from` (Task 21), `request.app.state.handle.connection()` (Task 18).
- Produces: `page_footer(request) -> dict | None` (None when there is no cache); the 404 and 422 pages render the same footer and scoring-drift notice as the other pages. The `no_cache` page still has no footer by design: with no cache there is nothing to compare the config with.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_web_app.py`:
```python
@pytest.mark.parametrize("page, status", [("/nope", 404), ("/company/ZZZ", 404), ("/?min_streak=abc", 422)])
def test_error_pages_carry_the_footer_with_the_drift_notice(tmp_path, page, status):
    persist(scored_cache({"AAA": ("2080", {})}), tmp_path / "dgi.duckdb", make_meta(scoring_hash="old"))
    with client_for(tmp_path, scoring_hash="new") as client:
        response = client.get(page)
    assert response.status_code == status
    assert "Cache built 2026-10-05 07:00 UTC" in response.text and "different scoring config" in response.text
```

Add to `tests/test_web_screener.py`:
```python
def test_the_filter_form_carries_a_non_default_page_size(web):
    assert '<input type="hidden" name="size" value="20">' in web.get("/?size=20").text
    assert 'name="size"' not in web.get("/").text   # the default page size is not repeated in the URL
```

- [x] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_web_app.py tests/test_web_screener.py`
Expected: FAIL (no footer on the error pages; no hidden `size` input).

- [x] **Step 3: Write the implementation**

`src/dgi/web/app.py`:
```python
def page_footer(request: Request) -> dict[str, Any] | None:
    """The footer for pages that did not read the cache themselves (error pages); None when there is no cache."""
    state = request.app.state
    with state.handle.connection() as con:
        return None if con is None else footer(read_meta_from(con), state.scoring_hash)


def not_found(request: Request, exc: Exception) -> Response:
    return render(request, "error.html", {"footer": page_footer(request), "title": "Not found", "message": "There is nothing at this address."}, status=404)


def bad_query(request: Request, exc: BadQuery) -> Response:
    context = {"footer": page_footer(request), "title": "Check the filters", "message": f"{exc.param}: {exc.reason}"}
    return render(request, "error.html", context, status=422)
```

`src/dgi/web/templates/screener.html`, right after the hidden `sort` and `dir` inputs in the filter form:
```html
  {% if q.params.size %}<input type="hidden" name="size" value="{{ q.params.size }}">{% endif %}
```

- [x] **Step 4: Run to verify they pass, then the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [x] **Step 5: Commit** (part of `7c096ac`)

---

### Task 30: Freeze the scoring config the tests use

Before: tests read `config/scoring.yaml`, the owner-edited file, so changing a band or a weight could fail unrelated tests. Now they read `tests/frozen/scoring.yaml`, an identical copy at the time of the change; only the shape of the owner's file is still tested.

**Files:**
- Create: `tests/frozen/scoring.yaml` (a copy of `config/scoring.yaml`)
- Modify: `tests/cache_fixtures.py`, `tests/test_scoring_config.py`, `tests/test_cli_pipeline.py`
- No new production code.

**Interfaces:**
- Produces: `tests.cache_fixtures.FROZEN_SCORING: Path` (the copy); `CFG` is loaded from it. The pipeline tests copy it to the temp scoring path instead of the repo file.

- [x] **Step 1: Create the frozen copy**

```bash
mkdir -p tests/frozen && cp config/scoring.yaml tests/frozen/scoring.yaml
```

- [x] **Step 2: Point the tests at it**

`tests/cache_fixtures.py`:
```python
FROZEN_SCORING = Path(__file__).resolve().parent / "frozen" / "scoring.yaml"  # a copy of config/scoring.yaml: editing the owner's file must not move the tests
CFG: ScoringConfig = load_scoring_config(FROZEN_SCORING)
```

`tests/test_cli_pipeline.py`: drop `REPO`; `from tests.cache_fixtures import FROZEN_SCORING`; the `env` fixture does `shutil.copy(FROZEN_SCORING, scoring)`.

`tests/test_scoring_config.py` (`from tests.cache_fixtures import FROZEN_SCORING`; the old test splits in two):
```python
REPO_CONFIG = Path(__file__).resolve().parent.parent / "config" / "scoring.yaml"  # the owner's file: only its shape is tested, never its values


def test_the_owners_config_parses_and_scores_every_known_metric():
    cfg = load_scoring_config(REPO_CONFIG)
    assert set(cfg.bands) == KNOWN_METRICS
    assert sum(cfg.pillars.values()) == pytest.approx(1.0)
    assert {b.pillar for b in cfg.bands.values()} == set(PILLARS)


def test_the_frozen_config_has_the_values_the_tests_rely_on():
    cfg = load_scoring_config(FROZEN_SCORING)
    assert cfg.metrics == MetricParams()
    assert cfg.universe.min_coverage == 0.6 and cfg.hard_filters.min_streak == 5
    assert (6798, 6798) in cfg.universe.excluded_sic_ranges
```

- [x] **Step 3: Run the whole suite**

Run: `uv run pytest`
Expected: PASS; editing a band in `config/scoring.yaml` no longer changes any test except `test_the_owners_config_parses_and_scores_every_known_metric`.

- [x] **Step 4: Commit** (part of `7c096ac`)

> **Building from scratch in order:** `tests/cache_fixtures.py` first appears in Task 14 and `test_scoring_config.py` in Task 11, so a fresh build creates `tests/frozen/scoring.yaml` in Task 11 and defines `FROZEN_SCORING` in `test_scoring_config.py` locally until Task 14 moves it to `cache_fixtures.py`. The end state is the one above.

---

### Task 31: `install_tools.sh` re-hashes the installed binaries

Before: "already installed" was decided from a `PINS` marker file, so a tampered or replaced `.tools/kubectl` passed. Now every run re-hashes both binaries against the pinned checksums.

**Files:**
- Modify: `scripts/install_tools.sh`
- Test: manual (script; the checksums are the pinned constants already in the file)

- [x] **Step 1: Replace the marker check with a hash check**

```sh
installed_ok() {  # installed_ok FILE EXPECTED_SHA256: present, executable, and still the bytes that were verified
  [ -x "$1" ] && [ "$(shasum -a 256 "$1" | awk '{print $1}')" = "$2" ]
}

mkdir -p "$TOOLS"
PINS="kubectl=$KUBECTL_VERSION kind=$KIND_VERSION arch=$ARCH"
if installed_ok "$TOOLS/kubectl" "$KUBECTL_SHA" && installed_ok "$TOOLS/kind" "$KIND_SHA"; then
  echo "tools already installed and checksums verified: $PINS"
  exit 0
fi
```
The `PINS` file is still written after a fresh install, but nothing reads it any more.

- [x] **Step 2: Check**

Run: `scripts/install_tools.sh`
Expected: `tools already installed and checksums verified: ...`. Then `echo x >> .tools/kind; scripts/install_tools.sh` re-downloads and verifies; the script never accepts a file whose hash differs from the pin.

- [x] **Step 3: Docs**

`docs/command.md`: the refresh paragraph says a scoring-only edit is rescored from the cache with a warning when the API is unreachable (Task 28); the tools line says installed files are re-hashed on every run.

- [x] **Step 4: Commit** (part of `7c096ac`)
