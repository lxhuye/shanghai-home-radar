from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class ObservationWindow:
    start_at: datetime
    end_at: datetime

    def contains(self, observed_at: datetime) -> bool:
        if observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        normalized = observed_at.astimezone(UTC)
        return self.start_at <= normalized < self.end_at


def closed_day_window(as_of_date: date, window_days: int) -> ObservationWindow:
    """Return a Shanghai calendar-day window as a half-open UTC interval."""
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    local_end = datetime.combine(as_of_date + timedelta(days=1), time.min, SHANGHAI)
    local_start = local_end - timedelta(days=window_days)
    return ObservationWindow(local_start.astimezone(UTC), local_end.astimezone(UTC))
