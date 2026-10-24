"""Fork: the MCP endpoint, its token management and its tools, through the production app."""

import asyncio
import importlib
import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.web.auth import WebAuth
from src.web.mcp_server import PROTOCOL_VERSIONS, MCPAccess


web = importlib.import_module("src.web.app")
DASHBOARD = {"X-TDM-Request": "1"}
PASSWORD = "test password only"


def rpc(method, params=None, message_id=1):
    message = {"jsonrpc": "2.0", "method": method}
    if message_id is not None:
        message["id"] = message_id
    if params is not None:
        message["params"] = params
    return message


def campaign(name, game, *, active=True, linked=True, finished=False, ends="2026-10-24T19:59:59+00:00"):
    return {
        "id": f"{name}-id", "name": name, "game_name": game, "active": active,
        "upcoming": not active, "expired": False, "linked": linked, "finished": finished,
        "starts_at": "2026-10-24T13:00:00+00:00", "ends_at": ends, "link_url": "https://link",
        "campaign_url": "https://campaign", "claimed_drops": 0, "total_drops": 1,
        "mining_finished": False,
        "drops": [{
            "name": "Common 1", "current_minutes": 5, "required_minutes": 60, "is_claimed": False,
            "can_claim": False, "is_mineable": True, "ignored_reason": None,
            "benefits": [{"name": "Common case", "type": "DIRECT_ENTITLEMENT", "image_url": "x"}],
        }],
    }


class FakeMiner:
    """The managers and client attributes the MCP tools use."""

    def __init__(self):
        self.games = ["Arena Breakout"]
        self.updates = []
        channel = SimpleNamespace(id=7, name="hyppoferrox", game=SimpleNamespace(name="Escape from Tarkov"))
        self.client = SimpleNamespace(
            settings=SimpleNamespace(
                games_to_watch=self.games, farm_mode=True, priority_list_only=True,
                allow_unlinked_campaigns=False, mining_hours={"mode": "always", "start": "08:00", "end": "22:00"},
                mining_benefits={"BADGE": True, "EMOTE": True, "DIRECT_ENTITLEMENT": True, "UNKNOWN": True},
            ),
            is_mining_time=lambda: True,
            get_manual_mode_info=lambda: {"active": False},
            inventory=[SimpleNamespace(game=SimpleNamespace(name="Escape from Tarkov"),
                                       can_earn=lambda channel: True)],
            channels={7: channel},
            request_inventory_refresh=MagicMock(return_value=True),
            change_state=MagicMock(),
            is_manual_mode=lambda: False,
            exit_manual_mode=MagicMock(),
            drop_history=SimpleNamespace(total_count=0, get_entries=lambda **kwargs: []),
        )

        def update_settings(changes):
            self.updates.append(changes)
            if "games_to_watch" in changes:
                self.games[:] = changes["games_to_watch"]
            return {"games_to_watch": list(self.games), **changes}

        self.gui = SimpleNamespace(
            status=SimpleNamespace(get=lambda: "Watching hyppoferrox"),
            login=SimpleNamespace(get_status=lambda: {"status": "Logged in", "user_id": 44041186}),
            channels=SimpleNamespace(get_channels=lambda: [
                {"id": 7, "name": "hyppoferrox", "game": "Escape from Tarkov", "online": True,
                 "viewers": 120, "drops_enabled": True, "acl_based": True, "watching": True, "url": "u"},
            ]),
            progress=SimpleNamespace(get_current_drop=lambda: {"drop_name": "Common 1", "progress": 0.1}),
            inv=SimpleNamespace(get_campaigns=lambda: [
                campaign("Souer GunGame", "Escape from Tarkov", linked=False),
                campaign("Arena Weekend", "Arena Breakout"),
                campaign("Old Tarkov", "Escape from Tarkov", finished=True),
                campaign("Next Week", "Escape from Tarkov", active=False),
            ]),
            get_wanted_game_tree=lambda: [{"game_name": "Arena Breakout", "campaigns": [
                {"name": "Arena Weekend", "drops": [{"name": "Badge", "benefits": ["Arena badge"]}]}]}],
            get_refresh_log=lambda: {"entries": [{"trigger": "scheduled"}] * 30, "interval_minutes": 30},
            output=SimpleNamespace(get_history=lambda: [f"line {n}" for n in range(100)]),
            settings=SimpleNamespace(
                get_settings=lambda: {"games_to_watch": list(self.games), "proxy": "http://user:pass@host",
                                      "telegram_bot_token": "masked", "discord_webhook_url": "masked",
                                      "games_available": ["Escape from Tarkov", "Arena Breakout"]},
                update_settings=update_settings,
            ),
            select_channel=MagicMock(),
        )


