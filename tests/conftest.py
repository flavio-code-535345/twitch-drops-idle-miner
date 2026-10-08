import subprocess
import sys
from pathlib import Path

import pytest


if sys.platform == "win32":
    # The frontend tests pipe JS containing non-ASCII text through `subprocess.run(text=True)`,
    # which uses the ANSI codepage (cp1252) on Windows and raises UnicodeEncodeError. Node
    # always speaks UTF-8, so default text-mode pipes to UTF-8 here instead of editing every
    # upstream test file.
    _original_run = subprocess.run

    def _utf8_run(*args, **kwargs):
        if kwargs.get("text") and "encoding" not in kwargs:
            kwargs["encoding"] = "utf-8"
        return _original_run(*args, **kwargs)

    subprocess.run = _utf8_run

    # Upstream tests read the project's UTF-8 files with Path.read_text() and no encoding,
    # which likewise decodes with cp1252 on Windows.
    _original_read_text = Path.read_text

    def _utf8_read_text(self, encoding=None, *args, **kwargs):
        return _original_read_text(self, encoding or "utf-8", *args, **kwargs)

    Path.read_text = _utf8_read_text


# Upstream tests that create symlinks, which Windows only allows with admin rights or
# Developer Mode. They pass on Linux, where the Docker image runs.
_POSIX_ONLY = (
    "tests/test_login_helper.py::test_cleanup_does_not_follow_a_link_to_an_unrelated_readonly_file",
)


def pytest_collection_modifyitems(config, items):
    if sys.platform != "win32":
        return
    skip = pytest.mark.skip(reason="needs symlinks (admin rights or Developer Mode on Windows)")
    for item in items:
        if item.nodeid.startswith(_POSIX_ONLY):
            item.add_marker(skip)
