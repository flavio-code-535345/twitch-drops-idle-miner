from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.gql_client import GQLClient
from src.config import CHANNEL_CAMPAIGNS_QUERY, ClientType, GQLRawQuery


@pytest.mark.asyncio
async def test_raw_query_service_error_is_retried_instead_of_crashing():
    # Raw-query responses carry no extensions.operationName, which the retry log reads.
    responses = [
        {"errors": [{"message": "service error"}], "data": None},
        {"data": {"channel": {"viewerDropCampaigns": []}}},
    ]

    @asynccontextmanager
    async def request(*args, **kwargs):
        yield SimpleNamespace(json=AsyncMock(return_value=responses.pop(0)))

    auth_state = SimpleNamespace(
        validate=AsyncMock(return_value=SimpleNamespace(headers=MagicMock(return_value={})))
    )
    client = GQLClient(SimpleNamespace(request=request), auth_state, ClientType.SMARTBOX)

    with patch("src.api.gql_client.asyncio.sleep", AsyncMock()):
        result = await client.request(GQLRawQuery(CHANNEL_CAMPAIGNS_QUERY, {"id": "1"}))

    assert result == {"data": {"channel": {"viewerDropCampaigns": []}}}
