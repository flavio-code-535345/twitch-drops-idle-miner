"""Model Context Protocol endpoint that lets an MCP client (Claude Code) read and control the miner.

Fork feature. Implements the MCP "Streamable HTTP" transport in JSON response mode: the
client POSTs one JSON-RPC message (or a batch) to ``/mcp`` and receives ``application/json``
replies; the server never opens a stream of its own. No SDK dependency is needed for that.

Access needs a bearer token generated in Settings. Only its SHA-256 digest is stored, failed
attempts are rate-limited per client address, and without a token the endpoint is off. Every
tool goes through the same managers as the dashboard's own API, so behaviour and validation
match the buttons in the web UI.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import logging
import os
import re
import secrets
import tempfile
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from src.config.settings import MiningHoursSettings
from src.version import UPSTREAM_VERSION, __version__


if TYPE_CHECKING:
    from src.core.client import Twitch
    from src.web.gui_manager import WebGUIManager


logger = logging.getLogger("TwitchDrops")

# Newest first; an unknown requested version is answered with the newest one.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "twitch-drops-idle-miner"
INSTRUCTIONS = (
    "Tools for a running Twitch Drops Miner. Start with get_status. list_campaigns shows what "
    "Twitch offers (filter by game or status); get_wanted_queue shows what the miner will "
    "actually mine and in which order. Control tools change the same settings as the "
    "dashboard. Campaigns marked linked=false need the game account linked on Twitch before "
    "rewards are delivered."
)


class ToolError(Exception):
    """A tool failed in a way the caller should see (returned with isError: true)."""


class MCPAccess:
    """The single MCP bearer token: stored as a digest, verified in constant time."""

    WINDOW_SECONDS = 60
    MAX_FAILURES_PER_PEER = 5
    MAX_FAILURES = 30
    TOKEN_PREFIX = "tdm_mcp_"

    def __init__(self, path: Path):
        self.path = path
        self.token_hash = ""
        self.created_at: str | None = None
        self.failures: deque[tuple[float, str]] = deque()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            token_hash = data["token_hash"]
            created_at = data.get("created_at")
            if (
                data.get("version") != 1
                or not isinstance(token_hash, str)
                or (token_hash and not re.fullmatch(r"[0-9a-f]{64}", token_hash))
                or not (created_at is None or isinstance(created_at, str))
            ):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            # Fail closed: a damaged file leaves MCP access off until a new token is made.
            logger.warning("Ignoring an invalid MCP access file; MCP access stays off.")
            return
        self.token_hash, self.created_at = token_hash, created_at

    @property
    def enabled(self) -> bool:
        return bool(self.token_hash)

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "created_at": self.created_at if self.enabled else None}

    def _save(self, token_hash: str, created_at: str | None) -> None:
        """Commit atomically before the running policy changes."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".mcp-access-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "token_hash": token_hash, "created_at": created_at}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
        self.token_hash, self.created_at = token_hash, created_at

    def generate(self) -> str:
        """Create a new token, replacing (and so revoking) any previous one."""
        token = self.TOKEN_PREFIX + secrets.token_urlsafe(32)
        self._save(self.digest(token), datetime.now(timezone.utc).isoformat(timespec="seconds"))
        return token

    def revoke(self) -> None:
        self._save("", None)

    def _prune(self, now: float) -> None:
        while self.failures and self.failures[0][0] <= now - self.WINDOW_SECONDS:
            self.failures.popleft()

    def limited(self, peer: str) -> bool:
        now = time.monotonic()
        self._prune(now)
        return (
            len(self.failures) >= self.MAX_FAILURES
            or sum(address == peer for _, address in self.failures) >= self.MAX_FAILURES_PER_PEER
        )

    def verify(self, authorization: str, peer: str) -> bool:
        token = authorization[7:].strip() if authorization[:7].lower() == "bearer " else ""
        valid = (
            self.enabled
            and 0 < len(token) <= 512
            and hmac.compare_digest(self.digest(token), self.token_hash)
        )
        if not valid:
            self.failures.append((time.monotonic(), peer))
        return valid


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[Any] | Any]
    read_only: bool

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "title": self.title,
                "readOnlyHint": self.read_only,
                "destructiveHint": False,
                "openWorldHint": False,
            },
        }


