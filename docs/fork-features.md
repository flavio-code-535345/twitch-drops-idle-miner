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

Farm Mode is separate from upstream's **Allow mining campaigns reported as Not Linked**
setting: badge and emote campaigns already count as eligible without a linked account, so
that setting only changes which Games to Watch campaigns can be mined.

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
The browser-based sessions used since 2.0 normally receive the catalog, so the fallback
stays idle unless Twitch withholds the catalog again.

## Signing in again

A saved login keeps working across updates. The **Sign in to Twitch** screen only appears
when Twitch needs a new login, or after **Settings → Log out of Twitch** (use that to
switch accounts). Sign-in happens entirely in the dashboard: the image's own Chromium is
shown in the page, you sign in and complete any verification there, then select **Finish
sign in**. See [Sign in](authentication.md) for the full guide. Things to check on a
server like this fork's (Docker behind an HTTPS reverse proxy):

- **Reverse proxy:** the browser view streams over a WebSocket at `/api/session/vnc` on
  the dashboard's own address. A proxy that already carries the dashboard's live updates
  carries it too. If `PUBLIC_BASE_URL` is set, it must be the exact address you open.
- **Dashboard password:** the browser view is protected only by the dashboard password
  (the sign-in screen offers to set one first). Set one if the dashboard is reachable from
  the internet, or anyone who can open it could use the sign-in browser.
- **Time zone:** set `TZ` to the time zone of your internet connection; a mismatch can make
  Twitch reject the browser login.
- **Mounts:** `data` and `logs` must be on a Linux file system that honours permissions.
  The sign-in browser runs as a separate container user, and TDM refuses to open it if
  that user could read your data (for example on SMB/NTFS mounts that ignore `chmod`).
  Docker named volumes work on such hosts.

If Twitch rejects the embedded browser, open **Use desktop helper** on the sign-in screen
and follow upstream's [desktop helper fallback](authentication.md#desktop-helper-fallback).
This fork does not build the helper: the download links point at the upstream release
matching the **upstream** version in the dashboard footer, which is the helper that fits
this fork's unchanged login code. While helper access is open (up to ten minutes), the
first helper to reach the dashboard is accepted without the dashboard password, so start
it only right before running the helper.

## Update notifier

The dashboard footer shows this fork's version and the upstream version it is synced
with, and flags a newer release of either on GitHub.

## Inventory: Use Tracked Games

The Inventory game filter has a **Use Tracked Games** button that adds every game from
Games to Watch to the filter.

## Dashboard look

Since upstream 2.2 the dashboard uses upstream's refreshed layout (header with tabs and your
Twitch avatar, optional **Show thumbnail** in Now Watching), on top of this fork's colours,
accent bar and sign-in card. Fork-only parts such as the Campaign Searches view are styled
to fit that layout.

## Differences in repository setup

- The only GitHub workflow builds the Docker image and pushes it to Docker Hub on every
  push to `main`. Upstream's validation, release, contributor and wiki workflows are not
  used; run Ruff, Mypy and pytest locally instead.
