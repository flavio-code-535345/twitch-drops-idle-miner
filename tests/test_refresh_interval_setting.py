import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import default_settings
from src.web.managers.settings import SettingsManager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("submitted", "stored"),
    [(0, 1), (-5, 1), (99_999, 1440), (45, 45)],
)
async def test_refresh_interval_is_clamped_before_saving(submitted, stored):
    settings = SimpleNamespace(**copy.deepcopy(default_settings))
    settings.save = MagicMock()
    manager = SettingsManager(AsyncMock(), settings, MagicMock())

    response = manager.update_settings({"minimum_refresh_interval_minutes": submitted})

    assert settings.minimum_refresh_interval_minutes == stored
    assert response["minimum_refresh_interval_minutes"] == stored
