"""Fork: the Inventory game filter matches game names regardless of capitalisation."""

import json
import subprocess

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


BASE_FILTERS = {
    "show_active": True,
    "link_status": "all",
    "show_upcoming": True,
    "show_expired": True,
    "show_finished": True,
    "show_benefit_item": True,
    "show_benefit_badge": True,
    "show_benefit_emote": True,
    "show_benefit_other": True,
}


def campaign(game):
    return {"active": True, "upcoming": False, "expired": False, "linked": True,
            "game_name": game, "total_drops": 1, "claimed_drops": 0,
            "drops": [{"benefits": [{"type": "DIRECT_ENTITLEMENT"}]}]}


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
def test_game_filter_ignores_capitalisation_but_not_other_games():
    source = extract_javascript_function(APP_JS.read_text(encoding="utf-8"), "campaignMatchesFilters")
    # "Use Tracked Games" copied the Games to Watch spelling, which differs from Twitch's.
    filters = {**BASE_FILTERS, "game_name_search": ["Escape From Tarkov", "War Thunder"]}
    cases = [
        (campaign("Escape from Tarkov"), True),
        (campaign("ESCAPE FROM TARKOV"), True),
        (campaign("Escape from Tarkov: Arena"), False),
        (campaign("Warframe"), False),
    ]
    script = f"""
{source}
const filters = {json.dumps(filters)};
const cases = {json.dumps([c for c, _ in cases])};
process.stdout.write(JSON.stringify(cases.map(c => campaignMatchesFilters(c, filters))));
"""
    completed = subprocess.run([NODE, "-"], check=True, capture_output=True, input=script, text=True)
    assert json.loads(completed.stdout) == [expected for _, expected in cases]
