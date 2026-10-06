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
# Top-level modules other than the wiring (cli.py, pipeline.py): what each may import from dgi, taken from the real imports.
MODULE_ALLOWED = {
    "errors": set(),
    "fsutil": set(),
    "schema": set(),
    "settings": {"errors"},
    "results": {"cache", "metrics", "scoring"},
    "report": {"cache", "errors", "results"},
}


RELATIVE = "<relative import>"  # in no ALLOWED set: a relative import fails the layering test


def dgi_imports(path: Path) -> set[str]:
    """The first component after `dgi` of every absolute import in the file; a relative import shows up as RELATIVE."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[1] for a in node.names if a.name.startswith("dgi.")}
        elif isinstance(node, ast.ImportFrom) and node.level > 0:
            found.add(RELATIVE)
        elif isinstance(node, ast.ImportFrom) and node.module:
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


def test_relative_imports_are_reported_so_they_cannot_dodge_the_layering(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("from ..cache import x\nfrom . import web\nfrom .meta import y\n")
    assert dgi_imports(f) == {RELATIVE}


@pytest.mark.parametrize("package", sorted(ALLOWED))
def test_each_layer_imports_only_what_it_may(package):
    folder = SRC / package
    assert folder.is_dir(), f"{package} is enforced here but its folder is gone: renamed or moved?"
    offenders = {
        str(path.relative_to(SRC)): sorted(dgi_imports(path) - ALLOWED[package])
        for path in folder.rglob("*.py")
        if dgi_imports(path) - ALLOWED[package]
    }
    assert offenders == {}


@pytest.mark.parametrize("module", sorted(MODULE_ALLOWED))
def test_each_top_level_module_imports_only_what_it_may(module):
    path = SRC / f"{module}.py"
    assert path.is_file(), f"{module} is enforced here but its file is gone: renamed or moved?"
    assert dgi_imports(path) - MODULE_ALLOWED[module] == set()


def test_the_layering_test_fails_on_a_relative_import(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("from ..cache import x\n")
    assert dgi_imports(f) - ALLOWED["web"] == {RELATIVE}


def test_only_the_pipeline_and_the_cli_import_the_client():
    users = {p.name for p in SRC.glob("*.py") if "client" in dgi_imports(p)}
    assert users <= {"pipeline.py", "cli.py"}
