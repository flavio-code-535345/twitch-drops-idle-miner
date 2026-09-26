"""
Inventory service for managing campaigns, drops, and inventory fetching.

This service handles fetching campaign data from Twitch's GraphQL API,
managing the inventory state, and determining active campaigns.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from dateutil.parser import isoparse

from src.api import GQLClient
from src.config import CHANNEL_CAMPAIGNS_QUERY, GQL_OPERATIONS, GQLRawQuery
from src.exceptions import ExitRequest, MinerException
from src.i18n import _
from src.models import DropsCampaign
from src.utils import chunk


if TYPE_CHECKING:
    from src.config import JsonType
    from src.core.client import Twitch
    from src.models.channel import Channel


logger = logging.getLogger("TwitchDrops")

# Live drops-enabled channels asked per Games to Watch entry while the catalog is withheld.
# Directory order is most relevant first, so small participating channels need depth.
DISCOVERY_CHANNELS_PER_GAME = 100


class InventoryService:
    """
    Service responsible for inventory and campaign management.

    Handles:
    - Fetching campaign details from GraphQL
    - Fetching inventory (in-progress campaigns)
    - Determining active campaign for a channel
    - Managing campaign data and claimed benefits
    """

    def __init__(self, twitch: Twitch) -> None:
        """
        Initialize the inventory service.

        Args:
            twitch: The Twitch client instance
        """
        self._twitch = twitch
        # Campaigns found through channel discovery, remembered until they end.
        self._discovered: dict[str, JsonType] = {}
        # Lowercased games covered by the last discovery; None while the catalog is served.
        self._discovered_games: set[str] | None = None

    def _clear_inventory_state(self) -> None:
        """Clear derived campaign and drop state before replacing inventory."""
        self._twitch._drops.clear()
        self._twitch._campaigns.clear()
        self._twitch.gui.inv.clear()
        self._twitch.inventory.clear()
        self._twitch._mnt_triggers.clear()

    def clear_cached_state(self) -> None:
        """Clear local derived miner state while preserving credentials and settings."""
        logger.info("Clearing local derived campaign and channel state")

        self._twitch.stop_watching()
        self._twitch.restart_watching()

        tracked_channels = list(self._twitch.channels.values())
        self._twitch._remove_channel_topics(tracked_channels)
        self._twitch.channels.clear()
        self._twitch.gui.channels.clear()
        self._twitch.gui.clear_channel_selection()

        self._twitch.clear_manual_mode("Local cache cleared")
        self._twitch.wanted_games.clear()
        self._discovered.clear()
        self._discovered_games = None
        self._clear_inventory_state()
        self._twitch.gui.set_games(set())
        self._twitch.gui.broadcast_wanted_items()

        if self._twitch._mnt_task is not None and not self._twitch._mnt_task.done():
            self._twitch._mnt_task.cancel()
        self._twitch._mnt_task = None

    async def fetch_campaigns(
        self, campaigns_chunk: list[tuple[str, JsonType]]
    ) -> dict[str, JsonType]:
        """
        Fetch detailed campaign data for a chunk of campaign IDs.

        Args:
            campaigns_chunk: List of (campaign_id, campaign_data) tuples

        Returns:
            Dictionary mapping campaign IDs to their detailed data
        """
        campaign_ids: dict[str, JsonType] = dict(campaigns_chunk)
        auth_state = await self._twitch.get_auth()

        response_list_raw = await self._twitch.gql_request(
            [
                GQL_OPERATIONS["CampaignDetails"].with_variables(
                    {"channelLogin": str(auth_state.user_id), "dropID": cid}
                )
                for cid in campaign_ids
            ]
        )

        # Ensure we have a list
        response_list: list[JsonType] = (
            response_list_raw if isinstance(response_list_raw, list) else [response_list_raw]
        )

        fetched_data: dict[str, JsonType] = {
            (campaign_data := response_json["data"]["user"]["dropCampaign"])["id"]: campaign_data
            for response_json in response_list
        }

        return GQLClient.merge_data(campaign_ids, fetched_data)

    def needs_discovery(self, games_to_watch: list[str]) -> bool:
        """
        Return whether some tracked games have never been through campaign discovery.

        While Twitch withholds the campaign catalog, a game's campaigns only exist after
        an inventory fetch has searched its channels, so adding a game needs a new fetch.
        """
        return self._discovered_games is not None and any(
            name.lower() not in self._discovered_games for name in games_to_watch
        )

    async def _live_drops_channel_ids(self, game_name: str) -> list[str]:
        """Return IDs of up to DISCOVERY_CHANNELS_PER_GAME live drops-enabled channels."""
        response = await self._twitch.gql_request(
            GQL_OPERATIONS["SlugRedirect"].with_variables({"name": game_name})
        )
        slug = ((response["data"] or {}).get("game") or {}).get("slug")
        channel_ids: dict[str, None] = {}  # ordered set: pages can overlap
        cursor: str | None = None
        while slug and len(channel_ids) < DISCOVERY_CHANNELS_PER_GAME:
            variables: JsonType = {
                "limit": 30,
                "slug": slug,
                "options": {
                    "includeRestricted": ["SUB_ONLY_LIVE"],
                    "systemFilters": ["DROPS_ENABLED"],
                },
            }
            if cursor is not None:
                variables["cursor"] = cursor
            response = await self._twitch.gql_request(
                GQL_OPERATIONS["GameDirectory"].with_variables(variables)
            )
            streams = ((response["data"] or {}).get("game") or {}).get("streams") or {}
            edges = streams.get("edges") or []
            for edge in edges:
                if edge["node"]["broadcaster"] is not None:
                    channel_ids[str(edge["node"]["broadcaster"]["id"])] = None
            cursor = edges[-1].get("cursor") if edges else None
            if not cursor or not (streams.get("pageInfo") or {}).get("hasNextPage"):
                break
        return list(channel_ids)[:DISCOVERY_CHANNELS_PER_GAME]

    @staticmethod
    def _channel_campaigns(response: JsonType) -> list[JsonType]:
        return ((response.get("data") or {}).get("channel") or {}).get("viewerDropCampaigns") or []

    async def _discover_campaigns_from_channels(self, known_ids: set[str]) -> dict[str, JsonType]:
        """
        Find campaigns for Games to Watch by asking their live drops-enabled channels.

        Stands in for the campaign catalog, which Twitch withholds from the Smart TV client.
        The persisted AvailableDrops finds which campaigns each channel offers; one raw
        query per offering channel then returns their complete data (linking, reward
        types, ACL, preconditions and progress). Campaigns are remembered until they end,
        so they stay known while none of their channels happens to be live.

        Args:
            known_ids: Campaign IDs already present with complete data (skipped)

        Returns:
            Dictionary mapping campaign IDs to campaign data in CampaignDetails shape
        """
        games = list(self._twitch.settings.games_to_watch)
        offered_on: dict[str, str] = {}  # campaign ID -> a live channel offering it
        for game_name in games:
            try:
                for batch in chunk(await self._live_drops_channel_ids(game_name), 20):
                    responses = await self._twitch.gql_request(
                        [
                            GQL_OPERATIONS["AvailableDrops"].with_variables({"channelID": cid})
                            for cid in batch
                        ]
                    )
                    for channel_id, response in zip(batch, responses, strict=False):
                        for offer in self._channel_campaigns(response):
                            if offer["id"] not in known_ids:
                                offered_on.setdefault(offer["id"], channel_id)
            except MinerException:
                logger.exception(f"Campaign discovery failed for game: {game_name}")

        fresh: dict[str, JsonType] = {}
        for channel_id in dict.fromkeys(offered_on.values()):
            try:
                response = await self._twitch.gql_request(
                    GQLRawQuery(CHANNEL_CAMPAIGNS_QUERY, {"id": channel_id})
                )
            except MinerException:
                logger.exception(f"Campaign details query failed for channel: {channel_id}")
                continue
            for campaign in self._channel_campaigns(response):
                if campaign["id"] in offered_on:
                    fresh.setdefault(campaign["id"], campaign)

        now = datetime.now(timezone.utc)
        self._discovered = {
            campaign_id: campaign
            for campaign_id, campaign in {**self._discovered, **fresh}.items()
            if isoparse(campaign["endAt"]) > now
        }
        self._discovered_games = {name.lower() for name in games}
        return {
            campaign_id: self._campaign_data(campaign, with_progress=campaign_id in fresh)
            for campaign_id, campaign in self._discovered.items()
            if campaign_id not in known_ids
        }

    @staticmethod
    def _campaign_data(campaign: JsonType, *, with_progress: bool) -> JsonType:
        """
        Fill the nullable fields DropsCampaign indexes directly.

        Remembered (not freshly queried) campaigns lose per-drop progress, so their claim
        state comes from the inventory's claimed benefits instead of stale data.
        """
        drops: list[JsonType] = []
        for drop in campaign.get("timeBasedDrops") or []:
            drop = dict(drop)
            if not with_progress or drop.get("self") is None:
                drop.pop("self", None)
            drops.append(drop)
        return {
            **campaign,
            "self": campaign.get("self") or {"isAccountConnected": False},
            "allow": campaign.get("allow") or {"channels": [], "isEnabled": False},
            "timeBasedDrops": drops,
        }

    async def fetch_inventory(self) -> None:
        """
        Fetch the complete inventory including campaigns and drops.

        This method:
        1. Fetches in-progress campaigns (inventory)
        2. Fetches available campaigns
        3. Fetches detailed data for each campaign
        4. Creates DropsCampaign objects
        5. Updates GUI with campaign information
        6. Sets up maintenance triggers for campaign timing changes
        """
        status_update = self._twitch.gui.status.update
        status_update(_.t["gui"]["status"]["fetching_inventory"])

        # fetch in-progress campaigns (inventory)
        response = await self._twitch.gql_request(GQL_OPERATIONS["Inventory"])
        inventory: JsonType = response["data"]["currentUser"]["inventory"]
        ongoing_campaigns: list[JsonType] = inventory["dropCampaignsInProgress"] or []

        # this contains claimed benefit edge IDs, not drop IDs
        claimed_benefits: dict[str, datetime] = {
            b["id"]: isoparse(b["lastAwardedAt"]) for b in inventory["gameEventDrops"]
        }

        inventory_data: dict[str, JsonType] = {c["id"]: c for c in ongoing_campaigns}

        # fetch general available campaigns data (campaigns)
        response = await self._twitch.gql_request(GQL_OPERATIONS["Campaigns"])
        catalog: list[JsonType] | None = response["data"]["currentUser"]["dropCampaigns"]
        if catalog is None:
            # Twitch withholds the catalog (and campaign details) from the Smart TV client,
            # which previously left only already-started campaigns visible.
            logger.warning(
                "Twitch returned no campaign catalog; discovering campaigns "
                "through live channels of Games to Watch instead"
            )
            discovered = await self._discover_campaigns_from_channels(set(inventory_data))
            logger.info(f"Discovered {len(discovered)} campaign(s) through live channels")
            inventory_data.update(discovered)
        else:
            self._discovered.clear()
            self._discovered_games = None
        available_list: list[JsonType] = catalog or []
        applicable_statuses = ("ACTIVE", "UPCOMING")
        available_campaigns: dict[str, JsonType] = {
            c["id"]: c
            for c in available_list
            if c["status"] in applicable_statuses  # that are currently not expired
        }

        # fetch detailed data for each campaign, in chunks
        status_update(_.t["gui"]["status"]["fetching_campaigns"])
        fetch_campaigns_tasks: list[asyncio.Task[Any]] = [
            asyncio.create_task(self.fetch_campaigns(campaigns_chunk))
            for campaigns_chunk in chunk(available_campaigns.items(), 20)
        ]

        try:
            for coro in asyncio.as_completed(fetch_campaigns_tasks):
                chunk_campaigns_data = await coro
                # merge the inventory and campaigns datas together
                inventory_data = GQLClient.merge_data(inventory_data, chunk_campaigns_data)
        except Exception:
            # asyncio.as_completed doesn't cancel tasks on errors
            for task in fetch_campaigns_tasks:
                task.cancel()
            raise

        # filter out invalid campaigns
        for campaign_id in list(inventory_data.keys()):
            if inventory_data[campaign_id]["game"] is None:
                del inventory_data[campaign_id]

        # use the merged data to create campaign objects
        campaigns: list[DropsCampaign] = [
            DropsCampaign(self._twitch, campaign_data, claimed_benefits)
            for campaign_data in inventory_data.values()
        ]
        campaigns.sort(key=lambda c: c.active, reverse=True)
        campaigns.sort(key=lambda c: c.upcoming and c.starts_at or c.ends_at)
        campaigns.sort(key=lambda c: c.eligible, reverse=True)

        self._clear_inventory_state()
        switch_triggers: set[datetime] = set()
        next_hour = datetime.now(timezone.utc) + timedelta(hours=1)

        # add the campaigns to the internal inventory
        for campaign in campaigns:
            self._twitch._drops.update({drop.id: drop for drop in campaign.drops})
            if campaign.can_earn_within(next_hour):
                switch_triggers.update(campaign.time_triggers)
            self._twitch.inventory.append(campaign)
            self._twitch._campaigns[campaign.id] = campaign

        # concurrently add the campaigns into the GUI
        # NOTE: this fetches pictures from the CDN, so might be slow without a cache
        status_update(
            _.t["gui"]["status"]["adding_campaigns"].format(counter=f"(0/{len(campaigns)})")
        )
        add_campaign_tasks: list[asyncio.Task[None]] = [
            asyncio.create_task(self._twitch.gui.inv.add_campaign(campaign))
            for campaign in campaigns
        ]

        try:
            for i, coro in enumerate(asyncio.as_completed(add_campaign_tasks), start=1):
                await coro
                status_update(
                    _.t["gui"]["status"]["adding_campaigns"].format(
                        counter=f"({i}/{len(campaigns)})"
                    )
                )
                # this is needed here explicitly, because cache reads from disk don't raise this
                from src.config import State

                if self._twitch._state == State.EXIT:
                    raise ExitRequest()
        except Exception:
            # asyncio.as_completed doesn't cancel tasks on errors
            for task in add_campaign_tasks:
                task.cancel()
            raise

        self._twitch._mnt_triggers.extend(sorted(switch_triggers))

        # trim out all triggers that we're already past
        now = datetime.now(timezone.utc)
        while self._twitch._mnt_triggers and self._twitch._mnt_triggers[0] <= now:
            self._twitch._mnt_triggers.popleft()

        # NOTE: maintenance task is restarted at the end of each inventory fetch
        if self._twitch._mnt_task is not None and not self._twitch._mnt_task.done():
            self._twitch._mnt_task.cancel()
        self._twitch._mnt_task = asyncio.create_task(
            self._twitch._maintenance_service.run_maintenance_task()
        )

    def get_active_campaign(self, channel: Channel | None = None) -> DropsCampaign | None:
        """
        Determine the active campaign for a given channel (or watching channel).

        Returns the campaign with the least remaining minutes that can be earned
        on the specified channel. This is used to determine which drop is actively
        being progressed.

        Args:
            channel: The channel to check (defaults to watching channel)

        Returns:
            The active DropsCampaign, or None if no campaign can be earned
        """
        if not self._twitch.wanted_games:
            return None

        watching_channel = self._twitch.watching_channel.get_with_default(channel)
        if watching_channel is None:
            # if we aren't watching anything, we can't earn any drops
            return None

        campaigns: list[DropsCampaign] = []
        for campaign in self._twitch.inventory:
            if campaign.can_earn(watching_channel):
                campaigns.append(campaign)

        if campaigns:
            campaigns.sort(key=lambda c: c.remaining_minutes)
            return campaigns[0]

        return None