@pytest.fixture
def miner(monkeypatch):
    fake = FakeMiner()
    monkeypatch.setattr(web, "gui_manager", fake.gui)
    monkeypatch.setattr(web, "twitch_client", fake.client)
    return fake


@pytest.fixture
def browser(tmp_path, monkeypatch):
    auth = web.web_auth
    for name, value in {"path": tmp_path / "web_auth.json", "password_hash": "",
                        "sessions": {}, "lock": asyncio.Lock(), "attempts": deque()}.items():
        monkeypatch.setattr(auth, name, value)
    access = web.mcp_access
    for name, value in {"path": tmp_path / "mcp_access.json", "token_hash": "",
                        "created_at": None, "failures": deque()}.items():
        monkeypatch.setattr(access, name, value)
    with TestClient(web.socket_app) as client:
        yield client


def token_for(browser):
    response = browser.post("/api/mcp/token", headers=DASHBOARD)
    assert response.status_code == 200
    return response.json()["token"]


def call(browser, token, message):
    return browser.post("/mcp", json=message, headers={"Authorization": f"Bearer {token}"})


def tool(browser, token, name, arguments=None):
    response = call(browser, token, rpc("tools/call", {"name": name, "arguments": arguments or {}}))
    assert response.status_code == 200
    result = response.json()["result"]
    text = result["content"][0]["text"]
    return result["isError"], (text if result["isError"] else json.loads(text))


# ------------------------------------------------------------------ access


def test_mcp_is_off_until_a_token_is_generated(browser):
    response = browser.post("/mcp", json=rpc("initialize"))
    assert response.status_code == 401
    assert response.json() == {"detail": "mcp_disabled"}
    assert browser.get("/api/mcp").json() == {"enabled": False, "created_at": None}


def test_generated_token_unlocks_mcp_and_only_its_digest_is_stored(browser):
    token = token_for(browser)
    assert token.startswith("tdm_mcp_")
    stored = web.mcp_access.path.read_text(encoding="utf-8")
    assert token not in stored and MCPAccess.digest(token) in stored
    assert browser.get("/api/mcp").json()["enabled"] is True

    response = call(browser, token, rpc("initialize", {"protocolVersion": "2025-03-26"}))
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "twitch-drops-idle-miner"

    wrong = call(browser, "tdm_mcp_wrong", rpc("ping"))
    assert wrong.status_code == 401 and wrong.json() == {"detail": "mcp_unauthorized"}


def test_replacing_or_revoking_the_token_locks_out_the_old_one(browser):
    first = token_for(browser)
    second = token_for(browser)
    assert call(browser, first, rpc("ping")).status_code == 401
    assert call(browser, second, rpc("ping")).status_code == 200
    assert browser.delete("/api/mcp/token", headers=DASHBOARD).json() == {"enabled": False, "created_at": None}
    assert call(browser, second, rpc("ping")).status_code == 401


