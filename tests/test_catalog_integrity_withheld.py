"""Fork: a Campaigns-only integrity rejection must not block sign-in or the refresh."""

from typing import cast

import pytest

from src.auth.browser_session import BrowserSession
from src.core.client import Twitch
from src.exceptions import GQLException, LoginException
from src.services.inventory_service import InventoryService
from tests.test_channel_campaign_discovery import (
    GAME,
    FakeTwitchGQL,
    _details,
    _fetch,
    _make_twitch,
    _offer,
    _wanted,
)


INTEGRITY = [{"message": "failed integrity check", "path": ["currentUser", "dropCampaigns"]}]
INVENTORY = {"data": {"currentUser": {"inventory": {"gameEventDrops": [], "dropCampaignsInProgress": []}}}}


def _results(campaigns_row: dict) -> list[dict]:
    return [INVENTORY, campaigns_row]


def _withheld_row(errors=None) -> dict:
    return {"errors": errors if errors is not None else INTEGRITY,
            "data": {"currentUser": {"dropCampaigns": None}}}


def test_integrity_only_campaigns_rejection_is_accepted_for_sign_in():
    assert BrowserSession.campaign_count(_results(_withheld_row())) == 0


@pytest.mark.parametrize("row", [
    _withheld_row([{"message": "service error"}]),
    _withheld_row(INTEGRITY + [{"message": "service error"}]),
    {"errors": INTEGRITY},
    {"errors": INTEGRITY, "data": {"currentUser": {"dropCampaigns": []}}},
])
def test_other_catalog_failures_still_reject_sign_in(row):
    with pytest.raises(LoginException):
        BrowserSession.campaign_count(_results(row))


def test_inventory_errors_still_reject_sign_in():
    inventory = {**INVENTORY, "errors": INTEGRITY}
    with pytest.raises(LoginException):
        BrowserSession.campaign_count([inventory, _withheld_row()])


class IntegrityGQL(FakeTwitchGQL):
    def __init__(self, *args, error=INTEGRITY, **kwargs):
        super().__init__(*args, **kwargs)
        self.error = error

    async def __call__(self, request):
        if not isinstance(request, (list, dict)) or "operationName" not in (request if isinstance(request, dict) else {}):
            return await super().__call__(request)
        if request["operationName"] == "ViewerDropsDashboard":
            raise GQLException(self.error)
        return await super().__call__(request)


@pytest.mark.asyncio
async def test_refresh_falls_back_to_channel_discovery_on_integrity_rejection():
    gql = IntegrityGQL(
        pages=[["11"]],
        offers={"11": [_offer("weekend")]},
        details={"11": [_details("weekend", acl=["11"])]},
    )
    twitch = _make_twitch(gql, [GAME])
    service = InventoryService(cast(Twitch, twitch))

    await _fetch(service, twitch)

    assert [c.name for c in twitch.inventory] == ["weekend"]
    assert _wanted(twitch) == [GAME]


@pytest.mark.asyncio
async def test_other_campaign_errors_still_fail_the_refresh():
    gql = IntegrityGQL(pages=[[]], offers={}, details={}, error=[{"message": "server blew up"}])
    twitch = _make_twitch(gql, [GAME])
    with pytest.raises(GQLException):
        await InventoryService(cast(Twitch, twitch)).fetch_inventory()


class FlakyDirectoryGQL(FakeTwitchGQL):
    """The game directory is rejected once, as Twitch does with integrity errors."""

    failures = 1

    async def __call__(self, request):
        if isinstance(request, dict) and request.get("operationName") == "DirectoryPage_Game" and self.failures:
            self.failures -= 1
            raise GQLException(INTEGRITY)
        return await super().__call__(request)


@pytest.mark.asyncio
async def test_discovery_retries_a_game_rejected_once():
    gql = FlakyDirectoryGQL(
        pages=[["11"]],
        offers={"11": [_offer("weekend")]},
        details={"11": [_details("weekend", acl=["11"])]},
    )
    twitch = _make_twitch(gql, [GAME])

    await _fetch(InventoryService(cast(Twitch, twitch)), twitch)

    assert [c.name for c in twitch.inventory] == ["weekend"]
