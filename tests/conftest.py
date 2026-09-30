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


# Upstream tests that need POSIX: os.killpg, owner-only file modes (NTFS reports 0o666)
# or a command line longer than Windows allows. They pass on Linux, where the Docker
# image runs.
_POSIX_ONLY = (
    "tests/test_imported_session.py::test_import_validates_before_private_save_and_preserves_android",
    "tests/test_server_renewal.py::test_owned_browser_always_stops_process_and_removes_profile",
    "tests/test_server_renewal.py::test_source_restarts_from_server_replacement_and_validates_before_save",
    "tests/test_server_seed.py::test_seed_preserves_expired_integrity_context_for_new_issuance",
)


def pytest_collection_modifyitems(config, items):
    if sys.platform != "win32":
        return
    skip = pytest.mark.skip(reason="needs POSIX (os.killpg, file modes or long command lines)")
    for item in items:
        if item.nodeid.startswith(_POSIX_ONLY):
            item.add_marker(skip)
