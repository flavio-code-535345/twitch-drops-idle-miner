"""In-memory log of campaign searches (inventory fetches) for the dashboard."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass
class RefreshRecord:
    trigger: str
    started_at: datetime
    finished_at: datetime | None = None
    campaigns: int | None = None
    # Campaigns found through live channels, and games searched; None when Twitch
    # served the full campaign catalog.
    discovered: int | None = None
    games_searched: int | None = None
    error: str | None = None

    def finish(self, campaigns: int, discovery: tuple[int, int] | None) -> None:
        self.finished_at = datetime.now(timezone.utc)
        self.campaigns = campaigns
        if discovery is not None:
            self.discovered, self.games_searched = discovery

    def fail(self, error: str) -> None:
        self.finished_at = datetime.now(timezone.utc)
        self.error = error

    def to_json(self) -> dict[str, Any]:
        return {
            "trigger": self.trigger,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "campaigns": self.campaigns,
            "discovered": self.discovered,
            "games_searched": self.games_searched,
            "error": self.error,
        }


class RefreshLog:
    MAX_ENTRIES = 100

    def __init__(self) -> None:
        self._records: deque[RefreshRecord] = deque(maxlen=self.MAX_ENTRIES)

    def start(self, trigger: str) -> RefreshRecord:
        record = RefreshRecord(trigger, datetime.now(timezone.utc))
        self._records.append(record)
        return record

    def snapshot(self, interval_minutes: int) -> dict[str, Any]:
        """Newest-first entries plus when the next scheduled search is due.

        The maintenance timer restarts at the end of every successful fetch, so the next
        scheduled search is the last successful finish plus the interval.
        """
        last_success = next(
            (r.finished_at for r in reversed(self._records) if r.finished_at and not r.error),
            None,
        )
        return {
            "entries": [record.to_json() for record in reversed(self._records)],
            "interval_minutes": interval_minutes,
            "next_scheduled_at": (
                (last_success + timedelta(minutes=interval_minutes)).isoformat()
                if last_success is not None
                else None
            ),
        }
