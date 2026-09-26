from collections import deque
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import State
from src.core.client import Twitch
from src.exceptions import GQLException
from src.services.inventory_service import InventoryService
from src.services.stream_selector import StreamSelector


NOW = datetime.now(timezone.utc)


def _iso(delta_hours: float) -> str:
    return (NOW + timedelta(hours=delta_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _offer(campaign_id: str, name: str, benefit_id: str) -> dict:
    """A campaign as DropsHighlightService_AvailableDrops returns it (no self/allow/status)."""
    return {
        "id": campaign_id,
        "name": name,
        "game": {"id": "1", "name": "Escape from Tarkov: Arena"},
        "detailsURL": "https://example.test/details",
        "endAt": _iso(5),
        "imageURL": "https://example.test/campaign.png",
        "eventBasedDrops": [],
        "timeBasedDrops": [
            {
                "id": f"{campaign_id}-drop1",
                "name": "Drop 1",
                "startAt": _iso(-6),
                "endAt": _iso(5),
                "requiredMinutesWatched": 60,
                "benefitEdges": [
                    {
                        "benefit": {
                            "id": benefit_id,
                            "name": "Reward",
                            "game": {"id": "1", "name": "Escape from Tarkov: Arena"},
                            "imageAssetURL": "https://example.test/reward.png",
                        },
                        "entitlementLimit": 1,
                    }
                ],
            }
        ],
    }


def _broadcaster(channel_id: str, login: str) -> dict:
    return {"node": {"broadcaster": {"id": channel_id, "login": login, "displayName": login}}}


def _make_twitch(gql_responses: list, games_to_watch: list[str], claimed: list | None = None):
    return SimpleNamespace(
        _drops={},
        _campaigns={},
        inventory=[],
        _mnt_triggers=deque(),
        _mnt_task=None,
        _state=State.IDLE,
        settings=SimpleNamespace(
            games_to_watch=games_to_watch,
            drop_name_blacklist=[],
            farm_mode=False,
            mining_benefits={"BADGE": True, "EMOTE": True, "DIRECT_ENTITLEMENT": True, "UNKNOWN": True},
        ),
        gui=SimpleNamespace(
            status=SimpleNamespace(update=MagicMock()),
            inv=SimpleNamespace(clear=MagicMock(), add_campaign=AsyncMock()),
            channels=MagicMock(),
        ),
        gql_request=AsyncMock(
            side_effect=[
                {
                    "data": {
                        "currentUser": {
                            "inventory": {
                                "dropCampaignsInProgress": [],
                                "gameEventDrops": claimed or [],
                            }
                        }
                    }
                },
                {"data": {"currentUser": {"dropCampaigns": None}}},
                *gql_responses,
            ]
        ),
        _maintenance_service=SimpleNamespace(run_maintenance_task=AsyncMock()),
    )


def _discovery_responses(offers_by_channel: dict[str, list]) -> list:
    return [
        {"data": {"game": {"slug": "escape-from-tarkov-arena"}}},
        {
            "data": {
                "game": {
                    "streams": {
                        "edges": [
                            _broadcaster(channel_id, f"login{channel_id}")
                            for channel_id in offers_by_channel
                        ]
                    }
                }
            }
        },
        [
            {"data": {"channel": {"viewerDropCampaigns": offers}}}
            for offers in offers_by_channel.values()
        ],
    ]


@pytest.mark.asyncio
async def test_null_catalog_discovers_campaigns_from_offering_channels():
    free_weekend = _offer("free-weekend", "Arena Free Weekend", "benefit-1")
    twitch = _make_twitch(
        _discovery_responses({"11": [free_weekend], "22": [free_weekend], "33": None}),
        ["Escape from Tarkov: Arena"],
    )

    await InventoryService(cast(Twitch, twitch)).fetch_inventory()

    [campaign] = twitch.inventory
    assert campaign.name == "Arena Free Weekend"
    # Only channels that reported the campaign are watch candidates.
    assert sorted(channel.id for channel in campaign.allowed_channels) == [11, 22]
    assert campaign.active and campaign.eligible
    wanted = StreamSelector().get_wanted_games(twitch.settings, twitch.inventory)
    assert [game.name for game in wanted] == ["Escape from Tarkov: Arena"]
    await twitch._mnt_task


@pytest.mark.asyncio
async def test_previously_claimed_rewards_stay_claimed_for_discovered_campaigns():
    twitch = _make_twitch(
        _discovery_responses({"11": [_offer("free-weekend", "Arena Free Weekend", "benefit-1")]}),
        ["Escape from Tarkov: Arena"],
        claimed=[{"id": "benefit-1", "lastAwardedAt": _iso(-1)}],
    )

    await InventoryService(cast(Twitch, twitch)).fetch_inventory()

    [campaign] = twitch.inventory
    assert all(drop.is_claimed for drop in campaign.drops)
    assert StreamSelector().get_wanted_games(twitch.settings, twitch.inventory) == []
    await twitch._mnt_task


@pytest.mark.asyncio
async def test_discovery_failure_for_one_game_does_not_abort_the_others():
    twitch = _make_twitch(
        [
            GQLException("boom"),
            *_discovery_responses({"11": [_offer("free-weekend", "Arena Free Weekend", "b")]}),
        ],
        ["Broken Game", "Escape from Tarkov: Arena"],
    )

    await InventoryService(cast(Twitch, twitch)).fetch_inventory()

    assert [campaign.name for campaign in twitch.inventory] == ["Arena Free Weekend"]
    await twitch._mnt_task


@pytest.mark.asyncio
async def test_available_catalog_skips_channel_discovery():
    twitch = _make_twitch([], ["Escape from Tarkov: Arena"])
    twitch.gql_request.side_effect = [
        {"data": {"currentUser": {"inventory": {"dropCampaignsInProgress": [], "gameEventDrops": []}}}},
        {"data": {"currentUser": {"dropCampaigns": []}}},
    ]

    await InventoryService(cast(Twitch, twitch)).fetch_inventory()

    assert twitch.gql_request.await_count == 2
    await twitch._mnt_task