def _schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def _bool_arg(arguments: dict[str, Any], name: str) -> bool | None:
    value = arguments.get(name)
    if value is not None and not isinstance(value, bool):
        raise ToolError(f"'{name}' must be true or false")
    return value


def _str_arg(arguments: dict[str, Any], name: str, *, required: bool = False) -> str | None:
    value = arguments.get(name)
    if value is None:
        if required:
            raise ToolError(f"'{name}' is required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"'{name}' must be a non-empty string")
    return value.strip()


def _int_arg(arguments: dict[str, Any], name: str, default: int, lowest: int, highest: int) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"'{name}' must be an integer")
    return max(lowest, min(highest, value))


class MCPServer:
    """JSON-RPC handling for /mcp plus the dashboard's token management API."""

    MAX_BATCH = 32

    def __init__(
        self,
        access: MCPAccess,
        get_gui: Callable[[], WebGUIManager | None],
        get_client: Callable[[], Twitch | None],
    ):
        self.access, self.get_gui, self.get_client = access, get_gui, get_client
        self.tools = {tool.name: tool for tool in self._build_tools()}
        self.router = APIRouter()
        self.router.add_api_route("/mcp", self.endpoint, methods=["POST"])
        self.router.add_api_route("/mcp", self.no_stream, methods=["GET", "DELETE"])
        self.router.add_api_route("/api/mcp", self.dashboard_status, methods=["GET"])
        self.router.add_api_route("/api/mcp/token", self.dashboard_generate, methods=["POST"])
        self.router.add_api_route("/api/mcp/token", self.dashboard_revoke, methods=["DELETE"])

    # ---------------------------------------------------------------- dashboard API

    async def dashboard_status(self):
        return self.access.status()

    async def dashboard_generate(self):
        try:
            token = self.access.generate()
        except OSError:
            raise HTTPException(500, "mcp_save_failed") from None
        logger.info("MCP access token generated; any previous token no longer works.")
        return {**self.access.status(), "token": token}

    async def dashboard_revoke(self):
        try:
            self.access.revoke()
        except OSError:
            raise HTTPException(500, "mcp_save_failed") from None
        logger.info("MCP access turned off.")
        return self.access.status()

    # ---------------------------------------------------------------- transport

    async def no_stream(self):
        # This server answers every POST directly and offers no standalone SSE stream.
        return Response(status_code=405, headers={"Allow": "POST"})

    async def endpoint(self, request: Request):
        peer = request.client.host if request.client else "unknown"
        if self.access.limited(peer):
            return JSONResponse({"detail": "rate_limited"}, status_code=429, headers={"Retry-After": "60"})
        if not self.access.verify(request.headers.get("authorization", ""), peer):
            return JSONResponse(
                {"detail": "mcp_unauthorized" if self.access.enabled else "mcp_disabled"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="twitch-drops-miner"'},
            )
        try:
            message = json.loads(await request.body())
        except (ValueError, UnicodeError, RecursionError):
            return JSONResponse(self._error(None, -32700, "Parse error"), status_code=400)

        if isinstance(message, list):
            if not message or len(message) > self.MAX_BATCH:
                return JSONResponse(self._error(None, -32600, "Invalid batch"), status_code=400)
            replies = [reply for item in message if (reply := await self.handle(item)) is not None]
            return JSONResponse(replies) if replies else Response(status_code=202)
        reply = await self.handle(message)
        return JSONResponse(reply) if reply is not None else Response(status_code=202)

    @staticmethod
    def _error(message_id: Any, code: int, text: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": text}}

    async def handle(self, message: Any) -> dict[str, Any] | None:
        """Answer one JSON-RPC message; notifications and client responses get no reply."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._error(None, -32600, "Invalid request")
        method = message.get("method")
        if method is None and ("result" in message or "error" in message):
            return None  # A response to a server request; this server never sends any.
        notification = "id" not in message
        message_id = message.get("id")
        if not isinstance(method, str):
            return None if notification else self._error(message_id, -32600, "Invalid request")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return None if notification else self._error(message_id, -32602, "Invalid params")
        if notification or method.startswith("notifications/"):
            return None

        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            result: dict[str, Any] = {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "title": "Twitch Drops Miner", "version": __version__},
                "instructions": INSTRUCTIONS,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [tool.describe() for tool in self.tools.values()]}
        elif method == "tools/call":
            name, arguments = params.get("name"), params.get("arguments") or {}
            tool = self.tools.get(name) if isinstance(name, str) else None
            if tool is None:
                return self._error(message_id, -32602, f"Unknown tool: {name}")
            if not isinstance(arguments, dict):
                return self._error(message_id, -32602, "Tool arguments must be an object")
            result = await self._call(tool, arguments)
        else:
            return self._error(message_id, -32601, f"Method not found: {method}")
        return {"jsonrpc": "2.0", "id": message_id, "result": result}

    async def _call(self, tool: Tool, arguments: dict[str, Any]) -> dict[str, Any]:
        unknown = set(arguments) - set(tool.input_schema.get("properties", {}))
        try:
            if unknown:
                raise ToolError("Unknown argument(s): " + ", ".join(sorted(unknown)))
            data = tool.handler(arguments)
            if inspect.isawaitable(data):
                data = await data
        except ToolError as error:
            return {"content": [{"type": "text", "text": str(error)}], "isError": True}
        except Exception:
            logger.exception(f"MCP tool {tool.name} failed")
            return {"content": [{"type": "text", "text": f"{tool.name} failed; see the miner's log."}],
                    "isError": True}
        text = json.dumps(data, ensure_ascii=False, indent=1, default=str)
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ---------------------------------------------------------------- helpers

    def _require(self) -> tuple[WebGUIManager, Twitch]:
        gui, client = self.get_gui(), self.get_client()
        if gui is None or client is None:
            raise ToolError("The miner is still starting; try again in a moment.")
        return gui, client

    def _update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        gui, _client = self._require()
        try:
            return gui.settings.update_settings(changes)
        except ValueError as error:
            raise ToolError(f"Invalid settings: {error}") from None

    def _resolve_game(self, name: str) -> str:
        """Match a game name to Twitch's spelling when the miner already knows the game."""
        gui, client = self._require()
        lowered = name.lower()
        known = list(gui.settings.get_settings().get("games_available") or [])
        known += [campaign.game.name for campaign in client.inventory]
        return next((game for game in known if isinstance(game, str) and game.lower() == lowered), name)

    @staticmethod
    def _campaign_status(campaign: dict[str, Any]) -> str:
        return "active" if campaign.get("active") else "upcoming" if campaign.get("upcoming") else "expired"

    # ---------------------------------------------------------------- read tools

    def get_status(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        gui, client = self._require()
        watching = next((channel for channel in gui.channels.get_channels() if channel.get("watching")), None)
        settings = client.settings
        login = gui.login.get_status()
        return {
            "status": gui.status.get(),
            "logged_in": login.get("user_id") is not None,
            "login_status": login.get("status"),
            "mining_window_open": client.is_mining_time(),
            "manual_mode": client.get_manual_mode_info(),
            "watching_channel": watching and {
                key: watching.get(key) for key in ("name", "game", "viewers", "url", "acl_based")
            },
            "current_drop": gui.progress.get_current_drop(),
            "games_to_watch": list(settings.games_to_watch),
            "farm_mode": bool(getattr(settings, "farm_mode", False)),
            "priority_list_only": bool(getattr(settings, "priority_list_only", True)),
            "allow_unlinked_campaigns": bool(getattr(settings, "allow_unlinked_campaigns", False)),
            "mining_hours": getattr(settings, "mining_hours", None),
            "version": {"fork": __version__, "upstream": UPSTREAM_VERSION},
        }

    def list_campaigns(self, arguments: dict[str, Any]) -> dict[str, Any]:
        gui, _client = self._require()
        game = _str_arg(arguments, "game")
        status = arguments.get("status", "active")
        if status not in ("active", "upcoming", "expired", "all"):
            raise ToolError("'status' must be active, upcoming, expired or all")
        linked = _bool_arg(arguments, "linked")
        include_finished = _bool_arg(arguments, "include_finished") or False
        include_drops = _bool_arg(arguments, "include_drops")
        limit = _int_arg(arguments, "limit", 50, 1, 500)

        matches = []
        for campaign in gui.inv.get_campaigns():
            if status != "all" and self._campaign_status(campaign) != status:
                continue
            if game and game.lower() not in str(campaign.get("game_name", "")).lower():
                continue
            if linked is not None and bool(campaign.get("linked")) != linked:
                continue
            if not include_finished and campaign.get("finished"):
                continue
            matches.append(campaign)
        matches.sort(key=lambda campaign: campaign.get("ends_at") or "")

        def summary(campaign: dict[str, Any]) -> dict[str, Any]:
            data = {
                "id": campaign.get("id"),
                "name": campaign.get("name"),
                "game": campaign.get("game_name"),
                "status": self._campaign_status(campaign),
                "starts_at": campaign.get("starts_at"),
                "ends_at": campaign.get("ends_at"),
                "linked": campaign.get("linked"),
                "link_url": campaign.get("link_url"),
                "campaign_url": campaign.get("campaign_url"),
                "claimed_drops": campaign.get("claimed_drops"),
                "total_drops": campaign.get("total_drops"),
                "mining_finished": campaign.get("mining_finished"),
            }
            if include_drops is not False:
                data["drops"] = [
                    {
                        "name": drop.get("name"),
                        "current_minutes": drop.get("current_minutes"),
                        "required_minutes": drop.get("required_minutes"),
                        "is_claimed": drop.get("is_claimed"),
                        "can_claim": drop.get("can_claim"),
                        "is_mineable": drop.get("is_mineable"),
                        "ignored_reason": drop.get("ignored_reason"),
                        "rewards": [
                            f"{benefit.get('name')} ({benefit.get('type')})"
                            for benefit in drop.get("benefits", [])
                        ],
                    }
                    for drop in campaign.get("drops", [])
                ]
            return data

        return {"total_matching": len(matches), "campaigns": [summary(c) for c in matches[:limit]]}

    def get_wanted_queue(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        gui, _client = self._require()
        queue = []
        for position, game in enumerate(gui.get_wanted_game_tree(), start=1):
            queue.append({
                "position": position,
                "game": game.get("game_name"),
                "farm_mode": bool(game.get("farm_mode")),
                "campaigns": [
                    {
                        "name": campaign.get("name"),
                        "drops": [
                            {"name": drop.get("name"), "rewards": list(drop.get("benefits", []))}
                            for drop in campaign.get("drops", [])
                        ],
                    }
                    for campaign in game.get("campaigns", [])
                ],
            })
        return {"queue": queue}

    def list_channels(self, arguments: dict[str, Any]) -> dict[str, Any]:
        gui, _client = self._require()
        online_only = _bool_arg(arguments, "online_only") or False
        game = _str_arg(arguments, "game")
        channels = [
            {key: channel.get(key) for key in
             ("id", "name", "game", "online", "viewers", "drops_enabled", "acl_based", "watching", "url")}
            for channel in gui.channels.get_channels()
            if (channel.get("online") or not online_only)
            and (not game or game.lower() in str(channel.get("game") or "").lower())
        ]
        return {"channels": channels}

    def get_settings(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        gui, _client = self._require()
        settings = dict(gui.settings.get_settings())
        available = settings.pop("games_available", None) or []
        for secret in ("telegram_bot_token", "discord_webhook_url"):
            settings.pop(secret, None)
        settings["proxy_configured"] = bool(settings.pop("proxy", ""))
        settings["games_available_count"] = len(available)
        return settings

    def get_campaign_search_log(self, arguments: dict[str, Any]) -> dict[str, Any]:
        gui, _client = self._require()
        limit = _int_arg(arguments, "limit", 20, 1, 100)
        log = dict(gui.get_refresh_log())
        log["entries"] = list(log.get("entries", []))[:limit]
        return log

    def get_drop_history(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _gui, client = self._require()
        game = _str_arg(arguments, "game")
        limit = _int_arg(arguments, "limit", 50, 1, 500)
        since = None
        if (raw_since := _str_arg(arguments, "since")) is not None:
            try:
                since = datetime.fromisoformat(raw_since)
            except ValueError:
                raise ToolError("'since' must be an ISO date such as 2026-10-01") from None
            since = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since.astimezone(timezone.utc)
        return {
            "total": client.drop_history.total_count,
            "entries": client.drop_history.get_entries(game=game, since=since, limit=limit),
        }

    def get_recent_messages(self, arguments: dict[str, Any]) -> dict[str, Any]:
        gui, _client = self._require()
        limit = _int_arg(arguments, "limit", 50, 1, 500)
        return {"lines": list(gui.output.get_history())[-limit:]}

    # ---------------------------------------------------------------- control tools

    def set_games_to_watch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        games = arguments.get("games")
        if not isinstance(games, list) or not all(isinstance(game, str) and game.strip() for game in games):
            raise ToolError("'games' must be a list of game names")
        resolved: list[str] = []
        for game in games:
            name = self._resolve_game(game.strip())
            if name.lower() not in {existing.lower() for existing in resolved}:
                resolved.append(name)
        updated = self._update_settings({"games_to_watch": resolved})
        return {"games_to_watch": updated.get("games_to_watch", resolved)}

    def add_game_to_watch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _gui, client = self._require()
        game = self._resolve_game(_str_arg(arguments, "game", required=True) or "")
        current = [name for name in client.settings.games_to_watch if name.lower() != game.lower()]
        already = len(current) != len(client.settings.games_to_watch)
        position = arguments.get("position")
        if position is None:
            index = len(current) if not already else next(
                i for i, name in enumerate(client.settings.games_to_watch) if name.lower() == game.lower()
            )
        else:
            if isinstance(position, bool) or not isinstance(position, int) or position < 1:
                raise ToolError("'position' must be a whole number starting at 1")
            index = min(position - 1, len(current))
        current.insert(index, game)
        updated = self._update_settings({"games_to_watch": current})
        has_campaigns = any(c.game.name.lower() == game.lower() for c in client.inventory)
        return {
            "games_to_watch": updated.get("games_to_watch", current),
            "note": None if has_campaigns else
            f"No current campaign for '{game}' is known yet; it will be mined once Twitch offers one.",
        }

    def remove_game_to_watch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _gui, client = self._require()
        game = _str_arg(arguments, "game", required=True) or ""
        remaining = [name for name in client.settings.games_to_watch if name.lower() != game.lower()]
        if len(remaining) == len(client.settings.games_to_watch):
            raise ToolError(f"'{game}' is not in Games to Watch")
        updated = self._update_settings({"games_to_watch": remaining})
        return {"games_to_watch": updated.get("games_to_watch", remaining)}

    def update_mining_settings(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _gui, client = self._require()
        changes: dict[str, Any] = {}
        for name in ("farm_mode", "priority_list_only", "allow_unlinked_campaigns"):
            if (value := _bool_arg(arguments, name)) is not None:
                changes[name] = value
        if (benefits := arguments.get("mining_benefits")) is not None:
            allowed = {"BADGE", "EMOTE", "DIRECT_ENTITLEMENT", "UNKNOWN"}
            if (
                not isinstance(benefits, dict) or not benefits
                or not set(benefits) <= allowed
                or not all(isinstance(value, bool) for value in benefits.values())
            ):
                raise ToolError("'mining_benefits' maps BADGE, EMOTE, DIRECT_ENTITLEMENT or UNKNOWN to true/false")
            changes["mining_benefits"] = {**client.settings.mining_benefits, **benefits}
        if (hours := arguments.get("mining_hours")) is not None:
            try:
                changes["mining_hours"] = MiningHoursSettings.normalize(hours)
            except ValueError:
                raise ToolError(
                    "'mining_hours' is {mode: always|range, start: HH:MM, end: HH:MM}"
                ) from None
        if not changes:
            raise ToolError("Nothing to change; pass at least one setting")
        updated = self._update_settings(changes)
        return {name: updated.get(name) for name in changes}

    def reload_campaigns(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _gui, client = self._require()
        clear_cache = _bool_arg(arguments, "clear_cache") or False
        accepted = client.request_inventory_refresh(
            clear_cache=clear_cache, trigger="cache_clear" if clear_cache else "manual"
        )
        if not accepted:
            raise ToolError("The miner is shutting down")
        return {"requested": True, "clear_cache": clear_cache,
                "note": "Watch get_campaign_search_log for the result."}

    def select_channel(self, arguments: dict[str, Any]) -> dict[str, Any]:
        gui, client = self._require()
        wanted = (_str_arg(arguments, "channel", required=True) or "").lower()
        channel = next(
            (c for c in client.channels.values() if c.name.lower() == wanted or str(c.id) == wanted),
            None,
        )
        if channel is None:
            raise ToolError(f"'{wanted}' is not a tracked channel; see list_channels")
        if not channel.game:
            raise ToolError(f"{channel.name} is not playing any game")
        if not any(campaign.can_earn(channel) for campaign in client.inventory):
            raise ToolError(f"{channel.name} has no drops this account can earn right now")
        from src.config import State

        gui.select_channel(channel.id)
        client.change_state(State.CHANNEL_SWITCH)
        return {"selected": channel.name, "manual_mode": True,
                "note": "Use exit_manual_mode to return to automatic selection."}

    def exit_manual_mode(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        _gui, client = self._require()
        if not client.is_manual_mode():
            return {"manual_mode": False, "note": "Already in automatic mode"}
        client.exit_manual_mode("MCP request")
        return {"manual_mode": False}

    def _claim_candidates(self, client: Twitch):
        """Unclaimed drops of campaigns that have started: (campaign, drop) pairs."""
        for campaign in client.inventory:
            if campaign.upcoming:
                continue
            for drop in campaign.drops:
                if not drop.is_claimed:
                    yield campaign, drop

    @staticmethod
    def _claim_state(campaign: Any, drop: Any) -> dict[str, Any]:
        required = getattr(drop, "required_minutes", None)
        current = getattr(drop, "real_current_minutes", None)
        return {
            "campaign": campaign.name,
            "game": campaign.game.name,
            "drop": drop.name,
            "minutes": current,
            "required_minutes": required,
            "minutes_complete": current is not None and required is not None and current >= required,
            "has_claim_id": drop.claim_id is not None,
            "can_claim": drop.can_claim,
            "campaign_ends_at": campaign.ends_at.isoformat(),
            "last_claim_error": getattr(drop, "last_claim_error", None),
        }

    def diagnose_claims(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _gui, client = self._require()
        everything = _bool_arg(arguments, "include_in_progress") or False
        rows = [self._claim_state(campaign, drop) for campaign, drop in self._claim_candidates(client)]
        if not everything:
            rows = [row for row in rows if row["minutes_complete"] or row["has_claim_id"]]
        stuck = [row for row in rows if row["minutes_complete"] and not row["can_claim"]]
        from src.auth.integrity_proof import summarize

        getter = getattr(client, "session_proof", None)
        proof = getter() if callable(getter) else {"available": False}
        return {
            "unclaimed_drops": rows,
            "complete_but_not_claimable": len(stuck),
            "session_proof": proof,
            "proof_summary": summarize(proof),
            "note": "complete_but_not_claimable drops have all their minutes but no claim ID: "
                    "claim_drops with generate_missing tries to claim them anyway. session_proof is what the "
                    "session's integrity proof claims (bot flag, expiry, and whether it was issued for the device, "
                    "account and client the requests use); Twitch refuses claims with 'failed integrity check' "
                    "when it does not accept that proof.",
        }

    async def claim_drops(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _gui, client = self._require()
        wanted = _str_arg(arguments, "drop")
        generate_missing = _bool_arg(arguments, "generate_missing") or False
        results = []
        for campaign, drop in list(self._claim_candidates(client)):
            if wanted and wanted.lower() not in drop.name.lower():
                continue
            if drop.claim_id is None:
                complete = drop.real_current_minutes >= drop.required_minutes
                if not (generate_missing and complete):
                    continue
                await drop.generate_claim()
            if not drop.can_claim:
                continue
            claimed = await drop.claim()
            results.append({**self._claim_state(campaign, drop), "claimed": claimed})
        return {"attempted": len(results), "claimed": sum(1 for r in results if r["claimed"]), "results": results}

    def set_paused(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _gui, client = self._require()
        paused = _bool_arg(arguments, "paused")
        if paused is None:
            raise ToolError("'paused' is required")
        duration = arguments.get("duration")
        if duration is not None and duration not in ("10m", "1h", "tomorrow"):
            raise ToolError("'duration' must be 10m, 1h or tomorrow")
        client.set_paused(paused, duration if paused else None)
        return client.pause_info()

    # ---------------------------------------------------------------- registry

    def _build_tools(self) -> list[Tool]:
        game_prop = {"type": "string", "description": "Case-insensitive part of the game name"}
        return [
            Tool("get_status", "Miner status",
                 "Current state: what is being watched, the drop in progress with its minutes, "
                 "login, mining-hours window and the main mining settings.",
                 _schema(), self.get_status, True),
            Tool("list_campaigns", "List campaigns",
                 "Campaigns Twitch currently offers this account, with per-drop progress, sorted by "
                 "end time. Use 'game' to find a specific game.",
                 _schema({
                     "game": game_prop,
                     "status": {"type": "string", "enum": ["active", "upcoming", "expired", "all"],
                                "description": "Default: active"},
                     "linked": {"type": "boolean", "description": "Only linked (true) or not linked (false) campaigns"},
                     "include_finished": {"type": "boolean", "description": "Include fully claimed campaigns"},
                     "include_drops": {"type": "boolean", "description": "Include each drop (default true)"},
                     "limit": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Default 50"},
                 }), self.list_campaigns, True),
            Tool("get_wanted_queue", "Wanted queue",
                 "What the miner will actually mine, in priority order (Games to Watch first, then "
                 "priority fallback and Farm Mode games).",
                 _schema(), self.get_wanted_queue, True),
            Tool("list_channels", "List channels",
                 "Channels the miner is tracking, with viewers, game and which one is being watched.",
                 _schema({"online_only": {"type": "boolean"}, "game": game_prop}), self.list_channels, True),
            Tool("get_settings", "Settings",
                 "Miner settings (secrets such as webhook URLs, bot tokens and the proxy address are omitted).",
                 _schema(), self.get_settings, True),
            Tool("get_campaign_search_log", "Campaign search log",
                 "Recent campaign searches (when, why, how long, what was found) and when the next "
                 "scheduled one is due.",
                 _schema({"limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
                 self.get_campaign_search_log, True),
            Tool("get_drop_history", "Drop history",
                 "Claimed drops, newest first.",
                 _schema({
                     "game": {"type": "string", "description": "Exact game name"},
                     "since": {"type": "string", "description": "ISO date, e.g. 2026-10-01"},
                     "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                 }), self.get_drop_history, True),
            Tool("get_recent_messages", "Recent log messages",
                 "The latest lines of the dashboard's message log.",
                 _schema({"limit": {"type": "integer", "minimum": 1, "maximum": 500}}),
                 self.get_recent_messages, True),
            Tool("diagnose_claims", "Diagnose unclaimed drops",
                 "Unclaimed drops whose minutes are complete or that have a claim ID, with whether they can be "
                 "claimed and why the last claim attempt failed, plus what the session's integrity proof claims "
                 "(no secrets). Use it to find out why drops stay unclaimed.",
                 _schema({"include_in_progress": {"type": "boolean", "description": "Also list drops still being mined"}}),
                 self.diagnose_claims, True),
            Tool("claim_drops", "Claim drops now",
                 "Try to claim ready drops now and report per drop whether it worked and why not. 'generate_missing' "
                 "also tries drops whose minutes are complete but that Twitch never gave a claim ID.",
                 _schema({"drop": {"type": "string", "description": "Case-insensitive part of a drop name"},
                          "generate_missing": {"type": "boolean"}}),
                 self.claim_drops, False),
            Tool("set_paused", "Pause or resume mining",
                 "Pause mining (optionally for 10m, 1h or until tomorrow) or resume it.",
                 _schema({"paused": {"type": "boolean"},
                          "duration": {"type": "string", "enum": ["10m", "1h", "tomorrow"]}}, ["paused"]),
                 self.set_paused, False),
            Tool("set_games_to_watch", "Set Games to Watch",
                 "Replace Games to Watch with this ordered list (first = highest priority).",
                 _schema({"games": {"type": "array", "items": {"type": "string"}}}, ["games"]),
                 self.set_games_to_watch, False),
            Tool("add_game_to_watch", "Add game to watch",
                 "Add a game to Games to Watch (at the end, or at 'position' starting from 1). "
                 "Adding an already listed game moves it when 'position' is given.",
                 _schema({"game": {"type": "string"},
                          "position": {"type": "integer", "minimum": 1}}, ["game"]),
                 self.add_game_to_watch, False),
            Tool("remove_game_to_watch", "Remove game to watch",
                 "Remove a game from Games to Watch.",
                 _schema({"game": {"type": "string"}}, ["game"]), self.remove_game_to_watch, False),
            Tool("update_mining_settings", "Update mining settings",
                 "Change Farm Mode, Priority list only, mining of campaigns reported as Not Linked, "
                 "wanted reward types or mining hours. Only the given settings change.",
                 _schema({
                     "farm_mode": {"type": "boolean"},
                     "priority_list_only": {"type": "boolean"},
                     "allow_unlinked_campaigns": {"type": "boolean"},
                     "mining_benefits": {
                         "type": "object",
                         "properties": {key: {"type": "boolean"} for key in
                                        ("BADGE", "EMOTE", "DIRECT_ENTITLEMENT", "UNKNOWN")},
                         "additionalProperties": False,
                     },
                     "mining_hours": {
                         "type": "object",
                         "properties": {"mode": {"type": "string", "enum": ["always", "range"]},
                                        "start": {"type": "string", "description": "HH:MM"},
                                        "end": {"type": "string", "description": "HH:MM"}},
                         "required": ["mode", "start", "end"],
                         "additionalProperties": False,
                     },
                 }), self.update_mining_settings, False),
            Tool("reload_campaigns", "Reload campaigns",
                 "Search Twitch for campaigns again now (optionally clearing cached miner state first).",
                 _schema({"clear_cache": {"type": "boolean"}}), self.reload_campaigns, False),
            Tool("select_channel", "Watch a channel",
                 "Switch to a tracked channel by name or ID (manual mode).",
                 _schema({"channel": {"type": "string"}}, ["channel"]), self.select_channel, False),
            Tool("exit_manual_mode", "Automatic channel selection",
                 "Leave manual mode and let the miner pick channels again.",
                 _schema(), self.exit_manual_mode, False),
        ]
