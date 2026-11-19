"""Fork Watch when idle: watch a listed Twitch category while no drop can be mined."""

import asyncio
import copy
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import aiohttp
import pytest

from src.config import MAX_INT, State
from src.config.settings import IdleWatchSettings, default_settings
from src.exceptions import ExitRequest, LoginException, MinerException
from src.i18n import _
from src.models.channel import Channel, Stream
from src.models.game import Game
from src.services.channel_service import idle_watch_rank
from src.services.watch_service import WatchService
from src.web.managers.settings import SettingsManager
from tests.test_session_lifecycle import miner


GTA = {"id": "100", "name": "Grand Theft Auto VI"}
CHATTING = {"id": "200", "name": "Just Chatting"}
MINECRAFT = {"id": "300", "name": "Minecraft"}
DROPS = {"id": "1", "name": "Test Game"}
SLUG = {"data": {"game": {"slug": "grand-theft-auto-vi"}}}


def _live(client, channel_id, game, *, viewers=100, drops=True):
    channel = Channel(client, id=channel_id, login=f"channel{channel_id}")
    channel._stream = Stream(channel, id=channel_id, game=game, viewers=viewers, title="Live")
    channel._stream.drops_enabled = drops
    return channel


def _idle_miner(tmp_path, monkeypatch, idle=("Grand Theft Auto VI",), *, mining=True):
    """A real miner whose Games to Watch campaign (Test Game) can be mined, if ``mining``."""
    client = miner(tmp_path, monkeypatch)
    client.settings.idle_watch_games = list(idle)
    game = Game(DROPS)
    client.wanted_games = [game] if mining else []
    client.inventory = [SimpleNamespace(
        game=game, allowed_channels=[], can_earn_within=lambda stamp: True,
        can_earn=lambda channel: channel.game == game,
        remaining_minutes=60, first_drop=None, mineable_watch_drops=[],
    )] if mining else []
    return client


# ------------------------------------------------------------------ settings


def test_settings_normalize_trims_deduplicates_and_bounds_the_list():
    names = [" Grand Theft Auto VI ", "grand theft auto vi", "", 42, "x" * 201,
             *(f"Game {n}" for n in range(20))]
    assert IdleWatchSettings.normalize(names) == ["Grand Theft Auto VI", *(f"Game {n}" for n in range(9))]
    assert IdleWatchSettings.normalize(None) == []
    assert default_settings["idle_watch_games"] == []


@pytest.mark.asyncio
async def test_settings_manager_normalizes_and_reloads_only_on_change():
    settings = SimpleNamespace(**copy.deepcopy(default_settings))
    settings.save = MagicMock()
    callback = MagicMock()
    manager = SettingsManager(AsyncMock(), settings, MagicMock(), on_change=callback)

    manager.update_settings({"idle_watch_games": [" Grand Theft Auto VI ", "grand theft auto vi"]})
    await asyncio.sleep(0)
    assert settings.idle_watch_games == ["Grand Theft Auto VI"]
    assert manager.get_settings()["idle_watch_games"] == ["Grand Theft Auto VI"]
    callback.assert_called_once_with()

    callback.reset_mock()
    manager.update_settings({"idle_watch_games": ["Grand Theft Auto VI"]})
    await asyncio.sleep(0)
    callback.assert_not_called()

    with pytest.raises(ValueError, match="invalid_idle_watch_games"):
        manager.update_settings({"idle_watch_games": "Grand Theft Auto VI"})
    assert settings.idle_watch_games == ["Grand Theft Auto VI"]


# ------------------------------------------------------------------ ranking


