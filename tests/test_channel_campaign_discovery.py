from collections import deque
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import State
from src.core.client import Twitch
from src.exceptions import GQLException
from src.services import inventory_service
from src.services.inventory_service import InventoryService
from src.services.stream_selector import StreamSelector


NOW = datetime.now(timezone.utc)
GAME = "Escape from Tarkov: Arena"


def _iso(delta_hours: float) -> str:
    return (NOW + timedelta(hours=delta_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _offer(campaign_id: str) -> dict:
    """Persisted AvailableDrops entry: identifies the campaign but lacks most fields."""
    return {"id": campaign_id, "name": campaign_id, "game": {"id": "1", "name": GAME}}


def _details(
    campaign_id: str,
    *,
    linked: bool = True,
    reward_type: str = "DIRECT_ENTITLEMENT",
    claimed: bool = False,
    acl: list[str] | None = None,
    ends_in_hours: float = 5,
) -> dict:
    """Raw ChannelDropCampaigns entry, shaped like the live Twitch response."""
    return {
        "id": campaign_id,
        "name": campaign_id,
        "status": "ACTIVE",
        "startAt": _iso(-6),
        "endAt": _iso(ends_in_hours),
        "accountLinkURL": "https://example.test/link",
        "self": {"isAccountConnected": linked},
        "allow": {
            "isEnabled": acl is not None,
            "channels": [{"id": c, "name": f"login{c}", "displayName": c} for c in acl or []],
        },
        "game": {"id": "1", "name": GAME, "displayName": GAME, "boxArtURL": "https://x/{width}x{height}"},
        "timeBasedDrops": [
            {
                "id": f"{campaign_id}-drop1",
                "name": "Drop 1",
                "startAt": _iso(-6),
                "endAt": _iso(ends_in_hours),
                "requiredMinutesWatched": 60,
                "preconditionDrops": None,
                "self": {
                    "currentMinutesWatched": 60 if claimed else 15,
                    "isClaimed": claimed,
                    "dropInstanceID": None,
                },
                "benefitEdges": [
                    {
                        "entitlementLimit": 1,
                        "benefit": {
                            "id": f"{campaign_id}-benefit",
                            "name": "Reward",
                            "distributionType": reward_type,
                            "imageAssetURL": "https://example.test/reward.png",
                        },
                    }
                ],
            }
        ],
    }


class FakeTwitchGQL:
    """Answers each discovery request the way the live Smart TV session does."""

    def __init__(self, pages: list[list[str]], offers: dict, details: dict, claimed=None):
        self.pages = pages  # directory pages of live channel IDs
        self.offers = offers  # channel ID -> persisted AvailableDrops entries
        self.details = details  # channel ID -> raw ChannelDropCampaigns entries
        self.claimed = claimed or []
        self.fail_games: set[str] = set()
        self.cursors: list[str | None] = []

    async def __call__(self, request):
        if isinstance(request, list):
            return [
                {"data": {"channel": {"viewerDropCampaigns": self.offers.get(op["variables"]["channelID"])}}}
                for op in request
            ]
        if "query" in request:
            channel_id = request["variables"]["id"]
            return {"data": {"channel": {"viewerDropCampaigns": self.details.get(channel_id)}}}
        name, variables = request["operationName"], request.get("variables", {})
        if name == "Inventory":
            inventory = {"dropCampaignsInProgress": [], "gameEventDrops": self.claimed}
            return {"data": {"currentUser": {"inventory": inventory}}}
        if name == "ViewerDropsDashboard":
            return {"data": {"currentUser": {"dropCampaigns": None}}}
        if name == "DirectoryGameRedirect":
            if variables["name"] in self.fail_games:
                raise GQLException("boom")
            return {"data": {"game": {"slug": "arena"}}}
        if name == "DirectoryPage_Game":
            cursor = variables.get("cursor")
            self.cursors.append(cursor)
            index = int(cursor or 0)
            page = self.pages[index] if index < len(self.pages) else []
            edges = [
                {"cursor": str(index + 1), "node": {"broadcaster": {"id": c, "login": c}}}
                for c in page
            ]
            has_next = index + 1 < len(self.pages)
            return {"data": {"game": {"streams": {"edges": edges, "pageInfo": {"hasNextPage": has_next}}}}}
        raise AssertionError(f"unexpected request {name}")


def _make_twitch(gql: FakeTwitchGQL, games_to_watch: list[str], benefits: dict | None = None):
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
            mining_benefits=benefits
            or {"BADGE": True, "EMOTE": True, "DIRECT_ENTITLEMENT": True, "UNKNOWN": True},
        ),
        gui=SimpleNamespace(
            status=SimpleNamespace(update=MagicMock()),
            inv=SimpleNamespace(clear=MagicMock(), add_campaign=AsyncMock()),
            channels=MagicMock(),
        ),
        gql_request=AsyncMock(side_effect=gql.__call__),
        _maintenance_service=SimpleNamespace(run_maintenance_task=AsyncMock()),
    )


async def _fetch(service: InventoryService, twitch) -> None:
    await service.fetch_inventory()
    await twitch._mnt_task


def _wanted(twitch) -> list[str]:
    return [g.name for g in StreamSelector().get_wanted_games(twitch.settings, twitch.inventory)]


