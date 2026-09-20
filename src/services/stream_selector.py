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

            wanted_drops = []
            for drop in campaign.drops:
                if (
                    not drop.is_watch_drop
                    or drop.is_claimed
                    or drop.ends_at <= now
                    or not drop.is_mineable
                ):
                    continue

                filtered_benefits = drop.get_wanted_unclaimed_benefits(allowed_benefits)

                if len(filtered_benefits) > 0:
                    wanted_drops.append({"name": drop.name, "benefits": filtered_benefits})

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

    def _get_wanted_game_tree(
        self, settings: Settings, campaigns: list[DropsCampaign]
    ) -> list[dict]:
        """
        Get the hierarchical tree of wanted items (Games -> Campaigns -> Drops -> Benefits).
        Ignoring 'can earn within' time constraint.

        Games explicitly listed in games_to_watch always come first, in that order, and are
        filtered by the user's Mining Benefits selection. When farm_mode is enabled, every
        other game with a currently mineable badge or emote drop is appended afterwards, so
        explicitly tracked games always take priority over farmed ones (both for the queue
        order here and for the channel priority it drives, which sorts by list position).
        """
        wanted_games = []
        now = datetime.now(timezone.utc)
        next_hour = now + timedelta(hours=1)

        watched_names_lower: set[str] = set()
        for game_name in settings.games_to_watch:
            watched_names_lower.add(game_name.lower())
            entry = self._build_game_entry(
                game_name, campaigns, settings.mining_benefits, now, next_hour
            )
            if entry is not None:
                wanted_games.append(entry)

        if settings.farm_mode:
            # Dedupe case-insensitively (keyed by lowercased name, keeping the first-seen
            # casing) rather than by the raw string: Twitch's campaign payloads don't
            # guarantee identical display-name casing for the same category across
            # different campaigns, and deduping on the exact string would otherwise
            # produce two duplicate tree entries - each independently matching (and
            # showing) every campaign for that game - for what is really one game.
            farm_games_by_key: dict[str, str] = {}
            for campaign in campaigns:
                key = campaign.game.name.lower()
                if key not in watched_names_lower and key not in farm_games_by_key:
                    farm_games_by_key[key] = campaign.game.name
            farm_game_names = sorted(farm_games_by_key.values(), key=str.lower)
            for game_name in farm_game_names:
                entry = self._build_game_entry(
                    game_name, campaigns, FARM_MODE_BENEFITS, now, next_hour
                )
                if entry is not None:
                    entry["farm_mode"] = True
                    wanted_games.append(entry)

        return wanted_games

    def get_wanted_game_tree(
        self, settings: Settings, campaigns: list[DropsCampaign]
    ) -> list[dict]:
        return [
            {**game, "game_obj": None} for game in self._get_wanted_game_tree(settings, campaigns)
        ]

    def get_wanted_games(self, settings: Settings, campaigns: list[DropsCampaign]) -> list[Game]:
        return [game["game_obj"] for game in self._get_wanted_game_tree(settings, campaigns)]
