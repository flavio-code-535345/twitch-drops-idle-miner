# Agent Instructions


## Repository Instructions

This file is the canonical harness for AI agents working in this repository.
`CLAUDE.md` and `GEMINI.md` are relative symbolic links to `AGENTS.md` so every agent
reads the same guidance. Maintain all shared and agent-specific instructions here.

## About This Fork

This repository (`flavio-code-535345/twitch-drops-idle-miner`) is a private, personal
fork of [rangermix/TwitchDropsMiner](https://github.com/rangermix/TwitchDropsMiner). It is
not published for public contribution, and the public-facing parts of the workflow below
(issue triage, external PR review, contributor credit automation) describe the upstream
project's process, not an active process here.

- User-facing fork documentation lives in `docs/fork-features.md` (linked from README's
  "About this fork"); keep upstream's `docs/` guides and README otherwise as upstream ships
  them so syncs stay conflict-light. Since upstream 2.1, Twitch sign-in runs inside the
  dashboard through the image's Chromium (`src/auth/container_login.py`). Upstream 2.1.1
  restored the desktop helper as an optional fallback; only upstream builds and publishes
  it, so `src/web/app.py` points the sign-in screen's helper downloads at
  `UPSTREAM_VERSION` rather than `MY_VERSION` (`tests/test_helper_download_version.py`).
  Upstream's release, validation, helper-build workflows and issue templates are not part
  of this fork.
- Fork fix in upstream code: `SessionAPI.viewer` also suppresses `WebSocketDisconnect` when
  closing the sign-in viewer, because Starlette raises it (not `OSError`) once the dashboard
  tab has gone, which otherwise logs an "Exception in ASGI application" traceback on every
  viewer disconnect (`tests/test_vnc_viewer_disconnect.py`). Keep it when syncing upstream.
- Developing on Windows: `tests/conftest.py` defaults subprocess pipes and `Path.read_text()`
  to UTF-8 and skips the upstream tests that need POSIX (`os.killpg`, owner-only file
  modes, unprivileged symlinks); the container sign-in desktop tests skip themselves off
  Linux. They pass on Linux, where the Docker image runs. Test with Python 3.12+ as
  `pyproject.toml` requires: on 3.11 the desktop helper's profile-cleanup tests fail because
  `shutil.rmtree(onexc=...)` does not exist yet. Upstream's Firefox capture tests use 20 ms
  timeouts and can flake on a loaded Windows machine; rerun before suspecting code. Check types with
  `mypy --platform linux src/` (POSIX-only `os`/`signal` attributes otherwise fail on
  Windows). Ruff findings in upstream's own session test files are left as shipped.
- `src/version.py` tracks two numbers: `UPSTREAM_VERSION` (the upstream release this fork
  was last synced with) and `MY_VERSION` (this fork's own version, independent of
  upstream). `__version__` follows `MY_VERSION`; keep `pyproject.toml` and `uv.lock` in
  sync with it (`tests/test_version_consistency.py` enforces this).
- CI/CD is intentionally minimal: `.github/workflows/docker-publish.yml` is the only
  workflow, and it just builds and pushes a `linux/amd64` image to Docker Hub
  (`DOCKER_USERNAME`/`DOCKER_PASSWORD` secrets) on push to `main`. The upstream project's
  semver release pipeline, contributor-credit automation, and PR validation workflow have
  been removed along with their supporting `.github/scripts/`.
- The `/api/version` endpoint (`src/web/app.py`) and footer UI check two things
  independently: this fork's own latest GitHub release (compared against `MY_VERSION`)
  and upstream's latest release (compared against `UPSTREAM_VERSION`), so it's visible
  in the dashboard when upstream has shipped something not yet merged here.

## Mandatory Contribution Workflow

Every coding agent MUST read [CONTRIBUTING.md](./CONTRIBUTING.md) before planning,
editing, testing, or reviewing changes and MUST follow its applicable requirements.
It is the repository's contribution policy, not optional background reading.

- Use its PR checklist as completion criteria, including integration with current
  `main` for PRs, unit and regression testing, and independent adversarial review.
- Pass this policy to delegated implementation and review agents. For agent-authored
  work, use a separate adversarial review agent when available, or an independent human.
- Report actual validation and review results and any missing checks in the handoff
  or PR. Never claim completion of checks that did not run or approval not received.
- Do not declare a PR ready to merge while required checks or review are missing or
  blocking findings remain. Document the gap and keep an incomplete PR in draft.

## Development Guidelines

### Issue triage

- `.github/ISSUE_TEMPLATE/bug_report.yml` requires the running app version,
  installation method, hosting environment, dashboard browser/device, reproduction
  steps, expected/actual behavior, redacted evidence, and troubleshooting results.
  Keep the form simple; allow an explanation when evidence is unavailable or browser
  details do not apply. The chooser disables blank issues and preserves a separate
  feature/question/documentation template.
- Read the issue and its comments before asking for missing information. Tailor the
  request to unresolved gaps, avoid duplicating unanswered requests, and distinguish
  feature requests, retired version-specific paths, and confirmed recoveries from
  current defects. Never request credentials, authentication files, whole data
  directories, or unredacted network captures. Preserve the home-hosting support scope.

1. **Testing**:
   - Always add unit tests for backend changes.
   - Frontend changes should have tests if possible.

2. **Code Style & Architecture**:
   - **DRY (Don't Repeat Yourself)**: Codebase must follow DRY principle.
   - **OOP (Object-Oriented Programming)**: Required for all backend code.

3. **Refactoring**:
   - You are authorized to refactor code to align with DRY/OOP principles.
   - **Permission Required**: You MUST ask for user permission before significant refactoring.

4. **Localization (i18n)**:
   - Update translation files if there are changes to UI text or console messages.
   - Frontend translation rendering must use safe DOM construction. Do not inject translated strings with non-clearing `innerHTML`; allowlist any intentional links and build them as DOM nodes.

5. **Documentation**:
   - Always update `README.md` and `AGENTS.md` when making changes.
   - Keep `README.md` short and focused on ordinary users: setup, login, migration,
     and links. Detailed public instructions belong in `docs/`, the source for the
     GitHub wiki. Developer contribution policy belongs in `CONTRIBUTING.md`.
   - Personal development plans, investigation notes, local verification records,
     and captures belong in `.dev-notes/`, which Git and Docker builds ignore.
     Never commit them or put them under `docs/`; this overrides skill templates
     that suggest committing `docs/plans/` or `docs/notes/`. Keep credentials out of
     public documentation, PRs, issues, and logs. Report concise, redacted validation
     results in the PR without publishing personal working records.
   - Keep `CLAUDE.md` and `GEMINI.md` as relative symbolic links to `AGENTS.md`; do not replace them with duplicated text. Put any agent-specific instructions in clearly named sections of `AGENTS.md`.

## Project Overview

Twitch Drops Miner is a Python application that automatically mines timed Twitch drops without downloading stream data. It uses Twitch's GraphQL API and websocket connections to simulate watching streams while tracking drop progress.

**Key Characteristics:**

- Python 3.12+ required
- Web-based GUI using FastAPI and Socket.IO
- Async/await architecture with asyncio
- Session persistence via cookies
- No stream video/audio download (bandwidth-efficient)
- Docker-ready for easy deployment

## Architecture


The application now uses a clean `src/` package structure with clear separation of concerns.

### Project Structure

```text
src/
├── models/          # Domain models (Game, Channel, Campaign, Drop, Benefit)
├── config/          # Configuration (constants, paths, operations, settings, client_info)
├── utils/           # Pure utilities (string, JSON, async helpers, rate_limiter, backoff)
├── i18n/            # Translation system (Translator class, TypedDict schemas)
├── auth/            # Authentication (auth_state for OAuth and token management)
├── api/             # External API (HTTP client, GraphQL client)
├── websocket/       # Real-time updates (websocket connection, pool)
├── web/             # Web GUI (app, gui_manager, api/)
│   └── managers/    # Individual UI managers (status, console, channels, campaigns, inventory, login, settings, cache, broadcaster)
├── services/        # Business logic services (channel, inventory, watch, maintenance, message_handlers)
├── core/            # Core client (Twitch client)
├── drop_history.py  # Claimed drop history store (JSON persistence + CSV/JSON export)
├── exceptions.py    # Custom exceptions
├── version.py       # UPSTREAM_VERSION / MY_VERSION
└── __main__.py      # Entry point

lang/                # Translation JSON files (20 languages)
├── English.json     # Default/fallback translations
├── Español.json
├── Français.json
├── Deutsch.json
└── ...              # 16 more languages
```

### Core Components

**main.py** - Simple launcher:

- Runs the `src` package as a module using `runpy.run_module("src")`
- All application logic is now in `src/__main__.py`

**src/__main__.py** - Entry point:

- Parses command-line arguments
- Initializes Settings, Twitch client, and WebGUIManager
- Starts the FastAPI web server (uvicorn on port 8080)
- Runs the main asyncio event loop
- Handles signals (SIGINT, SIGTERM on Linux) and exit codes

**src/core/client.py** - Central client (`Twitch` class):

- State machine: IDLE, INVENTORY_FETCH, GAMES_UPDATE, CHANNELS_CLEANUP, CHANNELS_FETCH, CHANNEL_SWITCH, EXIT
- Composes `_AuthState`, `HTTPClient`, and `GQLClient`
- Delegates to service layer for business logic
- Drop progress monitoring via periodic "watch" payloads
- Manages WebsocketPool and maintenance tasks

**src/services/** - Business logic layer (fully implemented):

- `ChannelService`: Channel management and selection logic
- `InventoryService`: Campaign and drop inventory operations
- `WatchService`: Drop mining watch payload logic
- `MaintenanceService`: Periodic maintenance tasks
- `MessageHandlerService`: Websocket message routing and handling


**src/models/channel.py** - Channel and Stream:

- `Channel` class: Twitch channel with online/offline status
- `Stream` class: Active stream with game, viewers, drop status
- Stream URL fetching and validation
- ACL-based vs directory channels

**src/models/campaign.py** - Drop campaigns:

- `DropsCampaign`: Campaign with game, timeframe, allowed channels
- Time-based eligibility and progress tracking
- Special Events (`509663`) and IRL (`509672`) are identified by `Game.is_special()`.
  Their campaigns can progress across categories only on live channels in a non-empty,
  enabled ACL. Without an ACL, the streamed category must still match. Explicit
  `ignore_channel_status=True` checks retain their discovery-only status bypass.
- `WatchService.can_watch()` requires the campaign's game in `wanted_games`, a live
  channel, and `campaign.can_earn(channel)`. Special categories bypass the channel's
  drops-enabled flag; regular campaigns still require it. Account eligibility, campaign
  and drop timing, prerequisites, claims, and ignore rules remain enforced.
- Channel priority still uses the streamed category; channels outside `wanted_games`
  retain `MAX_INT` fallback priority. Preserve special-category eligibility when changing
  watch selection; do not reintroduce an unconditional campaign/channel game equality gate.
- `WatchService.should_switch()` allows replacement of an unwatchable current channel
  before comparing priorities. Healthy streams keep the existing priority rules; tied
  `MAX_INT` participants must still take over after an offline or ineligible stream.

**src/models/drop.py** - Drop types:

- `TimedDrop`: Drops with minute requirements and progress
- `BaseDrop`: Base class with claim logic
- Precondition chains for sequential drops

**src/web/gui_manager.py** - Web GUI:

- `WebGUIManager`: Main GUI coordinator
- Composes individual managers for different UI concerns (status, console, channels, campaigns, inventory, login, settings, cache)
- Uses `WebSocketBroadcaster` for real-time Socket.IO updates
- Pure asyncio, no tkinter dependency

**src/web/app.py** - FastAPI application:

- REST API endpoints: `/api/status`, `/api/channels`, `/api/campaigns`, `/api/settings`, `/api/session`, `/api/session/finish`, `/api/session/retry`, `/api/session/logout`, `/api/session/vnc`, `/api/reload`, `/api/cache/clear`, `/api/close`, `/api/version`, `/api/history`, `/api/history/export.csv`, `/api/history/stats`
- Socket.IO server for real-time bi-directional communication
- Serves static web frontend from `web/` directory
- Integrates with WebGUIManager via `set_managers()`
- The Settings **Clear All Cache** action discards local campaign, channel, and other
  derived miner state, preserves OAuth login and settings, and then reloads from Twitch.
  It is a recovery and diagnostic action, not a correction for Twitch campaign metadata.
- `serve_index()` replaces the `__APP_VERSION__` placeholder in local CSS/JavaScript URLs
  with the application version and serves `/` with `Cache-Control: no-cache`
- Any `app.js` or `styles.css` change requires an application version bump through the release
  workflow before deployment so existing clients receive a new asset cache key

**src/websocket/pool.py** - WebSocket management:

- Sharded connections (up to 50 topics per socket, max 199 channels)
- Topics: User.Drops, User.Notifications, Channel.StreamState, Channel.StreamUpdate
- Automatic reconnection with exponential backoff
- Message routing to registered callbacks

**src/config/settings.py** - Application settings:

- Games to watch list (auto-populated from available campaigns if empty)
- Games can also be added manually from the web settings search box. Exact and
  unique partial matches resolve to available game names; ambiguous matches do not
  add a game. Confirmations support keyboard focus and Escape. Select All preserves
  priority order and manual entries, and manual confirmation uses current settings.
- Games to Watch supports drag ordering and editable integer priority numbers. Clamp valid
  ranks to the list bounds; reject blank/fractional values without changing settings.
  Keep priority and remove-control labels translated and accessible. Regression tests in
  `tests/test_game_priority.py` cover order, bounds, invalid inputs, and persistence calls.
- `farm_mode` (bool, default `False`, Settings > Farm Mode): when enabled, mines badge and
  emote drops from every other campaign already in `self.inventory` (when Twitch returns
  the catalog, it contains every campaign regardless of `games_to_watch`; while the
  catalog is withheld, see the campaign catalog fallback below). Implemented in
  `StreamSelector._get_wanted_game_tree` (`src/services/stream_selector.py`): games from
  `games_to_watch` are resolved first, filtered by the Mining Benefits settings as usual;
  farm-mode games are appended afterwards, filtered by a fixed `{BADGE, EMOTE}`-only
  allowlist regardless of Mining Benefits, and tagged `"farm_mode": True` in the tree (the
  web UI shows a "FARM" pill on these in the Wanted Drops Queue). Because channel priority
  (`ChannelService.get_priority`) is just `wanted_games.index(game)`, appending farm-mode
  games after the explicit list is sufficient to keep Games to Watch always winning channel
  slots - no separate priority mechanism was needed. Claiming is unaffected either way:
  `GAMES_UPDATE` claims any completed drop across the whole inventory regardless of
  `wanted_games` membership. Covered by `tests/test_farm_mode.py`.
- Connection quality multiplier
- Language selection
- Proxy support (including verification)
- Logging and dump flags from command-line arguments
- Persistence to JSON file (`settings.json`) in DATA_DIR
- Drop-name ignore list (`drop_name_blacklist`), empty by default. Entries are literal,
  case-insensitive substrings entered one per line; whitespace and blanks are removed and
  duplicates are casefolded while preserving the first spelling/order.
- Inventory filters (Status, Benefit Type, Game Search); Active/Upcoming/Expired use
  OR semantics, Not Linked narrows the result, and Finished opts claimed campaigns in.
  Zero-minute subscription rewards are omitted from Inventory and Wanted Drops Queue;
  individually expired and non-mineable rewards are omitted from the queue without hiding
  upcoming or sequential rewards; successful claims refresh the queue immediately; the
  actively watched channel remains visible while game settings are changing
- Consecutive identical no-active-campaign console prompts are collapsed until another
  console message appears
- Telegram drop notifications use a bot token stored server-side. The web API/socket
  never echoes the stored token: `get_settings()` returns only a `telegram_configured`
  flag and a masked placeholder, console logs mask the value, and a submitted value equal
  to the mask (or empty) leaves the stored credential untouched.
- Telegram alerts originate in the shared `BaseDrop.claim()` successful unclaimed-to-claimed
  transition, covering websocket, startup, and inventory-refresh claims without duplicate
  alerts for repeated events. Telegram failures must not change a successful Twitch claim.
- Campaign search log (Main tab > Output > Campaign Searches): `Twitch._fetch_inventory_logged`
  wraps every inventory fetch in a `RefreshRecord` (`src/services/refresh_log.py`, last 100,
  memory only) with its trigger, duration, campaign count and discovery stats
  (`InventoryService.last_discovery`, None when the catalog is served), and broadcasts
  `refresh_log_update`; `GET /api/refresh-log` and the socket `initial_state.refresh_log`
  return the same snapshot. Callers pass `request_inventory_refresh(trigger=...)`
  (`startup`, `scheduled`, `manual`, `cache_clear`, `games_changed`, `drop_notification`,
  `campaign_finished`, `unknown_claim`, default `other`); requests coalesced into a pending
  refresh keep the first trigger. The next scheduled search is the last successful finish
  plus `clamp_refresh_interval()` (`src/services/maintenance.py`, shared with the settings
  manager), because the maintenance timer restarts after every fetch. Trigger labels live
  in `gui.search_log.triggers` in all 20 language files. Tests: `tests/test_refresh_log.py`.
- Claim state never reverts. An inventory refresh can build drops from data fetched just
  before a websocket claim landed, so `fetch_inventory` carries `is_claimed`/`claim_id`
  over from the drop objects it replaces (`TimedDrop.adopt_claim`), and the websocket
  claim handler applies a claim that finished on a replaced object to the current one. A
  claim event for an unknown drop requests an inventory refresh so the sweep claims it.
  `DropsCampaign.first_drop` prefers drops still gaining minutes over finished ones
  awaiting a claim, so progress shows the drop actually being mined. Tests:
  `tests/test_claim_state_consistency.py`.
- The Telegram form reuses the saved token when its input is blank. Clearing the chat ID
  and saving disables alerts. Test Connection waits for settings persistence before showing
  success; HTTP, network, and application save failures must remain visible as errors.
- `tests/test_telegram_frontend.py`, `tests/test_telegram_api.py`, and
  `tests/test_telegram_integration.py` cover translated Help rendering, stored credentials,
  failed saves, disabling, all shared claim paths, and transport failures without sending
  real Telegram messages. Keep Telegram UI result strings in every locale.

Drop-name ignore policy is dependency-aware: a matching unclaimed drop and its dependent
branches are ignored dynamically. Prerequisite-only branches with no mineable reward are
skipped, while shared prerequisites required by an allowed reward remain mineable. Ignored
and skipped drops are never counted as claimed. Twitch can still award simultaneous
progress to an ignored drop while the miner intentionally targets another reward.

### State Machine Flow

1. **IDLE** - Waiting for campaigns or user action
2. **INVENTORY_FETCH** - Fetch campaigns from GraphQL, claim completed drops
3. **GAMES_UPDATE** - Determine wanted games based on priority/exclude lists
4. **CHANNELS_CLEANUP** - Remove channels not streaming wanted games
5. **CHANNELS_FETCH** - Discover channels via ACL lists or game directories
6. **CHANNEL_SWITCH** - Select best channel to watch based on priority/ACL
7. Loop between CHANNEL_SWITCH and periodic INVENTORY_FETCH (hourly)

### Authentication and integrated browser login

- `ImportedSession` owns validated WEB contexts and reads persisted formats 1/2/3.
  Accepted browser state takes precedence over valid saved Android cookies. Fresh,
  expired or rejected sessions wait for the integrated container browser. Do not
  restore legacy cookies after explicit logout. Version 3 atomically persists a
  logged-out marker with cleared session/SDK state; successful login clears it.
- `ContainerLogin` owns one temporary attempt with a 15-minute interactive timeout.
  Launch ordinary Chromium at Twitch sign in without remote automation, close its
  window normally to flush storage, then reopen the owned profile for CDP capture.
  Verify the browser PID before reading credentials. Finish sign in uses EWMH close;
  the window X works too. Profiles and all owned processes must be drained through
  cancellation, startup failures, timeout and shutdown. Never attach another profile.
- `SessionController` validates the captured account and catalog, independently issues
  a server context, revalidates the same account and atomically installs the seed.
  Account replacement and logout use `Twitch.authentication_change()` to cancel AND
  await miner, watch, maintenance, websocket callbacks and tracked channel/UI tasks.
  Clear old topics and derived account state before resuming. Epoch/revision checks
  prevent delayed login or renewal from reinstalling credentials after logout.
- The renewal worker rotates SDK state, normally five minutes before expiry, with
  bounded retries and same-account/freshness validation. Mining GraphQL stays in
  Python. Preserve safe-read retry rules; never replay ambiguous mutations.
- `SessionAPI` exposes sanitized status and finish/retry/logout actions, plus optional
  desktop-helper enable/cancel actions guarded by dashboard authentication and CSRF.
  There is no general session upload/export or renewal HTTP route. The binary
  `/api/session/vnc` WebSocket connects only to the current attempt’s loopback VNC
  listener, requires the exact dashboard origin and optional dashboard session, and
  rechecks authorization/attempt/state while connected. Cap viewers and input frames.
  All login HTTP actions retain CSRF/origin guards and fixed redacted error codes.
- The interactive desktop runs as dedicated UID/GID `tdm-browser`, with supplementary
  groups cleared and only PATH/timezone/locale environment inherited. Protect miner
  data and logs with mode 0700 and verify it took effect before starting VNC; fail
  closed on mounts that ignore chmod or give the browser ownership. Keep temporary
  display/profile directories owned by the browser UID. Root-only SDK profiles remain
  unreadable to it. Retain tests for file-access isolation and pre-login password setup.
- Docker includes Chromium, Xvfb, openbox, x11vnc, xdotool, noVNC and timezone data.
  Set `TZ` to the home internet connection’s timezone. Use init, shared memory and
  a cleanup grace period. Only the dashboard port is published. Display numbers are
  allocated by Xvfb to avoid stale fixed-display locks after engine restarts.
- The frontend shows the VNC sign in page when Twitch is logged out, keeps verification
  visible until cleanup, then restores the normal dashboard. Settings ends with Twitch
  logout. The dashboard-password form is reparented into the sign in screen while
  logged out, preserving its single form and listeners. Embedded login stays the default;
  desktop helpers are an explicit fallback. Detailed user
  guidance lives in `docs/authentication.md`; private tests and credentials stay ignored.
- Preserve matching WEB client/device/token/integrity/user-agent for imported requests,
  `Channel.url` on WEB for beacon discovery, locale/schema parity and safe DOM rendering.
  Never include session, SDK, password or verification data in dashboard status/logs.
- `HelperConnections` owns only temporary admission and receipts. It must call the
  shared `SessionController.accept()`; never add a second renewal worker or bypass
  current account validation, authentication-change draining, or logged-out persistence.
  Dashboard enable cancels and awaits the container attempt, then opens a ten-minute
  window for one helper. Native `/api/helper/connect` accepts an empty JSON object
  without a pairing code and issues the first client a scoped ticket;
  `/api/helper/session` submits once and `/api/helper/result` recovers
  a lost acknowledgement. These exact method/path pairs bypass dashboard cookies
  but retain origin/Fetch Metadata/request-header checks and bounded bodies. Upload
  and result routes require the issued bearer ticket. Admission is closed by default,
  requires an explicit dashboard action, and never uses a persistent enable flag.
  Atomically recheck the live window after parsing before issuing the one ticket.
  Recheck admission expiry and initiating dashboard authorization before installation.
  Cancel, logout, shutdown, replacement login and new admission invalidate stale work.
  In-memory receipts are intentionally lost on restart; never replay ambiguous uploads.
  Keep tickets out of URLs, CLI arguments, logs, status, broadcasts and storage.
  Document that the first reachable helper is admitted during the trusted-network window.
  `tests/test_helper_admission.py` and `tests/test_desktop_helper_api.py` cover these boundaries.
- Keep the released helper instructions and download links aligned with the four build
  targets: Windows x64, Linux x64, macOS arm64, and macOS x64. Users choose the helper
  computer's platform and match the miner release. The login screen's **Use desktop
  helper** action shows only the dashboard URL; do not reintroduce a pairing-code or
  dashboard-password prompt in the helper. **Return to embedded browser** revokes access.
  Explain the ten-minute, first-helper admission window and retain the embedded browser
  as the default. On macOS, users must quit the helper-owned browser instance to flush
  its profile. Keep the release-ready README, installation examples, login guide, and
  generated release-note instructions consistent with these behaviors.

### Dashboard authentication

- `src/web/auth.py` owns optional password-only dashboard protection, separate from Twitch
  OAuth and ordinary settings. It defaults off and stores a salted scrypt hash and SHA-256
  session-token digests in `data/web_auth.json` using atomic replacement; corrupt state must
  fail closed. Never expose these credentials in settings, broadcasts, validation errors,
  logs, or cache operations. Use one miner process per data directory.
- `AuthMiddleware` guards FastAPI and the outer Socket.IO ASGI app. Login resources,
  auth status, and `/healthz` are public when enabled. Login actions and the VNC
  viewer require the dashboard session when protection is enabled. Unsafe HTTP requests require
  `X-TDM-Request: 1`; writes and Socket.IO reject foreign origins. `DashboardOrigin` in
  `src/web/origin.py` owns the optional `PUBLIC_BASE_URL` startup configuration: one
  absolute HTTP(S) root URL supplies the allowed browser origin and cookie scheme even
  behind an HTTP backend or rewritten Host. Normalize host/scheme/default ports and IP
  serialization to browser origins; reject ambiguous short/octal/hex IPv4 forms and
  credentials, paths, queries, fragments, wildcard/list origins, and malformed values
  without echoing them. Unset/empty preserves request-derived behavior. This configuration
  must not change ASGI scheme/client or trust forwarded headers; client-IP forwarding
  still requires explicit trusted proxies. HTTPS public URLs enable Secure cookies on
  set and delete. Preserve CSRF, Fetch Metadata, session/revocation, and rate-limit checks.
  `tests/test_web_public_url.py` covers both Socket.IO transports, production environment
  wiring, cookie lifecycles, hostile origins, and separate proxy-IP trust.
- Default cookies are HttpOnly, SameSite=Strict session cookies. Remember me adds a fixed
  30-day Max-Age; server sessions also expire after 30 days and survive restarts. Logout
  revokes the current session; password changes require the current password and revoke
  other sessions. Disabling auth requires the current password and clears all credentials.
- `AuthSocketServer` rechecks authorization on events and broadcasts, disconnects revoked
  sessions, and schedules idle connections to close at expiry. Enabling auth must evict
  already connected anonymous clients before subsequent private broadcasts.
- `web/static/auth.js` owns login/settings behavior and adds the same-origin write header.
  A failed initial auth-status request must leave login available for retry without a
  reload; settings controls stay disabled until auth state is known.
  Keep all UI strings in `gui.auth` across all locales and render them using textContent.
  Local auth assets use the same `__APP_VERSION__`/`MY_VERSION` cache key as app.js and
  styles.css; bump `MY_VERSION` before deploying changes to existing auth assets.
- `tests/test_web_auth.py` and `tests/test_web_auth_frontend.py` cover access control,
  credential persistence, cookie lifetimes, CSRF, rate limiting, revocation, and UI errors.
  The idle socket-expiry regression controls the auth wall clock and captures the
  scheduled callback. Preserve its remaining-lifetime, disconnect, and cleanup assertions;
  do not replace them with millisecond session lifetimes or fixed wall-clock sleeps.
  Docker checks `/healthz`, not the protected `/api/status`. Recovery is local: stop the
  miner, restrict access, remove only `data/web_auth.json`, restart and set a new password.

### Drop Mining Mechanism

The application sends periodic "watch" payloads through Twitch GraphQL `sendSpadeEvents`:

- Payload contains gzip/base64-encoded minute-watched events with channel/broadcast IDs
- Twitch reports progress via websocket (User.Drops topic)
- If websocket updates stop, fallback to GQL CurrentDrop query
- Extrapolation via "bump minutes" when no updates received

### GraphQL Operations

Persisted operations are defined in `src/config/operations.py` as `GQL_OPERATIONS`; raw GraphQL payloads such as `sendSpadeEvents` use `GQLQuery`:

- **Inventory** - Fetch in-progress campaigns and claimed benefits
- **Campaigns** - List available active/upcoming campaigns
- **CampaignDetails** - Detailed drop info for a campaign
- **GameDirectory** - Find live streams for a game with drops enabled
- **GetStreamInfo** - Check if channel is online and get stream details
- **CurrentDrop** - Query currently mined drop progress
- **ClaimDrop** - Claim a completed drop
- **AvailableDrops** - Check which campaigns a channel qualifies for (badge validation)
- **NotificationsDelete** - Delete Twitch notifications

**Campaign catalog fallback (fork-specific):** added when the v1.3.1/v1.3.2 `SMARTBOX`
client got `dropCampaigns: null` for **Campaigns** and a null `dropCampaign` for
**CampaignDetails** (also as raw queries; upstream rangermix/TwitchDropsMiner#118). The
browser/Android sessions used since 2.0 normally receive the catalog, so this path is an idle safety net
that only runs when the catalog is null. Under `SMARTBOX`, **Inventory**,
**SlugRedirect**, **GameDirectory**, **AvailableDrops** and raw queries on
`channel.viewerDropCampaigns` still work. When the catalog is null,
`InventoryService._discover_campaigns_from_channels` pages the drops-enabled directory for
each Games to Watch entry (up to `DISCOVERY_CHANNELS_PER_GAME`, following `pageInfo`
cursors for at most `ceil(DISCOVERY_CHANNELS_PER_GAME / DIRECTORY_PAGE_SIZE)` pages and
stopping once a page adds no new channel: Twitch was observed reporting `hasNextPage`
forever while re-sending channels, which hung the fetch), asks those channels which campaigns they offer via batched **AvailableDrops**, then
sends one raw `CHANNEL_CAMPAIGNS_QUERY` (`src/config/operations.py`, `GQLRawQuery`) per
offering channel for the complete data: account linking, reward `distributionType`, ACL,
preconditions and per-drop progress. Nothing is assumed. Discovered campaigns are
remembered until they end, so they (and their ACL channels' stream-state subscriptions)
survive refreshes while no participant is live; remembered, not re-queried data drops
per-drop `self`, so claims resolve from the Inventory's `gameEventDrops`. In-progress
campaign IDs are skipped. `needs_discovery()` reports tracked games no discovery has
covered; settings changes go through `Twitch.request_policy_update()`, which refetches the
inventory instead of only recomputing the queue when a newly added game needs discovery.
`GQLClient` tolerates raw-query responses lacking `extensions.operationName` when retrying.
Farm Mode cannot discover games outside Games to Watch while the catalog is null. Tests:
`tests/test_channel_campaign_discovery.py`, `tests/test_client_state_requests.py`,
`tests/test_gql_raw_query_retry.py`.

### Channel Selection Priority

1. Selected channel (if user clicked one)
2. ACL-based channels over directory channels
3. Game priority order (from settings)
4. Viewer count (descending)
5. Maximum 199 channels tracked simultaneously

### Maintenance Task

Runs in background to trigger:

- Channel cleanup when drops start/end (based on time_triggers)
- Inventory reload every `minimum_refresh_interval_minutes` (Settings > General;
  default 30, clamped to [1, 1440] in `MaintenanceService.run_maintenance_task`)

### Translation System

**Architecture:**

- All translations stored as JSON files in `lang/` directory (20 languages supported)
- English (`lang/English.json`) is the single source of truth and fallback language
- Strongly typed with TypedDict schema defined in `src/i18n/translator.py`
- Translator class (`src/i18n/translator.py`) handles language loading and fallback
- Singleton instance `_` available via `from src.i18n import _`

**Supported Languages:**

- English, Dansk (Danish), Deutsch (German), Español (Spanish), Français (French)
- Magyar (Hungarian), Indonesian, Italiano (Italian), Nederlandse (Dutch), Polski (Polish), Português (Portuguese)
- Română (Romanian), Türkçe (Turkish), Čeština (Czech)
- Русский (Russian), Українська (Ukrainian), العربية (Arabic)
- 日本語 (Japanese), 简体中文 (Simplified Chinese), 繁體中文 (Traditional Chinese)

**Translation Structure:**

```python
Translation = {
    "language_name": str,      # Display name of language
    "english_name": str,       # English name of language
    "status": StatusMessages,  # Console status messages
    "login": LoginMessages,    # Login-related messages
    "error": ErrorMessages,    # Error messages
    "gui": GUIMessages        # All web GUI text (tabs, settings, help, etc.)
}
```

**Usage:**

```python
from src.i18n import _

# Access translations
status_text = _.t["gui"]["status"]["idle"]  # Returns "Idle"
login_text = _.t["login"]["status"]["logged_in"]  # Returns "Logged in"
```

**Language Persistence:**

- Language selection persisted in `settings.json` (DATA_DIR)
- Dynamic language switching supported in web GUI
- Changes take effect immediately without restart

## Key Files

- **src/config/constants.py** - Core enums (State, WebsocketTopic), logging config, type aliases
- **src/config/operations.py** - GraphQL operation definitions (GQL_OPERATIONS)
- **src/config/paths.py** - Path management and Docker environment detection
- **src/config/client_info.py** - Twitch client info (Client-Id, User-Agent)
- **src/config/settings.py** - Application settings with JSON persistence
- **src/exceptions.py** - Custom exceptions (MinerException, ExitRequest, RequestException, RequestInvalid, WebsocketClosed, LoginException, CaptchaRequired, GQLException)
- **src/drop_history.py** - Claimed-drop history store (`DropHistory`) with atomic JSON
  persistence, filtering, stats, and CSV export; recorded on every successful drop claim
- **src/utils/** - Helper utilities (string_utils, json_utils, async_helpers, rate_limiter, backoff)
- **src/i18n/** - Internationalization package with TypedDict schema and Translator class
  - **translator.py** - Translator class with typed translation schema (Translation TypedDict)
  - **__init__.py** - Exports translation types and `_` (Translator instance)
- **lang/** - Translation JSON files for 20 languages (English.json is the single source of truth)
- **src/version.py** - `UPSTREAM_VERSION` (upstream release last synced), `MY_VERSION`
  (this fork's own version; `__version__` follows this)
- **src/web/app.py** - FastAPI application with REST API and Socket.IO
- **src/web/managers/cache.py** - ImageCache for campaign artwork caching
- **web/** - Frontend assets (index.html, static/app.js, static/styles.css)

## Development Commands

**IMPORTANT: Always activate the virtual environment first!**

The project uses a virtual environment located at `env/`, managed with `uv`. Create it
once, then activate it before every Python command:

```bash
# First setup only
uv venv env --python 3.12
source env/bin/activate                 # Windows PowerShell: . .\env\Scripts\Activate.ps1
uv sync --active --extra dev --locked --python 3.12
```

`--locked` fails if `uv.lock` is out of date instead of silently rewriting it. After an
intentional dependency change in `pyproject.toml`, run `uv lock`, review the diff, and
sync again.

### Linting, Type Checking, and Tests

Run from the activated environment:

```bash
python -m ruff check src/          # lint (also covers import order, pyupgrade, bugbear, etc.)
python -m ruff check --fix src/    # auto-fix what Ruff can
python -m mypy src/                # type check (not currently enforced in CI; report new diagnostics)
python -m pytest tests/            # full test suite
python -m pytest tests/test_settings_api.py            # one file
python -m pytest tests/test_settings_api.py::test_name -v  # one test, verbose
```

Frontend behavior tests (e.g. `tests/test_web_auth_frontend.py`,
`tests/test_telegram_frontend.py`) drive `web/static/app.js` through Node.js via the
shared `tests/javascript_helpers.py` helper — Node.js (v24 recommended) must be on
`PATH` or those tests fail/are skipped.

### Running the Application

```bash
# Run from source (remember to activate venv first!)
source env/bin/activate && python main.py

# With verbose logging (stackable: -vv, -vvv)
source env/bin/activate && python main.py -v

# Create data dump for debugging
source env/bin/activate && python main.py --dump

# Access the web interface at http://localhost:8080
```

### Development Setup

The application requires:

- Python 3.12+
- Virtual environment at `env/` (must be activated before running commands)
- Dependencies from `pyproject.toml` (includes FastAPI, uvicorn, Socket.IO)
- Node.js 24 for frontend behavior tests

Docker deployment from a source checkout:

```bash
# Build and run the included Compose configuration
docker compose up -d --build

# Access at http://localhost:8080
```

Keep the README's Docker update commands aligned with `docs/installation.md`.
Published-image updates pull before stopping/removing the old container, stop on
command failures, and recreate with the same data mount and custom runtime options.
Retain `--init`, the 30-second shutdown grace period, `--shm-size 256m`, and the
user's timezone. Explain that `latest` and restart policies do not update running
containers automatically. Keep the README and installation guide's published-image
`compose.yaml` examples synchronized, including browser settings and persistent
data/log mounts. Pinned-image Compose updates must explain changing the image tag
before pulling. The included `docker-compose.yml` builds the checkout; use
`docker compose pull` for the published-image configuration without `build:`.
Keep those configurations in separate folders because `compose.yaml` takes
precedence. Migration from `docker run` must preserve the exact data mount, remove
the stopped old container before Compose creates its replacement, and avoid running
two miners against the same data or using `down -v` to update.

## Testing

### Automated Tests

The project includes a test suite in the `tests/` directory; see "Linting, Type
Checking, and Tests" above for the commands to run it.

The suite covers settings and proxy behavior, inventory-filter behavior, API filtering,
GraphQL watch events, batched channel discovery, full-locale translation schema and
placeholder consistency, frontend DOM safety, case-insensitive channel filtering,
watch-drop count and expiry semantics, immediate claim refresh behavior, consecutive
no-campaign console collapsing, and the claimed-drop history store with CSV export and
API endpoints. Frontend behavior tests share their JavaScript extraction helper and
require Node.js (v24 recommended) to be installed locally. Ignore-list coverage includes
normalization and settings persistence, dependency pruning, the combined expiry/ignore
Wanted Queue guard, watch selection, truthful ignored/skipped inventory state, translated
placeholder parity, and frontend rendering. `web/index.html` loads `app.js` and
`styles.css` with a `?v=__APP_VERSION__` cache-busting suffix that `src/web/app.py`
replaces with `__version__` (i.e. `MY_VERSION`) at serve time; bump `MY_VERSION` in
`src/version.py` when changing either file so existing clients pick up the new asset.

`tests/test_special_game_watch.py` covers Special Events and IRL across streamed categories,
missing category/drops flags, offline and nonparticipating channels, disabled or absent ACLs,
Games to Watch selection, campaign/drop eligibility, active-campaign selection, and fallback
priority and failover. It uses mocked Twitch state and does not verify live Twitch progress.

### Continuous Integration

There is no automated lint/type-check/test workflow in this fork — run Ruff, Mypy, and
pytest locally before committing (see Development Commands below). The only workflow is
`.github/workflows/docker-publish.yml`, which builds and pushes a `linux/amd64` Docker
image to Docker Hub on push to `main` (or manual dispatch). Upstream's validation
pipeline, contributor-credit automation, and version-release pipeline were intentionally
removed; see "About This Fork" above.

Keep browser/control listeners private; do not revive standalone sidecar URLs or
unrestricted credential-upload routes. The standard Alpine image owns default login
and renewal; an admitted desktop helper supplies only an initial validated session.


### Manual Testing

1. Run with `-vvv` for maximum verbosity (levels: -v, -vv, -vvv, -vvvv)
2. Check log files in `./logs/` directory
3. Monitor web GUI console output and browser developer tools

## Web GUI Architecture

The application uses a web-based interface accessible via browser:

### Web GUI Components

**src/web/gui_manager.py** - WebGUIManager class:

- Managers: StatusManager, ConsoleOutputManager, ChannelListManager, CampaignProgressManager, InventoryManager, LoginFormManager, SettingsManager, CacheManager
- Uses WebSocketBroadcaster to push real-time updates to connected clients via Socket.IO
- Pure async/await implementation

**src/web/app.py** - FastAPI application:

- REST API endpoints: `/api/status`, `/api/channels`, `/api/campaigns`, `/api/settings`, `/api/session`, `/api/session/finish`, `/api/session/retry`, `/api/session/logout`, `/api/session/vnc`, `/api/reload`, `/api/cache/clear`, `/api/close`, `/api/version`, `/api/history`, `/api/history/export.csv`, `/api/history/stats`
- Socket.IO server for real-time bi-directional communication
- Serves static web frontend from `web/` directory
- Integrates with WebGUIManager via `set_managers()`

**web/** - Frontend assets:

- `index.html` - Single-page application layout with tabs (Main, Inventory, History, Settings, Help)
- `static/app.js` - Socket.IO client, real-time UI updates, API calls, Inventory Filtering and Drop History logic
- `static/styles.css` - Responsive design with dark mode support

### Communication Protocol

**Server → Client (Socket.IO events):**

- `initial_state` - Full state on connect
- `status_update` - Status bar changes
- `console_output` - New log lines
- `channel_add/update/remove` - Channel list changes
- `drop_progress` - Drop mining progress
- `campaign_add` - New campaign added
- `login_required` - Prompt for credentials
- `settings_updated` - Settings changed

**Client → Server:**

- REST API for actions (login, settings, channel selection)
- Socket.IO for connection management

### Docker Integration

**src/config/paths.py:**

- Detects Docker environment via `DOCKER_ENV` env var or `/.dockerenv` file
- Docker: Uses `/app` for code, `/app/data` for persistent storage
- Development: Uses `<project_root>/data` for persistent storage
- All user data (cookies, settings, cache, logs) stored in DATA_DIR
- Provides `_resource_path()` helper for locating bundled resources

**Dockerfile:**

- Based on `python:3-alpine`, including Chromium for internal SDK renewal
- Installs dependencies from `pyproject.toml`
- Exposes port 8080
- Health check on the public `/healthz` endpoint

**docker-compose.yml:**

- Volume mounts `./data:/app/data` for persistence
- Port mapping `8080:8080`
- Auto-restart policy
- Timezone configuration

### Key Design Decisions

- **WebSocket for real-time** - Socket.IO chosen for reliability (fallback to polling)
- **Single-page app** - Simpler than full framework (React/Vue), fast load times
- **Direct Docker support** - Environment detection, proper path handling
- **Integrated Twitch authentication** - Sign in through the container browser in the
  dashboard, then validate and renew the saved session. Preserve valid saved Android
  sessions during migration.

## Project Scope

This is a hobby project for personal use on the user's own hardware and home network.
Support is limited to that setup and is provided on a best-effort basis. VPS, cloud,
other third-party hosting environments, and services operated for other users are
outside the support scope. Remote dashboard access and Docker support do not expand
that deployment scope. Keep the README disclaimer and contribution guidance consistent
with this policy when reviewing proposals or documenting deployment options.

**Supported:**

- ✅ Web GUI - browser-based interface with advanced filtering
- ✅ Docker deployment - containerized on the user's own home hardware
- ✅ Remote access - access to the user's home-hosted instance
- ✅ Headless operation - no display server required

**NOT supported:**

- Multi-account support
- Channel points mining
- Mining for unlinked campaigns
- Desktop GUI

### Claimed Drop History

`DropHistory` records successful claims locally in `data/drop_history.json`, deduplicated
by drop ID. The History tab provides game/date filters, pagination, statistics, CSV export,
and confirmed local deletion. Date-only filters mean midnight UTC; aware timestamps
preserve their instant. CSV attachment names use UTF-8 percent encoding with an ASCII
fallback. History text is defined in `gui.history` for every locale and rendered as text.
Tests cover persistence, filtering, Unicode exports, offsets, and translated UI behavior.