def test_mcp_uses_its_token_while_token_management_needs_the_dashboard_password(browser):
    web.web_auth.save(WebAuth.hash_password(PASSWORD), {})
    assert browser.post("/api/mcp/token", headers=DASHBOARD).status_code == 401
    browser.post("/api/auth/login", json={"password": PASSWORD}, headers=DASHBOARD)
    token = token_for(browser)
    browser.cookies.clear()
    # No dashboard cookie, no X-TDM-Request header: the bearer token alone is enough.
    assert call(browser, token, rpc("ping")).json() == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert browser.get("/api/mcp").status_code == 401


def test_dashboard_token_changes_need_the_csrf_header(browser):
    assert browser.post("/api/mcp/token").status_code == 403
    assert browser.delete("/api/mcp/token").status_code == 403


def test_foreign_browser_origins_are_rejected(browser):
    token = token_for(browser)
    response = browser.post("/mcp", json=rpc("ping"), headers={
        "Authorization": f"Bearer {token}", "Origin": "https://evil.example"})
    assert response.status_code == 403


def test_repeated_wrong_tokens_are_rate_limited(browser):
    token = token_for(browser)
    for _ in range(MCPAccess.MAX_FAILURES_PER_PEER):
        assert call(browser, "tdm_mcp_guess", rpc("ping")).status_code == 401
    limited = call(browser, token, rpc("ping"))
    assert limited.status_code == 429 and limited.headers["retry-after"] == "60"


def test_damaged_access_file_leaves_mcp_off(tmp_path):
    path = tmp_path / "mcp_access.json"
    path.write_text('{"version": 1, "token_hash": "not-a-digest"}', encoding="utf-8")
    assert MCPAccess(path).enabled is False


def test_streams_are_not_offered(browser):
    assert browser.get("/mcp").status_code == 405


# ------------------------------------------------------------------ protocol