@pytest.mark.asyncio
async def test_discovered_campaigns_carry_twitchs_real_fields():
    gql = FakeTwitchGQL(
        pages=[["11", "22"]],
        offers={"11": [_offer("weekend")], "22": [_offer("weekend")]},
        details={"11": [_details("weekend", acl=["11", "22", "99"])]},
    )
    twitch = _make_twitch(gql, [GAME])

    await _fetch(InventoryService(cast(Twitch, twitch)), twitch)

    [campaign] = twitch.inventory
    assert campaign.linked is True
    assert [b.type.name for d in campaign.drops for b in d.benefits] == ["DIRECT_ENTITLEMENT"]
    # The real ACL, including participants that weren't among the sampled live channels.
    assert sorted(c.id for c in campaign.allowed_channels) == [11, 22, 99]
    assert [d.current_minutes for d in campaign.drops] == [15]
    assert campaign.game.box_art_url
    assert _wanted(twitch) == [GAME]
    # One complete-data query covers every campaign a channel offers.
    raw_queries = [c.args[0] for c in twitch.gql_request.await_args_list if "query" in c.args[0]]
    assert [q["variables"]["id"] for q in raw_queries] == ["11"]


@pytest.mark.asyncio
async def test_unlinked_item_campaign_is_not_mined():
    gql = FakeTwitchGQL(
        pages=[["11"]], offers={"11": [_offer("items")]}, details={"11": [_details("items", linked=False)]}
    )
    twitch = _make_twitch(gql, [GAME])

    await _fetch(InventoryService(cast(Twitch, twitch)), twitch)

    assert twitch.inventory[0].eligible is False
    assert _wanted(twitch) == []


@pytest.mark.asyncio
async def test_mining_benefits_filter_uses_real_reward_types():
    gql = FakeTwitchGQL(
        pages=[["11"]],
        offers={"11": [_offer("badge"), _offer("items")]},
        details={"11": [_details("badge", reward_type="BADGE"), _details("items")]},
    )
    badges_only = {"BADGE": True, "EMOTE": False, "DIRECT_ENTITLEMENT": False, "UNKNOWN": False}
    twitch = _make_twitch(gql, [GAME], benefits=badges_only)

    await _fetch(InventoryService(cast(Twitch, twitch)), twitch)

    [entry] = StreamSelector().get_wanted_game_tree(twitch.settings, twitch.inventory)
    assert [c["name"] for c in entry["campaigns"]] == ["badge"]


@pytest.mark.asyncio
async def test_campaigns_are_remembered_while_no_participant_is_live():
    gql = FakeTwitchGQL(
        pages=[["11"]], offers={"11": [_offer("weekend")]}, details={"11": [_details("weekend", acl=["11"])]}
    )
    twitch = _make_twitch(gql, [GAME])
    service = InventoryService(cast(Twitch, twitch))
    await _fetch(service, twitch)

    gql.pages = [[]]  # every participating channel went offline
    gql.claimed = [{"id": "weekend-benefit", "lastAwardedAt": _iso(-1)}]
    await _fetch(service, twitch)

    [campaign] = twitch.inventory
    assert [c.id for c in campaign.allowed_channels] == [11]
    # Remembered data drops stale progress; claims come from the inventory's rewards.
    assert all(drop.is_claimed for drop in campaign.drops)


@pytest.mark.asyncio
async def test_remembered_campaigns_are_dropped_once_they_end():
    gql = FakeTwitchGQL(
        pages=[["11"]],
        offers={"11": [_offer("weekend")]},
        details={"11": [_details("weekend", ends_in_hours=-1)]},
    )
    twitch = _make_twitch(gql, [GAME])

    await _fetch(InventoryService(cast(Twitch, twitch)), twitch)

    assert twitch.inventory == []


@pytest.mark.asyncio
async def test_directory_is_paged_until_the_channel_cap(monkeypatch):
    monkeypatch.setattr(inventory_service, "DISCOVERY_CHANNELS_PER_GAME", 3)
    gql = FakeTwitchGQL(
        pages=[["1", "2"], ["3", "4"], ["5"]],
        offers={"3": [_offer("small")]},
        details={"3": [_details("small")]},
    )
    twitch = _make_twitch(gql, [GAME])

    await _fetch(InventoryService(cast(Twitch, twitch)), twitch)

    assert gql.cursors == [None, "1"]  # stops once 3 channels are collected
    assert [c.name for c in twitch.inventory] == ["small"]


@pytest.mark.asyncio
async def test_discovery_failure_for_one_game_does_not_abort_the_others():
    gql = FakeTwitchGQL(
        pages=[["11"]], offers={"11": [_offer("weekend")]}, details={"11": [_details("weekend")]}
    )
    gql.fail_games = {"Broken Game"}
    twitch = _make_twitch(gql, ["Broken Game", GAME])

    await _fetch(InventoryService(cast(Twitch, twitch)), twitch)

    assert [c.name for c in twitch.inventory] == ["weekend"]


@pytest.mark.asyncio
async def test_new_games_need_discovery_only_while_the_catalog_is_withheld():
    gql = FakeTwitchGQL(pages=[[]], offers={}, details={})
    twitch = _make_twitch(gql, [GAME])
    service = InventoryService(cast(Twitch, twitch))
    assert service.needs_discovery([GAME, "Rust"]) is False  # nothing fetched yet

    await _fetch(service, twitch)
    assert service.needs_discovery([GAME.upper()]) is False
    assert service.needs_discovery([GAME, "Rust"]) is True

    gql_catalog = AsyncMock(
        side_effect=[
            {"data": {"currentUser": {"inventory": {"dropCampaignsInProgress": [], "gameEventDrops": []}}}},
            {"data": {"currentUser": {"dropCampaigns": []}}},
        ]
    )
    twitch.gql_request = gql_catalog
    await _fetch(service, twitch)
    assert service.needs_discovery([GAME, "Rust"]) is False
    assert gql_catalog.await_count == 2  # no discovery requests when the catalog is served
