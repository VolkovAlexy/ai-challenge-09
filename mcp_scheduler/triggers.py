"""Правила расписания: `at` (однократно по ISO), `interval` (период в секундах), `cron`.

`next_run_at(trigger, last)` — момент следующего срабатывания строго ПОСЛЕ `last`.
Возвращает `None`, если следующего срабатывания нет (например `at`-триггер уже в прошлом).

Триггер описывается dict'ом:
- `{"type": "at", "at": "2026-09-24T12:00:00+00:00"}` — сработать один раз в момент `at`;
- `{"type": "interval", "seconds": 60}` — каждые 60 секунд (репит);
  c `"repeat": false` — срабатывает один раз через `seconds` (одноразовый);
- `{"type": "cron", "expr": "0 9 * * 1"}` — по cron-выражению `m h dom mon dow`.

Cron: поддерживаются `*`, списки `1,2,5`, диапазоны `1-5`, шаги `*/15` и `1-5/2`.
Минуты 0-59, часы 0-23, день месяца 1-31, месяц 1-12, день недели 0-7 (0 и 7 — воскресенье).
"""

from __future__ import annotations

import datetime as _dt
from datetime import UTC, datetime

ShiftedValue = tuple[int, ...]


def parse_iso(value: str) -> datetime | None:
    """Разбирает ISO-строку (с offset или `Z`, либо naive → UTC). None — не разобралось."""
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def next_run_at(trigger: dict[str, object], last: datetime) -> datetime | None:
    """Момент следующего срабатывания после `last` (в UTC), либо None."""
    trigger_type = trigger.get("type")
    if trigger_type == "at":
        at = parse_iso(str(trigger.get("at", "")))
        if at is None:
            return None
        return at if at > last else None
    if trigger_type == "interval":
        seconds = int(trigger.get("seconds", 0))
        if seconds <= 0:
            return None
        return last + _dt.timedelta(seconds=seconds)
    if trigger_type == "cron":
        return next_cron(str(trigger.get("expr", "")), last)
    return None


def _parse_field(field: str, lo: int, hi: int) -> set[int]:
    """Разбирает одно cron-поле в набор допустимых значений (в диапазоне lo..hi)."""
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            base, step_text = part.split("/", 1)
            step = int(step_text)
            if step <= 0:
                raise ValueError(f"шаг cron-поля должен быть > 0: {part!r}")
        else:
            base, step = part, 1
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start, end = int(base), int(base)
        if start < lo or end > hi or start > end:
            raise ValueError(f"значение cron-поля вне диапазона: {part!r}")
        values.update(range(start, end + 1, step))
    return values


def _dow_to_weekday(cron_dow: int) -> int:
    """cron dow (0/7=воскресенье, 1=понедельник) → datetime.weekday() (0=понедельник)."""
    return (cron_dow - 1) % 7


def next_cron(expr: str, last: datetime) -> datetime | None:
    """Следующая минута cron-выражения строго после `last`, либо None (не нашлось за 3 года)."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron-выражение должно из 5 полей: {expr!r}")
    try:
        minutes = _parse_field(fields[0], 0, 59)
        hours = _parse_field(fields[1], 0, 23)
        dom = _parse_field(fields[2], 1, 31)
        months = _parse_field(fields[3], 1, 12)
        dow = {_dow_to_weekday(d) for d in _parse_field(fields[4], 0, 7)}
    except ValueError:
        raise

    dom_restricted = dom != set(range(1, 32))
    dow_restricted = dow != set(range(7))

    last = last.astimezone(UTC) if last.tzinfo else last.replace(tzinfo=UTC)
    cursor = last.replace(second=0, microsecond=0) + _dt.timedelta(minutes=1)

    for _ in range(366 * 3):
        day = cursor.date()
        if day.month not in months:
            cursor = (cursor + _dt.timedelta(days=1)).replace(hour=0, minute=0)
            continue
        dom_match = day.day in dom
        dow_match = day.weekday() in dow
        if dom_restricted and dow_restricted:
            day_ok = dom_match or dow_match
        elif dom_restricted:
            day_ok = dom_match
        elif dow_restricted:
            day_ok = dow_match
        else:
            day_ok = True
        if not day_ok:
            cursor = (cursor + _dt.timedelta(days=1)).replace(hour=0, minute=0)
            continue
        for hour in sorted(hours):
            for minute in sorted(minutes):
                candidate = _dt.datetime.combine(day, _dt.time(hour, minute), tzinfo=UTC)
                if candidate >= cursor:
                    return candidate
        cursor = (cursor + _dt.timedelta(days=1)).replace(hour=0, minute=0)
    return None
