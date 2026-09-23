"""Правила расписания: `at`, `interval`, `cron` — расчёт следующего срабатывания."""

from datetime import UTC, datetime

import pytest

from mcp_scheduler.triggers import next_cron, next_run_at, parse_iso


def dt(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_parse_iso_accepts_z_and_offset() -> None:
    assert parse_iso("2026-09-24T12:00:00Z") == dt(2026, 9, 24, 12, 0, 0)
    assert parse_iso("2026-09-24T12:00:00+03:00") == dt(2026, 9, 24, 9, 0, 0)
    assert parse_iso("2026-09-24T12:00:00") == dt(2026, 9, 24, 12, 0, 0)
    assert parse_iso("не дата") is None


def test_at_trigger_once_then_none() -> None:
    trigger = {"type": "at", "at": "2026-09-24T12:00:00Z"}
    assert next_run_at(trigger, dt(2026, 9, 24, 11, 0)) == dt(2026, 9, 24, 12, 0)
    # после момента срабатывания — больше нет
    assert next_run_at(trigger, dt(2026, 9, 24, 12, 0)) is None
    assert next_run_at(trigger, dt(2026, 9, 25)) is None


def test_interval_trigger_adds_seconds() -> None:
    trigger = {"type": "interval", "seconds": 60}
    assert next_run_at(trigger, dt(2026, 9, 24, 10, 0, 0)) == dt(2026, 9, 24, 10, 1, 0)
    trigger0 = {"type": "interval", "seconds": 0}
    assert next_run_at(trigger0, dt(2026, 9, 24, 10)) is None


def test_cron_daily_at_9_on_monday() -> None:
    # 2026-09-23 — среда; ближайший понедельник — 2026-09-28
    assert next_run_at({"type": "cron", "expr": "0 9 * * 1"}, dt(2026, 9, 23, 12, 0)) == dt(
        2026, 9, 28, 9, 0
    )


def test_cron_every_15_minutes() -> None:
    assert next_run_at({"type": "cron", "expr": "*/15 * * * *"}, dt(2026, 9, 24, 10, 7)) == dt(
        2026, 9, 24, 10, 15
    )
    assert next_run_at({"type": "cron", "expr": "*/15 * * * *"}, dt(2026, 9, 24, 10, 30)) == dt(
        2026, 9, 24, 10, 45
    )


def test_cron_lists_and_ranges() -> None:
    trigger = {"type": "cron", "expr": "0,30 8-10 * * *"}
    assert next_run_at(trigger, dt(2026, 9, 24, 8, 45)) == dt(2026, 9, 24, 9, 0)
    assert next_run_at(trigger, dt(2026, 9, 24, 10, 30)) == dt(2026, 9, 25, 8, 0)


def test_cron_no_such_day_returns_none() -> None:
    # 30 февраля не существует
    assert next_cron("0 0 30 2 *", dt(2026, 9, 23)) is None


def test_cron_invalid_expr_raises() -> None:
    with pytest.raises(ValueError):
        next_cron("0 9 * *", dt(2026, 9, 23))  # 4 поля, а не 5
    with pytest.raises(ValueError):
        next_cron("0 61 * * *", dt(2026, 9, 23))  # минута 61 вне диапазона


def test_unknown_trigger_type_returns_none() -> None:
    assert next_run_at({"type": "bogus"}, dt(2026, 9, 23)) is None
