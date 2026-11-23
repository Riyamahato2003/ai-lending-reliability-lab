from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...

    def unix_seconds(self) -> int: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def unix_seconds(self) -> int:
        return int(self.now().timestamp())


@dataclass(frozen=True)
class FrozenClock:
    instant: datetime

    def now(self) -> datetime:
        value = self.instant
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    def unix_seconds(self) -> int:
        return int(self.now().timestamp())


system_clock: Clock = SystemClock()
