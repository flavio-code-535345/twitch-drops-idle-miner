"""Fork: reading Twitch's client-integrity proof for diagnosis, without exposing it."""

import base64
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.auth.integrity_proof import proof_report, read_claims, summarize, summarize_source
from src.config import ClientType
from src.core.client import Twitch
from src.exceptions import GQLException
from tests.test_imported_session import bundle_data, imported
from tests.test_watch_drop_filtering import _campaign, _drop


NOW = datetime(2026, 10, 31, 21, 0, tzinfo=timezone.utc)
HEADERS = {"client-id": "web-client", "x-device-id": "device-1"}
CLAIMS = {
    "client_id": "web-client", "client_ip": "203.0.113.7", "device_id": "device-1",
    "exp": "2026-11-01T13:00:00Z", "iat": "2026-10-31T21:00:00Z", "is_bad_bot": "false",
    "iss": "Twitch Client Integrity", "nbf": "2026-10-31T21:00:00Z", "user_id": "42",
}


def token(claims, footer=""):
    # The signature bytes include "{" and "}", so the parser cannot rely on them being absent.
    raw = json.dumps(claims).encode() + bytes(range(100, 164))
    return "v4.public." + base64.urlsafe_b64encode(raw).rstrip(b"=").decode() + (f".{footer}" if footer else "")


def test_reads_the_claims_and_compares_them_with_the_requests():
    report = proof_report(token(CLAIMS), HEADERS, 42, NOW)
    assert report == {
        "readable": True, "bot_flag": False,
        "issued_at": "2026-10-31T21:00:00+00:00", "expires_at": "2026-11-01T13:00:00+00:00",
        "expired": False, "not_yet_valid": False,
        "device_matches": True, "account_matches": True, "client_matches": True,
        "fields": sorted(CLAIMS),
    }
    assert summarize({"available": True, **report}) == "not flagged as a bot and nothing else wrong visible"
    assert read_claims(token(CLAIMS, footer="eyJmb290ZXIiOjF9")) == CLAIMS


def test_names_each_problem_it_can_see():
    flagged = {**CLAIMS, "is_bad_bot": "true", "device_id": "other", "user_id": "7", "exp": "2026-10-31T20:00:00Z"}
    report = {"available": True, **proof_report(token(flagged), HEADERS, 42, NOW), "proxy_for_requests": True}
    assert report["bot_flag"] is True and report["expired"] is True
    assert report["device_matches"] is False and report["account_matches"] is False
    line = summarize(report)
    for words in ("flagged it as a bot", "expired", "different device", "different account", "proxy"):
        assert words in line

    unflagged = {key: value for key, value in CLAIMS.items() if key != "is_bad_bot"}
    report = {"available": True, **proof_report(token(unflagged), HEADERS, 42, NOW)}
    assert report["bot_flag"] is None
    assert summarize(report) == "no bot flag in the proof and nothing else wrong visible"


def test_a_report_never_contains_the_proof_or_the_values_it_claims():
    proof = token(CLAIMS)
    text = json.dumps(proof_report(proof, HEADERS, 42, NOW))
    for secret in ("203.0.113.7", "device-1", '"42"', proof[10:40]):
        assert secret not in text


def test_unreadable_proofs_and_missing_sessions():
    unsigned = "v4.public." + base64.urlsafe_b64encode(b"x" * 80).decode().rstrip("=")
    for value, shape in (("not-a-token", "unknown"), ("v4.public.!!!", "v4.public"), (None, "unknown"),
                         (unsigned, "v4.public"), ("v3.local.abc", "v3.local")):
        assert proof_report(value, HEADERS, 42, NOW) == {"readable": False, "format": shape}
    assert "could not be read" in summarize({"available": True, "readable": False, "format": "unknown"})
    assert summarize(None) == summarize({"available": False}) == "no session proof available"
    assert summarize_source(object()) == "no session proof available"
    broken = MagicMock()
    broken.session_proof.side_effect = RuntimeError("boom")
    assert summarize_source(broken) == "no session proof available"


@pytest.mark.asyncio
async def test_the_session_reports_its_current_proof(tmp_path):
    session = imported(tmp_path, [1000])
    assert session.proof_report() == {"available": False}
    claims = {**CLAIMS, "device_id": "test-device", "client_id": ClientType.WEB.CLIENT_ID}
    await session.install(bundle_data(integrity=token(claims)))
    report = session.proof_report()
    assert report["available"] and report["readable"] and report["bot_flag"] is False
    assert report["device_matches"] and report["account_matches"] and report["client_matches"]


def test_the_client_adds_whether_requests_use_a_proxy():
    fake = SimpleNamespace(_browser=SimpleNamespace(proof_report=lambda: {"available": False}),
                           settings=SimpleNamespace(proxy="http://proxy.test:8080"))
    assert Twitch.session_proof(fake) == {"available": False, "proxy_for_requests": True}
    fake.settings.proxy = ""
    assert Twitch.session_proof(fake)["proxy_for_requests"] is False


@pytest.mark.asyncio
async def test_a_claim_refused_for_integrity_logs_the_proof_without_changing_the_result(caplog):
    drop = _campaign("c", [_drop("d5", "Drop 5", 300)]).timed_drops["d5"]
    drop.update_claim("instance-5")
    drop._twitch.gql_request = AsyncMock(side_effect=GQLException(
        [{"message": "failed integrity check", "path": ["claimDropRewards"]}]))
    drop._twitch.session_proof = MagicMock(return_value={"available": True, "readable": True, "bot_flag": True})
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        assert await drop._claim() is False
    assert "session proof: Twitch flagged it as a bot" in caplog.text
    assert drop.last_claim_error.startswith("request failed")
