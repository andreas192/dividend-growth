"""The one-way dependency rule from CLAUDE.md, enforced: client -> metrics -> scoring -> cache -> web."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "dgi"
LEAVES = {"errors", "settings", "fsutil", "schema"}
ALLOWED = {
    "client": LEAVES | {"client"},
    "metrics": LEAVES | {"metrics"},
    "scoring": LEAVES | {"metrics", "scoring"},
    "cache": LEAVES | {"scoring", "cache"},
    "web": {"errors", "settings", "cache", "web"},
}


def dgi_imports(path: Path) -> set[str]:
    """The first component after `dgi` of every absolute import in the file."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[1] for a in node.names if a.name.startswith("dgi.")}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            parts = node.module.split(".")
            if parts[0] == "dgi" and len(parts) > 1:
                found.add(parts[1])
            elif parts[0] == "dgi":
                found |= {a.name for a in node.names}
    return found


def test_the_import_scanner_reads_each_import_form(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("import dgi.client.http\nfrom dgi.cache.meta import x\nfrom dgi import schema, fsutil\nimport os\nfrom os import path\n")
    assert dgi_imports(f) == {"client", "cache", "schema", "fsutil"}


@pytest.mark.parametrize("package", sorted(ALLOWED))
def test_each_layer_imports_only_what_it_may(package):
    folder = SRC / package
    if not folder.exists():
        pytest.skip(f"{package} is added by a later task")
    offenders = {
        str(path.relative_to(SRC)): sorted(dgi_imports(path) - ALLOWED[package])
        for path in folder.rglob("*.py")
        if dgi_imports(path) - ALLOWED[package]
    }
    assert offenders == {}


def test_only_the_pipeline_and_the_cli_import_the_client():
    users = {p.name for p in SRC.glob("*.py") if "client" in dgi_imports(p)}
    assert users <= {"pipeline.py", "cli.py"}
