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


def test_a_malformed_environment_value_is_a_config_error_naming_the_variable():
    import pytest

    from dgi.errors import ConfigError

    with pytest.raises(ConfigError, match="DGI_PORT"):
        Settings.from_env({"DGI_PORT": "abc"})
