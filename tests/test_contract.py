"""The repo's promises about itself: 20 standalone scripts, honest docs, declared deps."""

from __future__ import annotations

import ast
import re
import subprocess
import sys

import pytest

from conftest import REPO, SCRIPTS_DIR, offline_env

SCRIPTS = sorted(SCRIPTS_DIR.glob("[0-9][0-9]_*.py"))
IDS = [p.stem for p in SCRIPTS]

# import name -> distribution name as written in requirements.txt
DISTRIBUTIONS = {
    "PIL": "pillow", "pypdf": "pypdf", "openpyxl": "openpyxl", "qrcode": "qrcode",
    "httpx": "httpx", "yaml": "pyyaml", "edge_tts": "edge-tts", "pyperclip": "pyperclip",
    "mss": "mss",
}


def docstring(path):
    return ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""


def declared_deps(path) -> set[str]:
    line = next(l for l in docstring(path).splitlines() if l.startswith("Dependencies:"))
    value = line.split(":", 1)[1].strip()
    if value.startswith("stdlib only"):
        return set()
    return {re.sub(r"\[.*?\]", "", d).strip().lower() for d in value.split(",")}


def requirement_names() -> set[str]:
    names = set()
    for line in (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(re.split(r"[\[<>=!~ ]", line, maxsplit=1)[0].lower())
    return names


def top_level_imports(path) -> list[tuple[str, int]]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found += [(alias.name.split(".")[0], 0) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(((node.module or "").split(".")[0], node.level))
    return found


def test_there_are_twenty_numbered_scripts():
    assert [p.name[:2] for p in SCRIPTS] == [f"{i:02d}" for i in range(1, 21)]


@pytest.mark.parametrize("path", SCRIPTS, ids=IDS)
def test_help_runs(path):
    result = subprocess.run([sys.executable, str(path), "-h"], capture_output=True, text=True,
                            encoding="utf-8", env=offline_env(), timeout=60)
    if path.stem.startswith("20_") and sys.platform != "win32":
        assert result.returncode == 1 and "only runs on Windows" in result.stderr
        return
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage:")


@pytest.mark.parametrize("path", SCRIPTS, ids=IDS)
def test_docstring_has_usage_and_dependencies(path):
    doc = docstring(path)
    assert "Usage:" in doc
    examples = [l for l in doc.splitlines() if l.strip().startswith(f"python {path.name}")]
    assert len(examples) >= 3, "every script documents three usage examples"
    assert any(l.startswith("Dependencies:") for l in doc.splitlines())


@pytest.mark.parametrize("path", SCRIPTS, ids=IDS)
def test_standalone_no_repo_imports(path):
    siblings = {p.stem for p in SCRIPTS} | {"scripts", "utils", "common", "helpers", "conftest"}
    for name, level in top_level_imports(path):
        assert level == 0, f"relative import in {path.name}"
        assert name not in siblings, f"{path.name} imports {name} from the repo"


@pytest.mark.parametrize("path", SCRIPTS, ids=IDS)
def test_third_party_imports_are_declared_and_required(path):
    declared = declared_deps(path)
    third_party = {name for name, _ in top_level_imports(path)
                   if name and name not in sys.stdlib_module_names and name != "__future__"}
    for name in third_party:
        assert name in DISTRIBUTIONS, f"unknown third-party import {name} in {path.name}"
        assert DISTRIBUTIONS[name] in declared, f"{path.name} imports {name} but does not declare it"
    assert declared <= requirement_names(), f"{path.name} declares deps missing from requirements.txt"


def test_readme_counts_match_reality():
    stdlib_only = [p for p in SCRIPTS if not declared_deps(p)]
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    badge = re.search(r"stdlib--only%20scripts-(\d+)%20of%20(\d+)", readme)
    assert badge and int(badge.group(1)) == len(stdlib_only) and int(badge.group(2)) == len(SCRIPTS)
    for path in SCRIPTS:
        assert f"`{path.name}`" in readme, f"{path.name} missing from the README table"
    words = {10: "ten", 11: "eleven", 12: "twelve"}
    requirements = (REPO / "requirements.txt").read_text(encoding="utf-8")
    assert f"{words[len(stdlib_only)]} of the scripts are stdlib-only" in requirements
