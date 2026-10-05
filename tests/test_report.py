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
