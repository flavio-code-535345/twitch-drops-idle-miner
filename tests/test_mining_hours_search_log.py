"""Fork: upstream 2.3 mining hours as seen by the Campaign Searches log."""

import asyncio
import copy
import unittest
from unittest.mock import MagicMock, patch

from src.config.settings import Settings, default_settings
from src.core.client import Twitch


class TestMiningHoursSearchLog(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        with patch("src.config.settings.json_load", return_value=copy.deepcopy(default_settings)):
            self.twitch = Twitch(Settings())
        self.twitch.gui = MagicMock()
        self.open = False
        self.twitch.is_mining_time = MagicMock(side_effect=lambda: self.open)

    async def asyncTearDown(self):
        await self.twitch._stop_mining_schedule()
        if self.twitch._watching_task is not None:
            self.twitch._watching_task.cancel()
            await asyncio.gather(self.twitch._watching_task, return_exceptions=True)

    async def test_window_opening_logs_a_mining_hours_search_and_rebroadcasts(self):
        entered = asyncio.Event()

        async def watch():
            entered.set()
            await asyncio.Event().wait()

        self.twitch._watch_service.watch_loop = watch
        self.twitch._schedule_task = asyncio.create_task(self.twitch._mining_schedule_loop())
        for _ in range(5):
            await asyncio.sleep(0)
        self.twitch.gui.broadcast_refresh_log.reset_mock()

        self.open = True
        self.twitch._schedule_changed.set()
        await asyncio.wait_for(entered.wait(), 1)

        self.assertTrue(self.twitch._inventory_refresh_pending)
        self.assertEqual(self.twitch._inventory_refresh_trigger, "mining_hours")
        # The search log's "paused" summary changes at the boundary.
        self.twitch.gui.broadcast_refresh_log.assert_called()

    async def test_pending_scheduled_search_keeps_its_trigger_when_the_window_opens(self):
        self.twitch.request_inventory_refresh(trigger="scheduled")
        entered = asyncio.Event()

        async def watch():
            entered.set()
            await asyncio.Event().wait()

        self.twitch._watch_service.watch_loop = watch
        self.twitch._schedule_task = asyncio.create_task(self.twitch._mining_schedule_loop())
        for _ in range(5):
            await asyncio.sleep(0)
        self.open = True
        self.twitch._schedule_changed.set()
        await asyncio.wait_for(entered.wait(), 1)

        self.assertEqual(self.twitch._inventory_refresh_trigger, "scheduled")
