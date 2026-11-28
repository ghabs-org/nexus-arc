"""Five-field cron with IANA time zones and deterministic DST behavior."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _field(text, low, high):
    values = set()
    for item in text.split(","):
        base, *step = item.split("/")
        stride = int(step[0]) if len(step) == 1 else 1
        if len(step) > 1 or stride < 1:
            raise ValueError("Invalid cron step")
        if base == "*":
            start, end = low, high
        elif "-" in base:
            start, end = map(int, base.split("-"))
        else:
            start = end = int(base)
        if not low <= start <= end <= high:
            raise ValueError("Cron field outside allowed range")
        values.update(range(start, end + 1, stride))
    return values


def parse_cron(expression, timezone):
    if not isinstance(expression, str) or len(expression) > 120:
        raise ValueError("Use a five-field cron expression")
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("Use five cron fields: minute hour day month weekday")
    try:
        zone = ZoneInfo(timezone)
        parsed = [
            _field(value, *bounds)
            for value, bounds in zip(
                fields, [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)], strict=True
            )
        ]
    except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError("Invalid cron expression or IANA timezone") from exc
    if 7 in parsed[4]:
        parsed[4].add(0)
    return fields, parsed, zone


def next_occurrence(expression, timezone, after):
    fields, parts, zone = parse_cron(expression, timezone)
    if not isinstance(after, datetime) or after.utcoffset() is None:
        raise ValueError("Schedule dates must include a timezone")
    candidate = after.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    # Iterate dates, then matching hours/minutes: bounded even for annual jobs.
    local_date = candidate.astimezone(zone).date()
    for offset in range(366 * 5):
        day = local_date + timedelta(days=offset)
        if day.month not in parts[3]:
            continue
        dom, dow = day.day in parts[2], (day.weekday() + 1) % 7 in parts[4]
        matches_day = (dom or dow) if fields[2] != "*" and fields[4] != "*" else dom and dow
        if not matches_day:
            continue
        for hour in sorted(parts[1]):
            for minute in sorted(parts[0]):
                local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone, fold=0)
                utc = local.astimezone(UTC)
                # Round-trip rejects spring-forward gaps. fold=0 selects one
                # occurrence during fall-back, regardless of worker restart.
                if utc.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
                    continue
                if utc >= candidate:
                    return utc
    raise ValueError("Cron schedule has no occurrence within five years")
