"""Fork: diagnosis data keeps the campaign-discovery raw query in its own bucket."""

import json

from src.config import CHANNEL_CAMPAIGNS_QUERY, GQLRawQuery
from src.diagnostics import Diagnostics


def test_channel_campaigns_raw_query_is_recorded_under_its_own_operation(tmp_path):
    diagnostics = Diagnostics(tmp_path)
    request = GQLRawQuery(CHANNEL_CAMPAIGNS_QUERY, {"id": "123456"})
    body = json.dumps({"data": {"channel": {"viewerDropCampaigns": []}}}).encode()

    diagnostics.record_http("POST", "https://gql.twitch.tv/gql", 200, body=body, operations=request)

    entry = diagnostics.apis["twitch_gql:ChannelDropCampaigns"]
    assert entry["operation"] == "ChannelDropCampaigns"
    assert entry["count"] == 1
    # The channel ID variable is never part of the recorded sample.
    assert "123456" not in json.dumps(entry)
