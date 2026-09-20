from pathlib import Path

import tomllib

import src
from src.version import __version__
from src.web.app import app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_all_project_version_sources_match():
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lockfile = tomllib.loads((PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked_projects = [
        package
        for package in lockfile["package"]
        if package["name"] == pyproject["project"]["name"]
    ]

    assert len(locked_projects) == 1
    assert pyproject["project"]["version"] == __version__
    assert locked_projects[0]["version"] == __version__
    assert src.__version__ == __version__
    assert app.version == __version__