@pytest.mark.parametrize(("priority_list_only", "reward_priority"), [
    (True, []), (False, []), (True, ["BADGE", "EMOTE", "DIRECT_ENTITLEMENT", "UNKNOWN"]),
    (False, ["BADGE", "EMOTE", "DIRECT_ENTITLEMENT", "UNKNOWN"]),
])
def test_listed_categories_rank_after_every_drop_channel(
    tmp_path, monkeypatch, priority_list_only, reward_priority,
):
    client = _idle_miner(tmp_path, monkeypatch, idle=["Just Chatting", "Grand Theft Auto VI"])
    client.settings.games_to_watch = ["Test Game"]
    client.settings.priority_list_only = priority_list_only
    client.settings.reward_priority = reward_priority
    service = client._channel_service
    drop, gta, chatting, other = (
        _live(client, 1, DROPS, viewers=1), _live(client, 2, GTA), _live(client, 3, CHATTING),
        _live(client, 4, MINECRAFT),
    )
    offline = Channel(client, id=5, login="channel5")

    assert [idle_watch_rank(client, channel) for channel in (drop, gta, chatting, other, offline)] == [
        None, 1, 0, None, None]
    assert [service.get_priority(channel) for channel in (drop, chatting, gta, other, offline)] == [
        0, 1, 2, MAX_INT, MAX_INT]
    ordered = sorted([offline, other, gta, chatting, drop], key=service.get_mining_priority)
    assert ordered[:3] == [drop, chatting, gta]


def test_categories_match_by_name_or_by_the_entry_they_were_found_under(tmp_path, monkeypatch):
    client = _idle_miner(tmp_path, monkeypatch, idle=["GTA 6"])
    gta = _live(client, 2, GTA)
    assert idle_watch_rank(client, gta) is None
    client._channel_service.idle_game_names = {100: "gta 6"}
    assert idle_watch_rank(client, gta) == 0
    # Removing the entry takes effect at once, before the next discovery.
    client.settings.idle_watch_games = ["Just Chatting"]
    assert idle_watch_rank(client, gta) is None
    gta._banned = True
    client.settings.idle_watch_games = ["grand theft auto vi"]
    assert idle_watch_rank(client, gta) is None


def test_a_category_mined_for_drops_keeps_the_normal_rules(tmp_path, monkeypatch):
    client = _idle_miner(tmp_path, monkeypatch, idle=["Test Game"])
    drop = _live(client, 1, DROPS, drops=False)
    assert idle_watch_rank(client, drop) is None
    assert not client._watch_service.can_watch(drop)


def test_mock_or_missing_settings_leave_the_watch_rules_unchanged():
    twitch = MagicMock()
    twitch.wanted_games = []
    twitch.inventory = []
    channel = _live(twitch, 2, GTA)
    assert idle_watch_rank(twitch, channel) is None
    assert not WatchService(twitch).can_watch(channel)
    twitch.settings = SimpleNamespace()
    assert idle_watch_rank(twitch, channel) is None


# ------------------------------------------------------------------ discovery


@pytest.mark.asyncio
async def test_discovery_resolves_categories_and_prefers_drops_enabled_streams(tmp_path, monkeypatch):
    client = _idle_miner(tmp_path, monkeypatch, idle=["grand theft auto vi", "test game", "Brand New"])
    client.gql_request = AsyncMock(side_effect=[SLUG, {"data": {"game": None}}])
    service = client._channel_service
    gta = _live(client, 2, GTA, drops=False)
    calls = []

    async def streams(game, *, limit, drops_enabled):
        calls.append((game.slug, limit, drops_enabled))
        return [gta] if game.slug == "grand-theft-auto-vi" and not drops_enabled else []

    service.get_live_streams = streams
    assert await service.get_idle_channels() == {gta}
    assert service.idle_game_names == {100: "grand theft auto vi"}
    # Test Game is mined for drops, so it is not looked up again; without a redirect
    # the slug comes from the name.
    assert calls == [("grand-theft-auto-vi", 5, True), ("grand-theft-auto-vi", 5, False),
                     ("brand-new", 5, True), ("brand-new", 5, False)]
    assert client._watch_service.can_watch(gta) and client._watch_service.watches_idle(gta)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    MinerException("failed"), aiohttp.ClientError(), TimeoutError(), KeyError("data"),
])
async def test_a_failed_category_lookup_skips_only_that_category(tmp_path, monkeypatch, failure, caplog):
    client = _idle_miner(tmp_path, monkeypatch, idle=["Broken", "Grand Theft Auto VI"])
    client.gql_request = AsyncMock(side_effect=[failure, SLUG])
    gta = _live(client, 2, GTA)
    client._channel_service.get_live_streams = AsyncMock(return_value=[gta])
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        assert await client._channel_service.get_idle_channels() == {gta}
    assert "could not look up live channels in Broken" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ExitRequest(), LoginException("expired")])
