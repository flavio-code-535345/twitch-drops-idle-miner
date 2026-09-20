import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.services.maintenance import MaintenanceService


def _make_twitch(minutes):
    return SimpleNamespace(
        settings=SimpleNamespace(minimum_refresh_interval_minutes=minutes),
        _mnt_triggers=[],
        request_inventory_refresh=MagicMock(),
        change_state=MagicMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_value", [0, -5, None])
async def test_non_positive_interval_is_clamped_and_actually_waits(bad_value):
    """A zero/negative/missing interval must not make the maintenance loop exit
    immediately and request another reload with no delay - that would hammer
    Twitch's API in a tight loop. Catch the task mid-sleep (rather than awaiting
    it to completion) to prove it's actually waiting the clamped minute, not
    racing back around instantly."""
    twitch = _make_twitch(bad_value)
    service = MaintenanceService(twitch)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(service.run_maintenance_task(), timeout=0.05)

    assert twitch.settings.minimum_refresh_interval_minutes == 1
    twitch.request_inventory_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_excessive_interval_is_clamped_to_one_day():
    twitch = _make_twitch(999_999)
    service = MaintenanceService(twitch)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(service.run_maintenance_task(), timeout=0.05)

    assert twitch.settings.minimum_refresh_interval_minutes == 1440
