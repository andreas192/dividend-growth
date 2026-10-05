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


def test_describe_refresh_summarizes_a_rebuild_and_a_no_op():
    from dgi.cache.checks import CheckResult
    from dgi.metrics import MetricsResult
    from dgi.report import describe_refresh
    from dgi.results import RefreshResult
    from dgi.scoring.stage import ScoreResult

    built = RefreshResult(
        "rebuilt", "upstream data changed", "h|t", {"stg_company": 5}, MetricsResult(5, 100, 3), ScoreResult(5, 2, {"no_price": 1, "financial_or_reit": 2}),
        [CheckResult("a", True, "x"), CheckResult("b", True, "y")], ["contract v1 is deprecated, sunset 2027-01-01"],
    )
    text = describe_refresh(built)
    assert "rebuilt: upstream data changed" in text and "pulled: stg_company=5" in text
    assert "metrics: 5 companies, 100 dividend payments, 3 with a streak" in text
    assert "scores: 2 scored of 5; not scored: financial_or_reit=2, no_price=1" in text
    assert "checks: 2 of 2 passed" in text and "warning: contract v1 is deprecated" in text
    noop = RefreshResult("up to date", "up to date", "h|t", {}, None, None, [], [])
    assert describe_refresh(noop) == "up to date: up to date\nupstream: h|t"


def test_describe_status_and_print_checks(capsys, tmp_path):
    from dgi.cache.checks import CheckResult
    from dgi.cache.status import CacheStatus
    from dgi.report import describe_status, print_checks
    from tests.cache_fixtures import make_meta

    status = CacheStatus(make_meta(), {"company_dim": 4}, 1, {"no_price": 3})
    text = describe_status(status, tmp_path / "dgi.duckdb")
    assert "companies: 4, scored: 1" in text and "not scored: no_price=3" in text and "contract: v1, metrics 1:abc, scoring s1" in text
    print_checks([CheckResult("schema", True, "ok"), CheckResult("row_counts", False, "collapsed")])
    out = capsys.readouterr().out
    assert "PASS schema: ok" in out and "FAIL row_counts: collapsed" in out


@pytest.mark.parametrize("host, warned", [("127.0.0.1", False), ("localhost", False), ("::1", False), ("0.0.0.0", True), ("192.168.1.5", True)])
def test_exposure_warning_only_for_addresses_beyond_this_machine(host, warned):
    from dgi.report import exposure_warning

    message = exposure_warning(host)
    assert (message is not None) == warned
    assert message is None or "no authentication" in message