async def test_exit_and_login_failures_are_not_swallowed(tmp_path, monkeypatch, failure):
    client = _idle_miner(tmp_path, monkeypatch)
    client.gql_request = AsyncMock(side_effect=failure)
    with pytest.raises(type(failure)):
        await client._channel_service.get_idle_channels()


@pytest.mark.asyncio
async def test_no_listed_categories_means_no_lookups(tmp_path, monkeypatch):
    client = _idle_miner(tmp_path, monkeypatch, idle=[])
    client.gql_request = AsyncMock(side_effect=AssertionError("no request expected"))
    assert await client._channel_service.get_idle_channels() == set()


# ------------------------------------------------------------------ watching


def test_drop_channels_always_win_and_idle_streams_do_not_flap(tmp_path, monkeypatch):
    client = _idle_miner(tmp_path, monkeypatch)
    watch = client._watch_service
    drop, gta, other_gta = _live(client, 1, DROPS), _live(client, 2, GTA), _live(client, 3, GTA, viewers=900)
    assert watch.can_watch(gta) and watch.watches_idle(gta)
    assert watch.can_watch(drop) and not watch.watches_idle(drop)

    client.watching_channel.set(gta)
    assert watch.should_switch(drop)
    assert not watch.should_switch(other_gta)
    client.watching_channel.set(drop)
    assert not watch.should_switch(gta)

    client.settings.idle_watch_games = []
    assert not watch.can_watch(gta)


def test_watching_an_idle_channel_says_so_and_clears_the_previous_drop(tmp_path, monkeypatch):
    client = _idle_miner(tmp_path, monkeypatch)
    client.print = MagicMock()
    gta, drop = _live(client, 2, GTA), _live(client, 1, DROPS)

    client._watch_service.watch(gta)
    expected = _.t["status"]["watching_idle"].format(channel=gta.name, game="Grand Theft Auto VI")
    client.print.assert_called_once_with(expected)
    client.gui.status.update.assert_called_with(expected)
    client.gui.clear_drop.assert_called_once_with()

    client.gui.clear_drop.reset_mock()
    client._watch_service.watch(drop)
    client.gui.clear_drop.assert_not_called()
    assert client._watch_service.watching_status(drop) == _.t["status"]["watching"].format(channel=drop.name)


# ------------------------------------------------------------------ state machine


async def _run(client):
    try:
        await asyncio.wait_for(client._run(), 1)
    finally:
        if client._watching_task is not None:
            client._watching_task.cancel()


def _running_miner(tmp_path, monkeypatch, *, mining):
    client = _idle_miner(tmp_path, monkeypatch, mining=mining)
    client._state = State.CHANNELS_CLEANUP
    client._inventory_loaded = True
    client._ensure_api_clients = MagicMock()
    client.request_inventory_refresh = MagicMock()
    client.get_auth = AsyncMock(return_value=SimpleNamespace(user_id=42))
    client.websocket.start = AsyncMock()
    client.websocket.remove_topics = MagicMock()
    client.websocket.add_topics = MagicMock()
    client._watch_service.watch_loop = AsyncMock()
    client.gui.channels.get_selection.return_value = None
    client._channel_service.filter_banned_channels = AsyncMock(
        side_effect=lambda channels, retained_channels: list(channels)
    )
    client.gql_request = AsyncMock(return_value=SLUG)
    client.print = MagicMock()
    original_watch = client.watch

    def finish_after_selection(channel, *, update_status=True):
        original_watch(channel, update_status=update_status)
        asyncio.get_running_loop().call_soon(client.close)

    client.watch = finish_after_selection
    return client


