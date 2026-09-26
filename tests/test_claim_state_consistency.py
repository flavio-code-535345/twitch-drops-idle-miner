"""Claims must survive inventory refreshes that race them (claims never revert)."""

import copy
from collections import deque
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import State
from src.core.client import Twitch
from src.services.inventory_service import InventoryService
from src.services.message_handlers import MessageHandlerService
from tests.test_watch_drop_filtering import _campaign, _drop


CLAIM_OK = {"data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}}}


def _claimed_copy(drop_data: dict, *, claimed: bool, minutes: int, instance: str | None) -> dict:
    data = copy.deepcopy(drop_data)
    data["self"] = {"isClaimed": claimed, "currentMinutesWatched": minutes, "dropInstanceID": instance}
    return data


def test_adopt_claim_keeps_an_earlier_claim_and_claim_id():
    previous = _campaign("c", [_drop("d5", "Drop 5", 300)]).timed_drops["d5"]
    previous.update_claim("instance-5")
    previous.is_claimed = True
    stale = _campaign("c", [_claimed_copy(_drop("d5", "Drop 5", 300), claimed=False, minutes=296, instance=None)])
    fresh = stale.timed_drops["d5"]

    fresh.adopt_claim(previous)

    assert fresh.is_claimed and fresh.claim_id == "instance-5"
    assert fresh.current_minutes == 300


def test_adopt_claim_never_unclaims():
    previous = _campaign("c", [_drop("d5", "Drop 5", 300)]).timed_drops["d5"]
    fresh = _campaign("c", [_claimed_copy(_drop("d5", "Drop 5", 300), claimed=True, minutes=300, instance="i")])
    drop = fresh.timed_drops["d5"]

    drop.adopt_claim(previous)

    assert drop.is_claimed and drop.claim_id == "i"


@pytest.mark.asyncio
async def test_refresh_built_from_pre_claim_data_keeps_the_claim():
    # The claim landed while the refresh awaited Twitch: the old object is claimed, but the
    # fetched data predates it (296/300, no instance ID).
    previous = _campaign("weekend", [_drop("d5", "Drop 5", 300)]).timed_drops["d5"]
    previous.update_claim("instance-5")
    previous.is_claimed = True
    stale_campaign = {
        "id": "weekend",
        "name": "Arena Free Weekend",
        "game": {"id": "1", "name": "Test Game"},
        "self": {"isAccountConnected": True},
        "accountLinkURL": "",
        "startAt": "2026-01-01T00:00:00Z",
        "endAt": "2099-01-01T00:00:00Z",
        "status": "ACTIVE",
        "allow": {"channels": [], "isEnabled": False},
        "timeBasedDrops": [
            _claimed_copy(_drop("d5", "Drop 5", 300), claimed=False, minutes=296, instance=None)
        ],
    }
    twitch = SimpleNamespace(
        _drops={"d5": previous},
        _campaigns={},
        inventory=[],
        _mnt_triggers=deque(),
        _mnt_task=None,
        _state=State.IDLE,
        settings=SimpleNamespace(drop_name_blacklist=[]),
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
                                "dropCampaignsInProgress": [stale_campaign],
                                "gameEventDrops": [],
                            }
                        }
                    }
                },
                {"data": {"currentUser": {"dropCampaigns": []}}},
            ]
        ),
        _maintenance_service=SimpleNamespace(run_maintenance_task=AsyncMock()),
    )

    await InventoryService(cast(Twitch, twitch)).fetch_inventory()
    await twitch._mnt_task

    refreshed = twitch._drops["d5"]
    assert refreshed is not previous
    assert refreshed.is_claimed and refreshed.claim_id == "instance-5"


class _ClaimHarness:
    def __init__(self):
        self.campaign = _campaign("weekend", [_drop("d5", "Drop 5", 300), _drop("d6", "Drop 6", 360)])
        self.twitch = self.campaign._twitch
        self.old = self.campaign.timed_drops["d5"]
        self.twitch._drops = {"d5": self.old}
        self.twitch.watching_channel.get_with_default.return_value = None
        self.twitch.gui.broadcast_wanted_items_now = AsyncMock()
        self.service = MessageHandlerService(self.twitch)

    @staticmethod
    def message(drop_id: str = "d5") -> dict:
        return {"type": "drop-claim", "data": {"drop_id": drop_id, "drop_instance_id": "instance-5"}}


@pytest.mark.asyncio
async def test_claim_finishing_after_a_refresh_marks_the_current_drop_object():
    harness = _ClaimHarness()
    refreshed_campaign = _campaign("weekend", [_drop("d5", "Drop 5", 300), _drop("d6", "Drop 6", 360)])
    refreshed = refreshed_campaign.timed_drops["d5"]

    async def gql(request):
        # A refresh swaps in new drop objects while the claim request is in flight.
        harness.twitch._drops["d5"] = refreshed
        return CLAIM_OK

    harness.twitch.gql_request = AsyncMock(side_effect=gql)
    with patch("src.services.message_handlers.asyncio.sleep", AsyncMock()):
        await harness.service.process_drops(1, harness.message())

    assert harness.old.is_claimed
    assert refreshed.is_claimed and refreshed.claim_id == "instance-5"
    harness.twitch.gui.inv.update_drop.assert_called_with(refreshed)


@pytest.mark.asyncio
async def test_claim_event_for_an_unknown_drop_requests_a_refresh():
    harness = _ClaimHarness()
    harness.twitch.gql_request = AsyncMock()

    await harness.service.process_drops(1, harness.message("unknown-drop"))

    harness.twitch.request_inventory_refresh.assert_called_once_with()
    harness.twitch.gql_request.assert_not_awaited()


def test_progress_shows_the_drop_still_gaining_minutes():
    campaign = _campaign("weekend", [_drop("d5", "Drop 5", 300), _drop("d6", "Drop 6", 360)])
    campaign.timed_drops["d5"].real_current_minutes = 300  # finished, awaiting its claim
    campaign.timed_drops["d6"].real_current_minutes = 328

    assert campaign.first_drop is campaign.timed_drops["d6"]


def test_progress_falls_back_to_a_finished_drop_when_nothing_else_progresses():
    campaign = _campaign("weekend", [_drop("d5", "Drop 5", 300)])
    campaign.timed_drops["d5"].real_current_minutes = 300

    assert campaign.first_drop is campaign.timed_drops["d5"]
