"""Fork: helper downloads follow UPSTREAM_VERSION even when MY_VERSION runs ahead."""

import asyncio

import pytest

from src.web import app as web_app


PLATFORMS = ("windows-x64", "linux-x64", "macos-arm64", "macos-x64")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_helper_links_use_upstream_release_when_fork_version_differs(monkeypatch, platform):
    monkeypatch.setattr(web_app, "__version__", "9.9.9")
    monkeypatch.setattr(web_app, "UPSTREAM_VERSION", "2.1.1")

    body = asyncio.run(web_app.serve_index()).body.decode()

    assert ("https://github.com/rangermix/TwitchDropsMiner/releases/download/v2.1.1/"
            f"tdm-login-helper-2.1.1-{platform}.tar.gz") in body
    assert "tdm-login-helper-9.9.9" not in body
    # Local assets still cache-bust on the fork's own version.
    assert "/static/app.js?v=9.9.9" in body
    assert "__APP_VERSION__" not in body
