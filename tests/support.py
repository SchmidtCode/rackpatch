import sys
import types
from datetime import datetime, timedelta, timezone


def install_croniter_stub() -> None:
    if "croniter" in sys.modules:
        return

    def _parse_field(token: str, *, minimum: int, maximum: int) -> set[int]:
        token = str(token).strip()
        if token == "*":
            return set(range(minimum, maximum + 1))
        values: set[int] = set()
        for part in token.split(","):
            value = int(part.strip())
            if minimum <= value <= maximum:
                values.add(value)
        return values

    def _cron_dow(value) -> int:
        return (value.weekday() + 1) % 7

    class _SimpleCroniter:
        def __init__(self, expr: str, base):
            fields = str(expr).split()
            if len(fields) != 5:
                raise ValueError(f"unsupported cron expression for test stub: {expr}")
            self._minutes = _parse_field(fields[0], minimum=0, maximum=59)
            self._hours = _parse_field(fields[1], minimum=0, maximum=23)
            self._days = _parse_field(fields[2], minimum=1, maximum=31)
            self._months = _parse_field(fields[3], minimum=1, maximum=12)
            dow_values = _parse_field(fields[4].replace("7", "0"), minimum=0, maximum=6)
            self._dows = {0 if value == 7 else value for value in dow_values}
            self._base = base

        def get_next(self, value_type):
            del value_type
            tz = self._base.tzinfo or timezone.utc
            base_utc = self._base.astimezone(timezone.utc)
            start_date = self._base.astimezone(tz).date()
            for day_offset in range(0, 370):
                date = start_date + timedelta(days=day_offset)
                if date.month not in self._months or date.day not in self._days:
                    continue
                local_probe = datetime(date.year, date.month, date.day, tzinfo=tz)
                if _cron_dow(local_probe) not in self._dows:
                    continue
                for hour in sorted(self._hours):
                    for minute in sorted(self._minutes):
                        local_candidate = datetime(date.year, date.month, date.day, hour, minute, tzinfo=tz)
                        candidate_utc = local_candidate.astimezone(timezone.utc)
                        if candidate_utc <= base_utc:
                            continue
                        return candidate_utc.astimezone(tz)
            raise RuntimeError("test croniter stub could not find next matching datetime")

    croniter_stub = types.ModuleType("croniter")
    croniter_stub.croniter = _SimpleCroniter
    sys.modules["croniter"] = croniter_stub
