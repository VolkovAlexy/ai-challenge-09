"""SchedulerStore: схема, CRUD заданий, запуски и точки данных."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_scheduler.store import SchedulerStore


def make_store() -> SchedulerStore:
    return SchedulerStore(Path(":memory:"))


def dt(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_add_job_sets_next_run_future() -> None:
    store = make_store()
    job = store.add_job("collect", "c", {"type": "interval", "seconds": 3600}, {"x": 1})
    assert job.kind == "collect"
    assert job.enabled is True
    assert job.next_run is not None
    assert job.last_run is None


def test_add_job_duplicate_name_raises() -> None:
    store = make_store()
    store.add_job("collect", "c", {"type": "interval", "seconds": 60})
    with pytest.raises(ValueError):
        store.add_job("collect", "c", {"type": "interval", "seconds": 60})


def test_add_job_invalid_kind_raises() -> None:
    store = make_store()
    with pytest.raises(ValueError):
        store.add_job("bogus", "x", {"type": "interval", "seconds": 60})


def test_add_job_unknown_trigger_raises() -> None:
    store = make_store()
    with pytest.raises(ValueError):
        store.add_job("collect", "x", {"type": "nope"})


def test_get_list_remove() -> None:
    store = make_store()
    store.add_job("collect", "a", {"type": "interval", "seconds": 60})
    store.add_job("summary", "b", {"type": "interval", "seconds": 60})
    assert store.get_job("a") is not None
    assert store.get_job("nope") is None
    assert sorted(j.name for j in store.list_jobs()) == ["a", "b"]
    assert store.remove_job("a") is True
    assert store.remove_job("a") is False
    assert [j.name for j in store.list_jobs()] == ["b"]


def test_set_enabled_toggles() -> None:
    store = make_store()
    store.add_job("collect", "c", {"type": "interval", "seconds": 60})
    assert store.set_enabled("c", False) is True
    assert store.get_job("c").enabled is False
    assert store.set_enabled("c", True) is True
    assert store.get_job("c").enabled is True


def test_due_jobs_empty_for_future() -> None:
    store = make_store()
    store.add_job("collect", "c", {"type": "interval", "seconds": 3600})
    # `now` в прошлом относительно `next_run` — задание ещё не созрело
    assert store.due_jobs(dt(2020, 1, 1)) == []


def test_complete_run_advances_interval() -> None:
    store = make_store()
    job = store.add_job("collect", "c", {"type": "interval", "seconds": 3600})
    store.complete_run(job, dt(2026, 9, 24, 10, 0, 0), {})
    updated = store.get_job("c")
    assert updated.last_run == "2026-09-24T10:00:00.000000+00:00"
    assert updated.next_run == "2026-09-24T11:00:00.000000+00:00"
    assert updated.enabled is True


def test_complete_run_disables_oneshot_after_due() -> None:
    store = make_store()
    job = store.add_job("reminder", "r", {"type": "at", "at": "2026-09-25T00:00:00Z"}, {"m": "x"})
    # срабатывание ПОСЛЕ момента `at` -> одноразовое задание выключается
    store.complete_run(job, dt(2026, 9, 26, 0, 0), {"note": "x"})
    updated = store.get_job("r")
    assert updated.next_run is None
    assert updated.enabled is False


def test_complete_run_disables_interval_with_repeat_false() -> None:
    store = make_store()
    job = store.add_job(
        "reminder", "r", {"type": "interval", "seconds": 300, "repeat": False}, {"m": "x"}
    )
    assert job.next_run is not None
    # одноразовый interval: после срабатывания не перезапускается
    store.complete_run(job, dt(2026, 9, 24, 10, 5, 0), {"note": "x"})
    updated = store.get_job("r")
    assert updated.next_run is None
    assert updated.enabled is False


def test_complete_run_interval_with_repeat_true_keeps_running() -> None:
    store = make_store()
    job = store.add_job(
        "reminder", "r", {"type": "interval", "seconds": 300, "repeat": True}, {"m": "x"}
    )
    store.complete_run(job, dt(2026, 9, 24, 10, 5, 0), {"note": "x"})
    updated = store.get_job("r")
    assert updated.next_run is not None
    assert updated.enabled is True


def test_append_and_query_data_window() -> None:
    store = make_store()
    store.append_data("m", {"value": 1})
    store.append_data("m", {"value": 2})
    store.append_data("n", {"value": 3})
    assert len(store.query_data("m")) == 2
    assert len(store.query_data("n")) == 1
    # окно «никогда» (since в будущем) — пусто
    assert store.query_data("m", since="2999-01-01T00:00:00+00:00") == []
    # окно «всё» (since в прошлом) — обе точки
    assert sum(p["value"] for p in store.query_data("m", since="1970-01-01T00:00:00+00:00")) == 3


def test_latest_run_returns_summary() -> None:
    store = make_store()
    job = store.add_job("summary", "s", {"type": "interval", "seconds": 60})
    store.complete_run(job, dt(2026, 9, 24, 10), {"aggregate": {"count": 3}})
    latest = store.latest_run("s")
    assert latest is not None
    assert latest["summary"]["aggregate"]["count"] == 3
    assert store.latest_run("nope") is None


def test_count_jobs() -> None:
    store = make_store()
    assert store.count_jobs() == 0
    store.add_job("collect", "c", {"type": "interval", "seconds": 60})
    assert store.count_jobs() == 1
