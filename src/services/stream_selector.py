from datetime import datetime, timedelta, timezone

from src.config.settings import Settings
from src.models.campaign import DropsCampaign
from src.models.game import Game


# Farm mode ignores the user's Mining Benefits filter and looks only for badges and
# emotes, regardless of it - that's the whole point of the mode.
FARM_MODE_BENEFITS: dict[str, bool] = {
    "BADGE": True,
    "EMOTE": True,
    "DIRECT_ENTITLEMENT": False,
    "UNKNOWN": False,
}


class StreamSelector:
    def _get_wanted_drops(
        self, campaign: DropsCampaign, mining_benefits: dict[str, bool], now: datetime
    ) -> list[dict]:
        wanted_drops = []
        for drop in campaign.drops:
            if (
                not drop.is_watch_drop
                or drop.is_claimed
                or drop.ends_at <= now
                or not drop.is_mineable
            ):
                continue

            filtered_benefits = drop.get_wanted_unclaimed_benefits(mining_benefits)

            if len(filtered_benefits) > 0:
                wanted_drops.append({"name": drop.name, "benefits": filtered_benefits})
        return wanted_drops

    def _build_game_entry(
        self,
        game_name: str,
        campaigns: list[DropsCampaign],
        allowed_benefits: dict[str, bool],
        now: datetime,
        next_hour: datetime,
    ) -> dict | None:
        """Build a wanted-game tree entry for a single game, or None if it has nothing mineable."""
        wanted_campaigns = []
        game_obj = None
        game_name_lower = game_name.lower()

        for campaign in campaigns:
            if campaign.game.name.lower() != game_name_lower:
                continue

            if game_obj is None:
                game_obj = campaign.game

            if not campaign.can_earn_within(next_hour):
                continue

            wanted_drops = self._get_wanted_drops(campaign, allowed_benefits, now)
            if len(wanted_drops) > 0:
                wanted_campaigns.append(
                    {
                        "id": campaign.id,
                        "name": campaign.name,
                        "url": campaign.campaign_url,
                        "drops": wanted_drops,
                    }
                )

        if game_obj is None or len(wanted_campaigns) == 0:
            return None

        return {
            "game_id": game_obj.id,
            "game_name": game_name,
            "game_icon": game_obj.box_art_url,
            "game_obj": game_obj,
            "campaigns": wanted_campaigns,
        }

    def _append_unlisted_games(
        self,
        wanted_games: list[dict],
        settings: Settings,
        campaigns: list[DropsCampaign],
        now: datetime,
        stamp: datetime,
    ) -> None:
        if getattr(settings, "priority_list_only", True) is not False:
            return

        selected_names = {game_name.lower() for game_name in settings.games_to_watch}
        queued_ids = {
            game["game_obj"].id
            for game in wanted_games
            if game.get("game_obj") is not None
        }

        unlisted_campaigns: dict[int, dict] = {}
        for campaign in campaigns:
            campaign_game = campaign.game
            if campaign_game.name.lower() in selected_names or campaign_game.id in queued_ids:
                continue
            if not campaign.can_earn_within(stamp):
                continue
            if not self._get_wanted_drops(campaign, settings.mining_benefits, now):
                continue
            if campaign_game.id not in unlisted_campaigns:
                unlisted_campaigns[campaign_game.id] = {
                    "game_id": campaign_game.id,
                    "game_name": campaign_game.name,
                    "game_icon": campaign_game.box_art_url,
                    "game_obj": campaign_game,
                    "campaigns": [],
                }
            unlisted_campaigns[campaign_game.id]["campaigns"].append(campaign)

        fallback_games = sorted(
            unlisted_campaigns.values(),
            key=lambda game: (
                min(campaign.ends_at for campaign in game["campaigns"]),
                game["game_name"].lower(),
            ),
        )
        for fallback_game in fallback_games:
            fallback_game["campaigns"] = [
                {
                    "id": campaign.id,
                    "name": campaign.name,
                    "url": campaign.campaign_url,
                    "drops": self._get_wanted_drops(campaign, settings.mining_benefits, now),
                }
                for campaign in sorted(fallback_game["campaigns"], key=lambda campaign: campaign.ends_at)
            ]
            wanted_games.append(fallback_game)

    def _append_farm_games(
        self,
        wanted_games: list[dict],
        settings: Settings,
        campaigns: list[DropsCampaign],
        now: datetime,
        next_hour: datetime,
    ) -> None:
        """Fork Farm Mode: append every remaining game's badge and emote drops, marked FARM.

        Runs after upstream's priority fallback, so each game is queued once: games already
        selected or queued by the fallback (with the user's own reward preferences) are
        skipped, and farming covers the rest regardless of the Mining Benefits filter.
        """
        # getattr: upstream's tests build minimal settings objects without this fork setting.
        if not getattr(settings, "farm_mode", False):
            return

        selected_names = {game_name.lower() for game_name in settings.games_to_watch}
        queued_ids = {
            game["game_obj"].id
            for game in wanted_games
            if game.get("game_obj") is not None
        }
        # Dedupe case-insensitively (keyed by lowercased name, keeping the first-seen
        # casing) rather than by the raw string: Twitch's campaign payloads don't
        # guarantee identical display-name casing for the same category across
        # different campaigns, and deduping on the exact string would otherwise
        # produce two duplicate tree entries - each independently matching (and
        # showing) every campaign for that game - for what is really one game.
        farm_games_by_key: dict[str, str] = {}
        for campaign in campaigns:
            key = campaign.game.name.lower()
            if key in selected_names or key in farm_games_by_key or campaign.game.id in queued_ids:
                continue
            farm_games_by_key[key] = campaign.game.name
        for game_name in sorted(farm_games_by_key.values(), key=str.lower):
            entry = self._build_game_entry(game_name, campaigns, FARM_MODE_BENEFITS, now, next_hour)
            if entry is not None:
                entry["farm_mode"] = True
                wanted_games.append(entry)

    def _get_wanted_game_tree(
        self, settings: Settings, campaigns: list[DropsCampaign]
    ) -> list[dict]:
        """
        Get the hierarchical tree of wanted items (Games -> Campaigns -> Drops -> Benefits).
        Include campaigns available within an hour, with selected games first.

        Order: games_to_watch in their saved order (filtered by Mining Benefits, one entry
        per game ID), then upstream's priority fallback games when priority_list_only is
        off, then this fork's Farm Mode games. Channel priority follows list position, so
        explicitly tracked games always take priority over fallback and farmed ones.
        """
        wanted_games: list[dict] = []
        queued_ids: set[int] = set()
        now = datetime.now(timezone.utc)
        next_hour = now + timedelta(hours=1)

        for game_name in settings.games_to_watch:
            entry = self._build_game_entry(
                game_name, campaigns, settings.mining_benefits, now, next_hour
            )
            if entry is None or entry["game_obj"].id in queued_ids:
                continue
            queued_ids.add(entry["game_obj"].id)
            wanted_games.append(entry)

        self._append_unlisted_games(wanted_games, settings, campaigns, now, next_hour)
        self._append_farm_games(wanted_games, settings, campaigns, now, next_hour)
        return wanted_games

    def get_wanted_game_tree(
        self, settings: Settings, campaigns: list[DropsCampaign]
    ) -> list[dict]:
        return [
            {**game, "game_obj": None} for game in self._get_wanted_game_tree(settings, campaigns)
        ]

    def get_wanted_games(self, settings: Settings, campaigns: list[DropsCampaign]) -> list[Game]:
        return [game["game_obj"] for game in self._get_wanted_game_tree(settings, campaigns)]
