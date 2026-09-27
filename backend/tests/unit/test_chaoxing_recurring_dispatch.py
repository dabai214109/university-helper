"""Recurring-task dispatcher tests.

These exercise the manager's scheduling loop directly: a recurring template must
spawn a SEPARATE child task per occurrence, keep living afterwards, never replay
a backlog after downtime, and never occupy the user's one-active-task slot.
"""

from datetime import UTC, datetime, timedelta

import app.services.course.chaoxing.learning_manager as lm
from app.services.course.chaoxing.learning_manager import ChaoxingLearningManager


def _make_manager() -> ChaoxingLearningManager:
    manager = ChaoxingLearningManager.__new__(ChaoxingLearningManager)
    import threading

    manager._lock = threading.Lock()
    manager._tasks = {}
    manager._loaded_task_users = set()
    manager._last_full_restore_ts = 0.0
    return manager


def _noop_store(monkeypatch):
    monkeypatch.setattr(lm.task_store, "upsert_task", lambda kind, payload: True)
    # No live QR session: the tests supply a password instead.
    monkeypatch.setattr(lm.chaoxing_cookie_vault, "get", lambda user_id: None)


def _no_worker(monkeypatch):
    """Stop the real worker thread from running.

    These tests assert on the task records the dispatcher/admission path
    creates. A real worker would immediately fail against the stubbed Chaoxing
    client and rewrite the status, hiding what is actually under test.
    """

    class _InertThread:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def start(self):
            return None

        def join(self, timeout=None):
            return None

        def is_alive(self):
            return False

    monkeypatch.setattr(lm.threading, "Thread", _InertThread)


def _template(
    manager,
    *,
    task_id="tpl-1",
    repeat="daily",
    time_of_day="08:00",
    next_fire_at=None,
    anchor_date=None,
    password="secret",
    fired_count=0,
):
    schedule = {
        "repeat": repeat,
        "time_of_day": time_of_day,
        "anchor_date": anchor_date,
        "max_duration_min": None,
        "next_fire_at": next_fire_at,
        "last_fired_at": None,
        "fired_count": fired_count,
        "start_at": None,
        "stop_at": None,
        "start_jitter_min": 0,
        "stop_jitter_min": 0,
        "fire_at": None,
        "actual_stop_at": None,
    }
    manager._tasks[task_id] = {
        "task_id": task_id,
        "user_id": "u1",
        "platform": "chaoxing",
        "status": "recurring",
        "message": "Recurring template",
        "current_task": "recurring",
        "progress": manager._default_progress(),
        "created_at": "2026-09-26T00:00:00+00:00",
        "started_at": "2026-09-26T00:00:00+00:00",
        "updated_at": "2026-09-26T00:00:00+00:00",
        "logs": [],
        "_log_cursor": 0,
        "schedule": schedule,
        "credentials": {"username": "u", "password": password, "tiku_config": {}},
        "schedule_args": {
            "platform": "chaoxing",
            "course_ids": ["200_300_400"],
            "speed": 1.5,
            "concurrency": 4,
            "unopened_strategy": "retry",
            "notify_config": {},
        },
    }
    return schedule


def _children(manager):
    return [
        task for task in manager._tasks.values() if task.get("schedule", {}).get("parent_task_id")
    ]


# ── firing ────────────────────────────────────────────────────────────────────


