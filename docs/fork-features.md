# Fork features

Features of [flavio-code-535345/twitch-drops-idle-miner](https://github.com/flavio-code-535345/twitch-drops-idle-miner)
that are not part of upstream [rangermix/TwitchDropsMiner](https://github.com/rangermix/TwitchDropsMiner).
Everything else in these guides applies unchanged.

## Farm Mode

**Settings → Farm Mode → Farm all available badges & emotes** also mines badge and emote
drops from every other available campaign, beyond your **Games to Watch**. Games to Watch
always keep priority for channel slots; farmed games are appended after them and marked
**FARM** in the Wanted Drops Queue. Farm Mode ignores the Mining Benefits filter and only
considers badges and emotes, which Twitch awards without account linking.

Farm Mode relies on Twitch's full campaign list. While Twitch withholds that list (see
below), it cannot discover games outside Games to Watch.

## Campaign Searches log

The **Output** panel on the Main tab has a **Campaign Searches** view next to the message
log. It lists every campaign search since the miner started: when it ran, what triggered
it (startup, the schedule, a manual reload, a Games to Watch change, a drop notification,
and so on), how long it took, and what it found. The summary shows the last search and
when the next scheduled one is due.

The schedule follows **Settings → General → minimum refresh interval** (1 to 1440
minutes, default 30), so you should see a **Scheduled** entry at that interval. An overdue
schedule is highlighted. The log is kept in memory and resets when the miner restarts.

## Campaign discovery when Twitch withholds the catalog

If Twitch returns no campaign catalog, the miner searches up to 100 live drops-enabled
channels per Games to Watch entry for the campaigns they offer, then loads each
campaign's complete details (account linking, reward types, participating channels and
progress). Found campaigns are remembered until they end, so the miner can switch as soon
as a participating channel goes live, and adding a game starts a new search immediately.
A campaign can only be found while at least one of its channels is live within that
search.

This was added for the v1.3.1/v1.3.2 Smart TV login, which Twitch denied the catalog.
With the 2.0 helper login the catalog is normally available, so the fallback stays idle
unless Twitch withholds the catalog again.

## Update notifier

The dashboard footer shows this fork's version and the upstream version it is synced
with, and flags a newer release of either on GitHub.

## Inventory: Use Tracked Games

The Inventory game filter has a **Use Tracked Games** button that adds every game from
Games to Watch to the filter.

## Differences in repository setup

- The only GitHub workflow builds the Docker image and pushes it to Docker Hub on every
  push to `main`. Upstream's validation, release, native login helper build, contributor
  and wiki workflows are not used; run Ruff, Mypy and pytest locally instead.
- The native login helper is not built by this fork. Download it from the upstream
  release matching the synced upstream version.
