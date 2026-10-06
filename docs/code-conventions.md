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
