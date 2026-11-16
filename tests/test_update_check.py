import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.services.release_service import ForkReleaseService, Release, ReleaseService
from src.version import UPSTREAM_VERSION, __version__
from src.web import app as web
from src.web.app import _is_newer_version
from tests.test_release_notifications import github


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("1.3.10", "1.3.9", True),
        ("1.3.9", "1.3.10", False),
        ("1.10.0", "1.9.9", True),
        ("2.0.0", "1.99.99", True),
        ("1.3.4", "1.3.4", False),
        ("1.0.0", "1.3.4", False),
        (None, "1.3.4", False),
        ("1.3.5-beta", "1.3.4", False),
        # Fork patch releases carry a fourth component.
        ("2.6.0.1", "2.6.0", True),
        ("2.6.0", "2.6.0.1", False),
        ("2.7.0", "2.6.0.9", True),
    ],
)
def test_versions_compare_numerically(candidate, current, expected):
    assert _is_newer_version(candidate, current) is expected


def test_fork_release_tags_may_carry_a_fourth_component_but_upstream_tags_may_not():
    assert ForkReleaseService.parse_version("v2.6.0.1") == (2, 6, 0)
    assert ForkReleaseService.parse_version("v2.6.0") == (2, 6, 0)
    assert ReleaseService.parse_version("v2.6.0.1") is None
    for tag in ("2.6.0.01", "2.6.0.1.2", "2.6.0.", "latest", None):
        assert ForkReleaseService.parse_version(tag) is None


def test_new_release_alerts_compare_with_the_synced_upstream_version():
    # MY_VERSION may carry a fork-only fourth component (2.6.0.1), which the strict upstream
    # format rejects: comparing with it would silently stop every new-release alert.
    assert inspect.signature(ReleaseService.is_newer).parameters["current"].default == UPSTREAM_VERSION
    major, minor, patch_level = map(int, UPSTREAM_VERSION.split("."))
    assert ReleaseService.is_newer(f"{major}.{minor}.{patch_level + 1}")
    assert not ReleaseService.is_newer(UPSTREAM_VERSION)
    assert not ReleaseService.is_newer("99.0.0", f"{UPSTREAM_VERSION}.1")


@pytest.mark.asyncio
async def test_fork_release_lookup_reads_this_forks_repository():
    with github({"tag_name": "v2.6.0.1"}) as session_factory:
        assert await ForkReleaseService.fetch_latest() == Release(
            "2.6.0.1", f"{ForkReleaseService.RELEASES_URL}/tag/v2.6.0.1"
        )
    assert session_factory().get.call_args.args[0] == ForkReleaseService.API_URL
    assert ForkReleaseService.API_URL != ReleaseService.API_URL


@pytest.mark.asyncio
async def test_version_endpoint_checks_the_fork_and_upstream_independently(monkeypatch):
    diagnostics = object()
    monkeypatch.setattr(web, "twitch_client", SimpleNamespace(diagnostics=diagnostics))
    fork_release = Release("9.0.0.1", f"{ForkReleaseService.RELEASES_URL}/tag/v9.0.0.1")
    fork_lookup, upstream_lookup = AsyncMock(return_value=fork_release), AsyncMock(return_value=None)
    with (
        patch.object(ForkReleaseService, "fetch_latest", fork_lookup),
        patch.object(ReleaseService, "fetch_latest", upstream_lookup),
    ):
        result = await web.get_version()
    # Both GitHub lookups are recorded in the miner's diagnosis data.
    fork_lookup.assert_awaited_once_with(diagnostics)
    upstream_lookup.assert_awaited_once_with(diagnostics)
    assert result == {
        "current_version": __version__,
        "latest_version": "9.0.0.1",
        "update_available": True,
        "download_url": fork_release.url,
        "upstream_version": UPSTREAM_VERSION,
        "upstream_latest_version": None,
        "upstream_update_available": False,
        "upstream_url": ReleaseService.RELEASES_URL,
    }

    upstream_release = Release("9.0.0", f"{ReleaseService.RELEASES_URL}/tag/v9.0.0")
    with (
        patch.object(ForkReleaseService, "fetch_latest", AsyncMock(return_value=None)),
        patch.object(ReleaseService, "fetch_latest", AsyncMock(return_value=upstream_release)),
    ):
        result = await web.get_version()
    assert result["latest_version"] is None and result["update_available"] is False
    assert result["download_url"] == ForkReleaseService.RELEASES_URL
    assert result["upstream_latest_version"] == "9.0.0" and result["upstream_update_available"] is True
    assert result["upstream_url"] == upstream_release.url