def test_protocol_messages(browser):
    token = token_for(browser)
    unknown_version = call(browser, token, rpc("initialize", {"protocolVersion": "1999-01-01"}))
    assert unknown_version.json()["result"]["protocolVersion"] == PROTOCOL_VERSIONS[0]

    notification = call(browser, token, rpc("notifications/initialized", message_id=None))
    assert notification.status_code == 202 and notification.content == b""

    missing = call(browser, token, rpc("resources/list")).json()
    assert missing["error"]["code"] == -32601

    batch = call(browser, token, [rpc("ping", message_id=1), rpc("notifications/initialized", message_id=None),
                                  rpc("ping", message_id=2)]).json()
    assert [reply["id"] for reply in batch] == [1, 2]

    garbage = browser.post("/mcp", content=b"{not json",
                           headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    assert garbage.status_code == 400 and garbage.json()["error"]["code"] == -32700

    unknown_tool = call(browser, token, rpc("tools/call", {"name": "nope"})).json()
    assert unknown_tool["error"]["code"] == -32602


def test_tool_list_marks_read_only_tools(browser):
    token = token_for(browser)
    tools = {t["name"]: t for t in call(browser, token, rpc("tools/list")).json()["result"]["tools"]}
    assert len(tools) == 15
    assert tools["get_status"]["annotations"]["readOnlyHint"] is True
    assert tools["add_game_to_watch"]["annotations"]["readOnlyHint"] is False
    assert tools["add_game_to_watch"]["inputSchema"]["required"] == ["game"]


def test_tools_report_a_starting_miner_as_a_tool_error(browser, monkeypatch):
    token = token_for(browser)
    monkeypatch.setattr(web, "gui_manager", None)
    is_error, text = tool(browser, token, "get_status")
    assert is_error and "starting" in text


# ------------------------------------------------------------------ tools


def test_read_tools(browser, miner):
    token = token_for(browser)
    _, status = tool(browser, token, "get_status")
    assert status["watching_channel"]["name"] == "hyppoferrox"
    assert status["farm_mode"] is True and status["logged_in"] is True

    _, found = tool(browser, token, "list_campaigns", {"game": "tarkov"})
    assert [c["name"] for c in found["campaigns"]] == ["Souer GunGame"]
    assert found["campaigns"][0]["linked"] is False
    assert found["campaigns"][0]["drops"][0]["rewards"] == ["Common case (DIRECT_ENTITLEMENT)"]
    _, every = tool(browser, token, "list_campaigns",
                    {"game": "tarkov", "status": "all", "include_finished": True, "include_drops": False})
    assert {c["name"] for c in every["campaigns"]} == {"Souer GunGame", "Old Tarkov", "Next Week"}
    assert "drops" not in every["campaigns"][0]

    _, queue = tool(browser, token, "get_wanted_queue")
    assert queue["queue"][0]["campaigns"][0]["drops"][0]["rewards"] == ["Arena badge"]

    _, settings = tool(browser, token, "get_settings")
    assert "telegram_bot_token" not in settings and "discord_webhook_url" not in settings
    assert settings["proxy_configured"] is True and "proxy" not in settings
    assert settings["games_available_count"] == 2

    _, log = tool(browser, token, "get_campaign_search_log", {"limit": 5})
    assert len(log["entries"]) == 5
    _, messages = tool(browser, token, "get_recent_messages", {"limit": 3})
    assert messages["lines"] == ["line 97", "line 98", "line 99"]


def test_games_to_watch_tools_resolve_names_and_keep_order(browser, miner):
    token = token_for(browser)
    _, added = tool(browser, token, "add_game_to_watch", {"game": "escape from tarkov", "position": 1})
    assert added["games_to_watch"] == ["Escape from Tarkov", "Arena Breakout"]
    assert added["note"] is None

    _, unknown = tool(browser, token, "add_game_to_watch", {"game": "Brand New Game"})
    assert unknown["games_to_watch"][-1] == "Brand New Game" and "No current campaign" in unknown["note"]

    _, replaced = tool(browser, token, "set_games_to_watch",
                       {"games": ["arena breakout", "Arena Breakout", "escape from tarkov"]})
    assert replaced["games_to_watch"] == ["Arena Breakout", "Escape from Tarkov"]

    _, removed = tool(browser, token, "remove_game_to_watch", {"game": "ARENA BREAKOUT"})
    assert removed["games_to_watch"] == ["Escape from Tarkov"]
    is_error, text = tool(browser, token, "remove_game_to_watch", {"game": "Not There"})
    assert is_error and "not in Games to Watch" in text


def test_update_mining_settings_validates_and_merges(browser, miner):
    token = token_for(browser)
    is_error, text = tool(browser, token, "update_mining_settings")
    assert is_error and "Nothing to change" in text
    is_error, _ = tool(browser, token, "update_mining_settings", {"farm_mode": "yes"})
    assert is_error
    is_error, _ = tool(browser, token, "update_mining_settings", {"mining_hours": {"mode": "range"}})
    assert is_error
    is_error, text = tool(browser, token, "update_mining_settings", {"bogus": True})
    assert is_error and "Unknown argument" in text

    _, changed = tool(browser, token, "update_mining_settings",
                      {"priority_list_only": False, "mining_benefits": {"BADGE": False}})
    assert changed["priority_list_only"] is False
    assert miner.updates[-1]["mining_benefits"] == {
        "BADGE": False, "EMOTE": True, "DIRECT_ENTITLEMENT": True, "UNKNOWN": True}


def test_control_tools(browser, miner):
    token = token_for(browser)
    _, reload = tool(browser, token, "reload_campaigns")
    miner.client.request_inventory_refresh.assert_called_with(clear_cache=False, trigger="manual")
    assert reload["requested"] is True

    _, selected = tool(browser, token, "select_channel", {"channel": "HyppoFerrox"})
    assert selected["selected"] == "hyppoferrox"
    miner.gui.select_channel.assert_called_once_with(7)
    miner.client.change_state.assert_called_once()

    is_error, text = tool(browser, token, "select_channel", {"channel": "nobody"})
    assert is_error and "not a tracked channel" in text

    _, automatic = tool(browser, token, "exit_manual_mode")
    assert automatic["manual_mode"] is False