def test_due_template_spawns_a_child_task(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    _no_worker(monkeypatch)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    _template(manager, next_fire_at=past)

    manager.dispatch_scheduled_tasks()

    children = _children(manager)
    assert len(children) == 1
    assert children[0]["status"] == "pending"
    assert children[0]["schedule"]["parent_task_id"] == "tpl-1"


def test_template_survives_its_own_firing(monkeypatch):
    """The template must stay recurring so it can fire again tomorrow."""
    manager = _make_manager()
    _noop_store(monkeypatch)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    schedule = _template(manager, next_fire_at=past)

    manager.dispatch_scheduled_tasks()

    assert manager._tasks["tpl-1"]["status"] == "recurring"
    assert schedule["fired_count"] == 1
    assert schedule["last_fired_at"]
    # And it advanced to a future slot.
    assert datetime.fromisoformat(schedule["next_fire_at"]) > datetime.now(UTC)


def test_not_yet_due_template_does_not_fire(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    _template(manager, next_fire_at=future)

    manager.dispatch_scheduled_tasks()

    assert _children(manager) == []
    assert manager._tasks["tpl-1"]["schedule"]["fired_count"] == 0


def test_child_inherits_the_template_credentials_and_args(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    _template(manager, next_fire_at=past)

    fired: list[tuple[str, str, dict]] = []

    class _RecordingThread:
        def __init__(self, target=None, args=(), daemon=None, **kwargs):
            self._args = args

        def start(self):
            fired.append(self._args)

    monkeypatch.setattr(lm.threading, "Thread", _RecordingThread)

    manager.dispatch_scheduled_tasks()

    assert fired, "no worker thread was started for the occurrence"
    child_id, user_id, payload = fired[0]
    assert user_id == "u1"
    assert payload["password"] == "secret"
    assert payload["course_ids"] == ["200_300_400"]
    assert child_id in manager._tasks


# ── backlog protection ────────────────────────────────────────────────────────


def test_missed_occurrences_are_not_replayed_in_a_burst(monkeypatch):
    """After long downtime only ONE occurrence fires, then it jumps forward."""
    manager = _make_manager()
    _noop_store(monkeypatch)
    long_ago = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    schedule = _template(manager, next_fire_at=long_ago)

    manager.dispatch_scheduled_tasks()

    # Exactly one child, not ten.
    assert len(_children(manager)) == 1
    assert schedule["fired_count"] == 1
    assert datetime.fromisoformat(schedule["next_fire_at"]) > datetime.now(UTC)


def test_second_dispatch_does_not_double_fire(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    _template(manager, next_fire_at=past)

    manager.dispatch_scheduled_tasks()
    manager.dispatch_scheduled_tasks()

    # The template advanced past now, so the second scan finds nothing due.
    assert len(_children(manager)) == 1


# ── missing credentials ───────────────────────────────────────────────────────


def test_missing_credentials_skips_the_occurrence_but_keeps_the_schedule(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    schedule = _template(manager, next_fire_at=past, password="")

    manager.dispatch_scheduled_tasks()

    assert _children(manager) == []
    # Crucially the template is NOT failed — the user may re-scan and recover.
    assert manager._tasks["tpl-1"]["status"] == "recurring"
    assert schedule["fired_count"] == 1
    assert datetime.fromisoformat(schedule["next_fire_at"]) > datetime.now(UTC)


# ── max duration ──────────────────────────────────────────────────────────────


def test_max_duration_sets_the_child_stop_time(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    schedule = _template(manager, next_fire_at=past)
    schedule["max_duration_min"] = 90

    manager.dispatch_scheduled_tasks()

    child = _children(manager)[0]
    stop_at = datetime.fromisoformat(child["schedule"]["actual_stop_at"])
    started = datetime.fromisoformat(child["created_at"])
    assert (stop_at - started) == timedelta(minutes=90)


def test_no_max_duration_leaves_the_child_unbounded(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    _template(manager, next_fire_at=past)

    manager.dispatch_scheduled_tasks()

    assert _children(manager)[0]["schedule"]["actual_stop_at"] is None


# ── admission interaction ─────────────────────────────────────────────────────


def test_template_does_not_block_the_user_from_starting_another_task(monkeypatch):
    """The whole point of `recurring` not being in ACTIVE_TASK_STATUSES."""
    manager = _make_manager()
    _noop_store(monkeypatch)
    _no_worker(monkeypatch)
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    _template(manager, next_fire_at=future)

    # A second, ordinary task for the same user must be admitted.
    task_id = manager.start_task("u1", {"platform": "chaoxing", "course_ids": ["1_2_3"]})

    assert task_id
    assert manager._tasks[task_id]["status"] == "pending"


def test_template_does_not_count_towards_active_capacity(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    _template(manager, next_fire_at=future)

    assert lm.count_active_tasks(manager._tasks) == 0


# ── restore ───────────────────────────────────────────────────────────────────


def test_restored_template_recomputes_a_stale_next_fire_at(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    stale = (datetime.now(UTC) - timedelta(days=5)).isoformat()

    stored = {
        "task_id": "tpl-restored",
        "user_id": "u1",
        "platform": "chaoxing",
        "status": "recurring",
        "message": "Recurring template",
        "progress": {},
        "created_at": "2026-09-01T00:00:00+00:00",
        "started_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
        "logs": [],
        "schedule": {
            "repeat": "daily",
            "time_of_day": "08:00",
            "anchor_date": None,
            "next_fire_at": stale,
            "fired_count": 3,
        },
    }

    assert manager._merge_task_from_store(stored) is True

    task = manager._tasks["tpl-restored"]
    assert task["status"] == "recurring"
    assert datetime.fromisoformat(task["schedule"]["next_fire_at"]) > datetime.now(UTC)


def test_restored_template_is_not_marked_failed(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)

    stored = {
        "task_id": "tpl-2",
        "user_id": "u1",
        "platform": "chaoxing",
        "status": "recurring",
        "progress": {},
        "logs": [],
        "schedule": {"repeat": "daily", "time_of_day": "08:00", "next_fire_at": None},
    }

    manager._merge_task_from_store(stored)

    assert manager._tasks["tpl-2"]["status"] == "recurring"


def test_restore_tolerates_a_corrupt_recurrence_rule(monkeypatch):
    """A bad stored rule must not crash the restore loop."""
    manager = _make_manager()
    _noop_store(monkeypatch)

    stored = {
        "task_id": "tpl-3",
        "user_id": "u1",
        "platform": "chaoxing",
        "status": "recurring",
        "progress": {},
        "logs": [],
        "schedule": {"repeat": "daily", "time_of_day": "not-a-time", "next_fire_at": None},
    }

    assert manager._merge_task_from_store(stored) is True
    assert manager._tasks["tpl-3"]["status"] == "recurring"


# ── task-control paths must not destroy a template ────────────────────────────


def test_pause_refuses_to_convert_a_template(monkeypatch):
    """Pausing would overwrite `recurring` and silently delete the schedule."""
    manager = _make_manager()
    _noop_store(monkeypatch)
    _template(manager, next_fire_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat())

    result = manager.pause_task("u1", "tpl-1")

    assert result.get("code") == "invalid_status"
    assert manager._tasks["tpl-1"]["status"] == "recurring"


def test_resume_refuses_to_convert_a_template(monkeypatch):
    """Resuming would flip the template to `running`, which is not a schedule."""
    manager = _make_manager()
    _noop_store(monkeypatch)
    _template(manager, next_fire_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat())

    result = manager.resume_task("u1", "tpl-1")

    assert result.get("code") == "invalid_status"
    assert manager._tasks["tpl-1"]["status"] == "recurring"


def test_stop_cancels_the_schedule_and_stamps_finished_at(monkeypatch):
    """Stopping is the supported way to delete a recurring schedule."""
    manager = _make_manager()
    _noop_store(monkeypatch)
    _template(manager, next_fire_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat())

    result = manager.stop_task("u1", "tpl-1")

    assert result["status"] == "cancelled"
    task = manager._tasks["tpl-1"]
    assert task["status"] == "cancelled"
    assert task["finished_at"]


def test_a_cancelled_template_no_longer_fires(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    _no_worker(monkeypatch)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    _template(manager, next_fire_at=past)

    manager.stop_task("u1", "tpl-1")
    manager.dispatch_scheduled_tasks()

    assert _children(manager) == []


def test_reschedule_rejects_a_template(monkeypatch):
    """One-shot rescheduling is a different operation; it must not half-apply."""
    manager = _make_manager()
    _noop_store(monkeypatch)
    _template(manager, next_fire_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat())

    result = manager.reschedule_task(
        "u1", "tpl-1", (datetime.now(UTC) + timedelta(days=2)).isoformat()
    )

    assert result.get("code") == "invalid_status"
    assert manager._tasks["tpl-1"]["status"] == "recurring"


def test_start_now_on_a_template_spawns_a_child_and_keeps_the_schedule(monkeypatch):
    """"Run once now" must not consume or shift the schedule."""
    manager = _make_manager()
    _noop_store(monkeypatch)
    _no_worker(monkeypatch)
    future = (datetime.now(UTC) + timedelta(hours=6)).isoformat()
    schedule = _template(manager, next_fire_at=future)

    result = manager.start_now_task("u1", "tpl-1")

    assert result["status"] == "pending"
    assert result.get("child_task_id")
    # The template survives untouched...
    assert manager._tasks["tpl-1"]["status"] == "recurring"
    assert schedule["next_fire_at"] == future
    assert schedule["fired_count"] == 0
    # ...and exactly one child run was created.
    assert len(_children(manager)) == 1


def test_start_now_still_works_for_a_one_shot_task(monkeypatch):
    """The historical behaviour must be unchanged for `scheduled`."""
    manager = _make_manager()
    _noop_store(monkeypatch)
    manager._tasks["one-shot"] = {
        "task_id": "one-shot",
        "user_id": "u1",
        "platform": "chaoxing",
        "status": "scheduled",
        "message": "",
        "current_task": "scheduled",
        "progress": manager._default_progress(),
        "logs": [],
        "_log_cursor": 0,
        "schedule": {"repeat": "once", "start_at": None, "fire_at": None},
    }

    result = manager.start_now_task("u1", "one-shot")

    assert result["status"] == "scheduled"
    assert manager._tasks["one-shot"]["schedule"]["fire_at"]
    assert manager._tasks["one-shot"]["status"] == "scheduled"
