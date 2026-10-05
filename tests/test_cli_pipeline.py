"""PIPELINE layer: the commands, the stages and the cache wired together over a fake investment API."""

import datetime as dt
import hashlib
import shutil
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from dgi import cli
from dgi.cache.meta import read_meta
from tests.fake_api import FakeGold
from tests.sample_gold import build_sample_gold

runner = CliRunner()
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def env(tmp_path, monkeypatch):
    scoring = tmp_path / "scoring.yaml"
    shutil.copy(REPO / "config" / "scoring.yaml", scoring)
    monkeypatch.setattr(cli, "current_date", lambda: dt.date(2026, 10, 5))
    monkeypatch.setattr(cli, "current_time", lambda: dt.datetime(2026, 10, 5, 7, 0, tzinfo=dt.timezone.utc))
    return {"DGI_DATA_DIR": str(tmp_path / "data"), "DGI_SCORING_CONFIG": str(scoring), "DGI_INVEST_API_URL": "http://api.test"}


@pytest.fixture
def gold(monkeypatch):
    fake = FakeGold(build_sample_gold())
    monkeypatch.setattr(cli, "make_client", lambda settings: fake.client(page_limit=500))
    return fake


def cache(env):
    return duckdb.connect(str(Path(env["DGI_DATA_DIR"]) / "dgi.duckdb"), read_only=True)


def sha(env) -> str:
    return hashlib.sha256((Path(env["DGI_DATA_DIR"]) / "dgi.duckdb").read_bytes()).hexdigest()


def test_the_commands_are_registered():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in ("refresh", "status", "check"):
        assert command in result.output


def test_refresh_builds_a_cache_and_one_value_traces_from_the_api_to_the_score(env, gold):
    result = runner.invoke(cli.app, ["refresh"], env=env)
    assert result.exit_code == 0, result.output
    assert "rebuilt: no usable cache" in result.output and "checks: 6 of 6 passed" in result.output
    con = cache(env)
    streak, status, price, basis = con.execute(
        "SELECT m.streak, s.status, m.price, m.basis FROM metrics_current m JOIN scores s USING (ticker) WHERE ticker = 'ACME'"
    ).fetchone()
    assert (streak, status, price, basis) == (11, "scored", 60.0, "ttm")
    assert con.execute("SELECT ticker, status, reason FROM scores ORDER BY ticker").fetchall() == [
        ("ACME", "scored", None),
        ("BANKY", "not_scored", "financial_or_reit"),
        ("NEWCO", "not_scored", "short_dividend_history"),
        ("NOPAY", "not_scored", "short_dividend_history"),
    ]
    assert con.execute("SELECT upstream_key FROM meta").fetchone() == ("hash-1|2026-10-04T06:00:00+00:00",)
    assert not list(Path(env["DGI_DATA_DIR"]).glob("dgi.duckdb.*"))


def test_a_second_refresh_on_unchanged_upstream_does_nothing(env, gold):
    runner.invoke(cli.app, ["refresh"], env=env)
    before, requests = sha(env), len(gold.requests)
    result = runner.invoke(cli.app, ["refresh"], env=env)
    assert result.exit_code == 0 and "up to date" in result.output
    assert sha(env) == before
    assert all("/v1/" not in r for r in gold.requests[requests:])  # only /health and /contracts were asked


def test_new_upstream_data_rebuilds_and_a_scoring_edit_rescores_without_the_api(env, gold):
    runner.invoke(cli.app, ["refresh"], env=env)
    gold.built_at = "2026-10-05T06:00:00+00:00"
    assert "rebuilt: upstream data changed" in runner.invoke(cli.app, ["refresh"], env=env).output
    scoring = Path(env["DGI_SCORING_CONFIG"])
    scoring.write_text(scoring.read_text().replace("min_streak: 5", "min_streak: 7"))
    requests = len(gold.requests)
    result = runner.invoke(cli.app, ["refresh"], env=env)
    assert "rescored: scoring config changed" in result.output
    assert all("/v1/" not in r for r in gold.requests[requests:])
    assert read_meta(Path(env["DGI_DATA_DIR"]) / "dgi.duckdb").upstream_key == "hash-1|2026-10-05T06:00:00+00:00"


def test_refresh_force_rebuilds_even_when_nothing_changed(env, gold):
    runner.invoke(cli.app, ["refresh"], env=env)
    assert "rebuilt: forced" in runner.invoke(cli.app, ["refresh", "--force"], env=env).output


def test_refresh_with_the_api_down_exits_1_and_keeps_the_old_cache(env, gold):
    runner.invoke(cli.app, ["refresh"], env=env)
    before = sha(env)
    gold.down = True
    result = runner.invoke(cli.app, ["refresh", "--force"], env=env)
    assert result.exit_code == 1 and "error:" in result.output
    assert sha(env) == before


def test_status_and_check_read_the_live_cache(env, gold):
    runner.invoke(cli.app, ["refresh"], env=env)
    status = runner.invoke(cli.app, ["status"], env=env)
    assert status.exit_code == 0 and "companies: 4, scored: 1" in status.output and "financial_or_reit=1" in status.output
    check = runner.invoke(cli.app, ["check"], env=env)
    assert check.exit_code == 0 and "FAIL" not in check.output
    # a 4-company sample scores 25%: inside the plausible range of the scored-share check


def test_check_without_a_cache_exits_1_with_a_way_out(env):
    result = runner.invoke(cli.app, ["check"], env=env)
    assert result.exit_code == 1 and "run `dgi refresh`" in result.output
