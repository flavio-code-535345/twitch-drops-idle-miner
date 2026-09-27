import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.config import State
from src.core.client import Twitch
from src.services.refresh_log import RefreshLog
from src.web import app as web_app_module
from src.web.gui_manager import WebGUIManager


def test_snapshot_lists_newest_first_and_schedules_from_last_success():
    log = RefreshLog()
    first = log.start("startup")
    first.finish(7, (3, 6))
    failed = log.start("manual")
    failed.fail("boom")
    running = log.start("scheduled")

    snapshot = log.snapshot(30)

    assert [e["trigger"] for e in snapshot["entries"]] == ["scheduled", "manual", "startup"]
    assert snapshot["entries"][0]["finished_at"] is None  # still running
    assert snapshot["entries"][1]["error"] == "boom"
    assert snapshot["entries"][2] | {"started_at": None, "finished_at": None} == {
        "trigger": "startup",
        "started_at": None,
        "finished_at": None,
        "campaigns": 7,
        "discovered": 3,
        "games_searched": 6,
        "error": None,
    }
    expected = first.finished_at + timedelta(minutes=30)
    assert datetime.fromisoformat(snapshot["next_scheduled_at"]) == expected
    assert running.error is None


def test_catalog_searches_report_no_discovery_and_log_is_bounded():
    log = RefreshLog()
    for _ in range(RefreshLog.MAX_ENTRIES + 5):
        log.start("scheduled").finish(12, None)

    snapshot = log.snapshot(30)

    assert len(snapshot["entries"]) == RefreshLog.MAX_ENTRIES
    assert snapshot["entries"][0]["discovered"] is None
    assert RefreshLog().snapshot(30)["next_scheduled_at"] is None


def _client() -> Twitch:
    client = object.__new__(Twitch)
    client._state = State.IDLE
    client._state_change = asyncio.Event()
    client._inventory_refresh_pending = False
    client._clear_cache_pending = False
    client.refresh_log = RefreshLog()
    client.gui = MagicMock()
    return client


def test_coalesced_refresh_requests_keep_the_first_trigger():
    client = _client()
    client.request_inventory_refresh(trigger="scheduled")
    client.request_inventory_refresh(trigger="manual")

    assert client._inventory_refresh_trigger == "scheduled"


@pytest.mark.asyncio
async def test_logged_fetch_records_trigger_result_and_broadcasts():
    client = _client()
    client.request_inventory_refresh(trigger="games_changed")
    client.inventory = [object()] * 5
    client._inventory_service = SimpleNamespace(last_discovery=(2, 4))
    client.fetch_inventory = AsyncMock()

    await client._fetch_inventory_logged()

    [entry] = client.refresh_log.snapshot(30)["entries"]
    assert (entry["trigger"], entry["campaigns"], entry["discovered"], entry["games_searched"]) == (
        "games_changed", 5, 2, 4,
    )
    assert entry["finished_at"] is not None
    assert client.gui.broadcast_refresh_log.call_count == 2  # started, finished
    assert client._inventory_refresh_trigger == "other"


@pytest.mark.asyncio
async def test_logged_fetch_records_failures_and_reraises():
    client = _client()
    client._inventory_refresh_trigger = "scheduled"
    client.fetch_inventory = AsyncMock(side_effect=RuntimeError("Twitch unreachable"))

    with pytest.raises(RuntimeError):
        await client._fetch_inventory_logged()

    [entry] = client.refresh_log.snapshot(30)["entries"]
    assert entry["error"] == "Twitch unreachable" and entry["finished_at"] is not None


def test_gui_snapshot_uses_the_clamped_interval():
    manager = object.__new__(WebGUIManager)
    manager._twitch = SimpleNamespace(
        settings=SimpleNamespace(minimum_refresh_interval_minutes=0), refresh_log=RefreshLog()
    )

    assert manager.get_refresh_log()["interval_minutes"] == 1


@pytest.mark.asyncio
async def test_refresh_log_endpoint():
    gui = MagicMock()
    gui.get_refresh_log.return_value = {"entries": [], "interval_minutes": 30, "next_scheduled_at": None}
    with patch.object(web_app_module, "gui_manager", gui):
        assert (await web_app_module.get_refresh_log())["interval_minutes"] == 30
    with patch.object(web_app_module, "gui_manager", None), pytest.raises(HTTPException):
        await web_app_module.get_refresh_log()