@pytest.mark.asyncio
async def test_with_nothing_to_mine_the_miner_watches_a_listed_category(tmp_path, monkeypatch):
    client = _running_miner(tmp_path, monkeypatch, mining=False)
    kept = _live(client, 2, GTA, viewers=10)
    unrelated = _live(client, 4, MINECRAFT)
    client.channels.update({2: kept, 4: unrelated})
    client._channel_service.get_live_streams = AsyncMock(
        return_value=[_live(client, 2, GTA, viewers=10), _live(client, 3, GTA, viewers=500)]
    )
    await _run(client)

    watched = client.watching_channel.get_with_default(None)
    assert watched.id == 3
    # The cleanup kept the listed category's channel and dropped the unrelated one.
    assert set(client.channels) == {2, 3} and client.channels[2] is kept
    client.print.assert_any_call(_.t["status"]["no_campaign"], collapse_key="status.no_campaign")
    client.print.assert_any_call(
        _.t["status"]["watching_idle"].format(channel=watched.name, game="Grand Theft Auto VI")
    )


@pytest.mark.asyncio
async def test_a_reload_while_watching_a_listed_category_does_not_log_the_waiting_line(
    tmp_path, monkeypatch
):
    client = _running_miner(tmp_path, monkeypatch, mining=False)
    watched = _live(client, 2, GTA)
    client.channels[2] = watched
    client.watching_channel.set(watched)
    client._channel_service.get_live_streams = AsyncMock(return_value=[_live(client, 2, GTA)])
    status = client._watch_service.watching_status(watched)
    task = asyncio.create_task(client._run())
    for _attempt in range(100):
        await asyncio.sleep(0)
        if call(status) in client.gui.status.update.call_args_list:
            break
    client.close()
    await asyncio.wait_for(task, 1)

    # The miner kept watching, and the log shows no "waiting for a campaign" line under it.
    client.gui.status.update.assert_any_call(status)
    assert client.watching_channel.get_with_default(None) is watched
    waiting = call(_.t["status"]["no_campaign"], collapse_key="status.no_campaign")
    assert waiting not in client.print.call_args_list


@pytest.mark.asyncio
async def test_a_mineable_drop_channel_is_watched_before_the_listed_category(tmp_path, monkeypatch):
    client = _running_miner(tmp_path, monkeypatch, mining=True)
    drop = _live(client, 1, DROPS, viewers=1)
    client.get_live_streams = AsyncMock(return_value=[drop])
    client._channel_service.get_live_streams = AsyncMock(return_value=[_live(client, 3, GTA, viewers=500)])
    await _run(client)

    assert client.watching_channel.get_with_default(None) is drop
    assert set(client.channels) == {1, 3}
    client.print.assert_any_call(_.t["status"]["watching"].format(channel=drop.name))


@pytest.mark.asyncio
async def test_without_listed_categories_an_empty_queue_still_goes_idle(tmp_path, monkeypatch):
    client = _running_miner(tmp_path, monkeypatch, mining=False)
    client.settings.idle_watch_games = []
    client.channels[4] = _live(client, 4, MINECRAFT)
    client._channel_service.get_live_streams = AsyncMock(side_effect=AssertionError("no discovery"))
    client.change_state = MagicMock(wraps=client.change_state)
    task = asyncio.create_task(client._run())
    for _attempt in range(50):
        await asyncio.sleep(0)
        if client._state is State.IDLE:
            break
    client.close()
    await asyncio.wait_for(task, 1)
    assert client.channels == {}
    client.change_state.assert_any_call(State.IDLE)
    client.gui.channels.clear.assert_called_once_with()
