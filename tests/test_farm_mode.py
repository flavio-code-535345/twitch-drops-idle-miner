import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.models.benefit import Benefit, BenefitType
from src.models.campaign import DropsCampaign
from src.models.drop import TimedDrop
from src.models.game import Game
from src.services.stream_selector import StreamSelector


def _campaign(game_id: int, game_name: str, benefit_type: BenefitType, drop_name: str = "Drop"):
    """Build a mocked single-drop, single-benefit campaign for a given game."""
    campaign = MagicMock(spec=DropsCampaign)
    campaign.id = f"{game_name}-campaign"
    campaign.name = f"{game_name} Campaign"
    campaign.campaign_url = f"http://example.test/{game_name}"
    campaign.game = Game({"id": game_id, "name": game_name, "boxArtURL": f"http://img/{game_name}"})
    campaign.can_earn_within.return_value = True

    drop = MagicMock(spec=TimedDrop)
    drop.name = drop_name
    drop.is_claimed = False
    drop.ends_at = datetime.max.replace(tzinfo=timezone.utc)
    drop.get_wanted_unclaimed_benefits = TimedDrop.get_wanted_unclaimed_benefits.__get__(
        drop, TimedDrop
    )
    benefit = MagicMock(spec=Benefit)
    benefit.name = f"{game_name} Benefit"
    benefit.type = benefit_type
    benefit.is_wanted = Benefit.is_wanted.__get__(benefit, Benefit)
    drop.benefits = [benefit]
    campaign.drops = [drop]

    return campaign


class TestFarmMode(unittest.TestCase):
    def setUp(self):
        self.settings = MagicMock()
        self.settings.games_to_watch = ["Tracked Game"]
        self.settings.mining_benefits = {
            "BADGE": True,
            "EMOTE": True,
            "DIRECT_ENTITLEMENT": True,
            "UNKNOWN": True,
        }
        self.selector = StreamSelector()

    def test_farm_mode_off_ignores_untracked_games(self):
        self.settings.farm_mode = False
        campaigns = [
            _campaign(1, "Tracked Game", BenefitType.BADGE),
            _campaign(2, "Untracked Badge Game", BenefitType.BADGE),
        ]

        wanted_games = self.selector.get_wanted_games(self.settings, campaigns)

        self.assertEqual([g.name for g in wanted_games], ["Tracked Game"])

    def test_farm_mode_adds_untracked_badge_and_emote_games_after_tracked_ones(self):
        self.settings.farm_mode = True
        campaigns = [
            _campaign(2, "Zebra Badge Game", BenefitType.BADGE),
            _campaign(1, "Tracked Game", BenefitType.BADGE),
            _campaign(3, "Aardvark Emote Game", BenefitType.EMOTE),
        ]

        wanted_games = self.selector.get_wanted_games(self.settings, campaigns)

        # Tracked game keeps first position regardless of farm-mode games' alphabetical order.
        self.assertEqual(
            [g.name for g in wanted_games],
            ["Tracked Game", "Aardvark Emote Game", "Zebra Badge Game"],
        )

    def test_farm_mode_skips_item_only_untracked_campaigns(self):
        self.settings.farm_mode = True
        campaigns = [
            _campaign(1, "Tracked Game", BenefitType.BADGE),
            _campaign(2, "Item Only Game", BenefitType.DIRECT_ENTITLEMENT),
        ]

        wanted_games = self.selector.get_wanted_games(self.settings, campaigns)

        self.assertEqual([g.name for g in wanted_games], ["Tracked Game"])

    def test_farm_mode_does_not_duplicate_tracked_game(self):
        self.settings.farm_mode = True
        campaigns = [_campaign(1, "Tracked Game", BenefitType.BADGE)]

        wanted_games = self.selector.get_wanted_games(self.settings, campaigns)

        self.assertEqual([g.name for g in wanted_games], ["Tracked Game"])

    def test_farm_mode_merges_same_game_with_inconsistent_campaign_casing(self):
        # Twitch's campaign payloads don't guarantee identical display-name casing for the
        # same category across separate campaigns; farm mode must still treat them as one game.
        self.settings.farm_mode = True
        campaigns = [
            _campaign(2, "Escape from Tarkov", BenefitType.BADGE, drop_name="Drop A"),
            _campaign(2, "ESCAPE FROM TARKOV", BenefitType.EMOTE, drop_name="Drop B"),
        ]

        tree = self.selector.get_wanted_game_tree(self.settings, campaigns)
        wanted_games = self.selector.get_wanted_games(self.settings, campaigns)

        self.assertEqual(len(tree), 1)
        self.assertEqual(len(tree[0]["campaigns"]), 2)
        self.assertEqual(len(wanted_games), 1)

    def test_farm_mode_tree_entries_are_tagged_for_the_ui(self):
        self.settings.farm_mode = True
        campaigns = [
            _campaign(1, "Tracked Game", BenefitType.BADGE),
            _campaign(2, "Untracked Badge Game", BenefitType.BADGE),
        ]

        tree = self.selector.get_wanted_game_tree(self.settings, campaigns)

        by_name = {game["game_name"]: game for game in tree}
        self.assertNotIn("farm_mode", by_name["Tracked Game"])
        self.assertTrue(by_name["Untracked Badge Game"]["farm_mode"])


if __name__ == "__main__":
    unittest.main()
