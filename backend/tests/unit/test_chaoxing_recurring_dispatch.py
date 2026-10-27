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


# ── max_duration_min: reaching it must END the task ───────────────────────────


def _running_child(manager, *, task_id="child-1", stop_in_minutes=-5):
    """A running child task whose max_duration_min has already elapsed."""
    import threading

    stop_event = threading.Event()
    pause_event = threading.Event()
    pause_event.set()  # the worker is allowed to run right now
    manager._tasks[task_id] = {
        "task_id": task_id,
        "user_id": "u1",
        "platform": "chaoxing",
        "status": "running",
        "message": "Learning course 1/1",
        "current_task": "course:测试课程",
        "progress": manager._default_progress(),
        "logs": [],
        "_log_cursor": 0,
        "_stop_event": stop_event,
        "_pause_event": pause_event,
        "schedule": {
            "repeat": "once",
            "start_at": None,
            "stop_at": None,
            "start_jitter_min": 0,
            "stop_jitter_min": 0,
            "fire_at": None,
            # Already in the past => max_duration_min has elapsed.
            "actual_stop_at": (
                datetime.now(UTC) + timedelta(minutes=stop_in_minutes)
            ).isoformat(),
            "parent_task_id": "tpl-1",
        },
    }
    return manager._tasks[task_id], stop_event, pause_event


def test_reaching_max_duration_ends_the_task_not_pauses_it(monkeypatch):
    """The old behaviour left the task `paused` forever and holding the slot.

    `paused` is an ACTIVE status, so a run that hit max_duration_min kept
    occupying the user's single active-task slot until somebody cancelled it by
    hand -- the reported "11pm and still not finished" bug.
    """
    manager = _make_manager()
    _noop_store(monkeypatch)
    task, stop_event, _pause_event = _running_child(manager)

    manager.dispatch_scheduled_tasks()

    assert task["status"] == "cancelled"
    assert task["_stop_reason"] == lm.STOP_REASON_SCHEDULE
    # The worker thread must also be told to unwind, otherwise it keeps running
    # invisibly while the record says cancelled.
    assert stop_event.is_set()


def test_schedule_stop_is_not_reported_as_a_user_cancel(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    task, _stop, _pause = _running_child(manager)

    manager.dispatch_scheduled_tasks()

    assert task["message"] == lm.CANCEL_MESSAGES[lm.STOP_REASON_SCHEDULE]
    assert "user" not in task["message"].lower()


def test_a_user_cancelled_task_keeps_its_own_message(monkeypatch):
    """The schedule path must not hijack the wording of a manual cancel."""
    manager = _make_manager()
    _noop_store(monkeypatch)
    task, stop_event, _pause = _running_child(manager)
    task["_stop_reason"] = lm.STOP_REASON_USER
    stop_event.set()

    manager._cancel_task(task["task_id"])

    assert task["message"] == lm.CANCEL_MESSAGES[lm.STOP_REASON_USER]


def test_expired_task_stops_holding_an_active_slot(monkeypatch):
    """count_active_tasks drops once the expired run is cancelled."""
    from app.services.course.chaoxing.task_admission import count_active_tasks

    manager = _make_manager()
    _noop_store(monkeypatch)
    _running_child(manager)
    assert count_active_tasks(manager._tasks) == 1

    manager.dispatch_scheduled_tasks()

    assert count_active_tasks(manager._tasks) == 0


def test_task_before_max_duration_keeps_running(monkeypatch):
    """Not yet due: nothing may be touched."""
    manager = _make_manager()
    _noop_store(monkeypatch)
    task, stop_event, pause_event = _running_child(manager, stop_in_minutes=+30)

    manager.dispatch_scheduled_tasks()

    assert task["status"] == "running"
    assert not stop_event.is_set()
    assert pause_event.is_set()


# ── max_duration_min: manual runs must honour the cap too ────────────────────
#
# The cap used to be attached to the payload only for recurring tasks, and even
# then a manual run had no `schedule` block, so the dispatcher skipped it. The
# result: "单次上限" was displayed for every task kind but enforced for none of
# the manually started ones -- a run could keep going all night.


def _manual_run(manager, *, task_id="manual-1", deadline_in_minutes=-5):
    """A running manual task: no schedule block, only a task-level deadline."""
    import threading

    stop_event = threading.Event()
    pause_event = threading.Event()
    pause_event.set()
    manager._tasks[task_id] = {
        "task_id": task_id,
        "user_id": "u1",
        "platform": "chaoxing",
        "status": "running",
        "message": "Learning course 1/1",
        "current_task": "course:测试课程",
        "progress": manager._default_progress(),
        "logs": [],
        "_log_cursor": 0,
        "_stop_event": stop_event,
        "_pause_event": pause_event,
        "deadline_at": (datetime.now(UTC) + timedelta(minutes=deadline_in_minutes)).isoformat(),
    }
    return manager._tasks[task_id], stop_event, pause_event


def test_a_manual_run_records_its_deadline(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    _no_worker(monkeypatch)

    task_id = manager.start_task("u1", {"platform": "chaoxing", "max_duration_min": 90})

    task = manager._tasks[task_id]
    assert not task.get("schedule"), "手动任务本就没有 schedule 块"
    assert task["deadline_at"], "截止时间必须记在任务上，否则调度循环看不到它"
    remaining = (datetime.fromisoformat(task["deadline_at"]) - datetime.now(UTC)).total_seconds() / 60
    assert 89 <= remaining <= 91


def test_a_manual_run_without_a_cap_has_no_deadline(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    _no_worker(monkeypatch)

    task_id = manager.start_task("u1", {"platform": "chaoxing"})

    assert manager._tasks[task_id]["deadline_at"] is None


def test_a_manual_run_past_its_deadline_is_ended(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    task, stop_event, _pause = _manual_run(manager, deadline_in_minutes=-5)

    manager.dispatch_scheduled_tasks()

    assert task["status"] == "cancelled", "超过单次上限的手动任务必须被结束"
    assert stop_event.is_set(), "还要通知 worker 线程退出"


def test_a_manual_run_before_its_deadline_keeps_running(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    task, _stop, _pause = _manual_run(manager, deadline_in_minutes=30)

    manager.dispatch_scheduled_tasks()

    assert task["status"] == "running"


def _scheduled_task(manager, *, stop_at=None, max_duration_min=60):
    manager._tasks["sched-1"] = {
        "task_id": "sched-1",
        "user_id": "u1",
        "platform": "chaoxing",
        "status": "scheduled",
        "message": "Scheduled",
        "current_task": "scheduled",
        "progress": manager._default_progress(),
        "logs": [],
        "_log_cursor": 0,
        "credentials": {"username": "u", "password": "p", "tiku_config": {}},
        "schedule_args": {"platform": "chaoxing", "course_ids": ["1"]},
        "schedule": {
            "repeat": "once",
            "start_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "stop_at": stop_at,
            # Already due.
            "fire_at": (datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
            "start_jitter_min": 0,
            "stop_jitter_min": 0,
            "max_duration_min": max_duration_min,
            "actual_stop_at": None,
        },
    }
    return manager._tasks["sched-1"]


def test_a_scheduled_run_applies_the_cap_when_no_stop_at_was_given(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    _no_worker(monkeypatch)
    task = _scheduled_task(manager, stop_at=None, max_duration_min=60)

    manager.dispatch_scheduled_tasks()

    remaining = (datetime.fromisoformat(task["schedule"]["actual_stop_at"]) - datetime.now(UTC)).total_seconds() / 60
    assert 59 <= remaining <= 61, "没有 stop_at 时，单次上限必须自己算出截止时间"


def test_the_earlier_of_stop_at_and_the_cap_wins(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    _no_worker(monkeypatch)
    # stop_at 只有 10 分钟，上限 60 分钟 → 取 10 分钟
    stop_at = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    task = _scheduled_task(manager, stop_at=stop_at, max_duration_min=60)

    manager.dispatch_scheduled_tasks()

    remaining = (datetime.fromisoformat(task["schedule"]["actual_stop_at"]) - datetime.now(UTC)).total_seconds() / 60
    assert 9 <= remaining <= 11, "两个限制都不能被突破，取更早的那个"


# ── idle runs: a schedule with nothing left to do must stop itself ───────────


def _recurring_child(manager, *, child_id="child-1", parent_id="tpl-1"):
    manager._tasks[child_id] = {
        "task_id": child_id,
        "user_id": "u1",
        "platform": "chaoxing",
        "status": "completed",
        "message": "Task completed",
        "current_task": "finished",
        "progress": manager._default_progress(),
        "logs": [],
        "_log_cursor": 0,
        "schedule": {"repeat": "daily", "parent_task_id": parent_id, "actual_stop_at": None},
    }
    return manager._tasks[child_id]


def test_an_idle_run_stops_its_recurring_schedule(monkeypatch):
    """什么都不用做时，每天再跑一遍只会重复空转（还会写随机草稿）。"""
    manager = _make_manager()
    _noop_store(monkeypatch)
    _recurring_child(manager)
    _template(manager, task_id="tpl-1")

    manager._note_idle_run("child-1")

    assert manager._tasks["tpl-1"]["status"] == "cancelled"
    assert "already complete" in manager._tasks["tpl-1"]["message"].lower()


def test_a_manual_run_has_no_schedule_to_stop(monkeypatch):
    """手动任务没有 parent_task_id，不能被误停。"""
    manager = _make_manager()
    _noop_store(monkeypatch)
    manager._tasks["manual-1"] = {
        "task_id": "manual-1",
        "user_id": "u1",
        "platform": "chaoxing",
        "status": "completed",
        "message": "Task completed",
        "current_task": "finished",
        "progress": manager._default_progress(),
        "logs": [],
        "_log_cursor": 0,
    }

    manager._note_idle_run("manual-1")

    assert manager._tasks["manual-1"]["status"] == "completed"


def test_idle_threshold_keeps_the_schedule_on_the_first_idle_run(monkeypatch):
    """阈值设为 2 时，第一次空转只记录，第二次才停。"""
    monkeypatch.setenv("RECURRING_IDLE_RUNS_BEFORE_STOP", "2")
    manager = _make_manager()
    _noop_store(monkeypatch)
    _recurring_child(manager)
    _template(manager, task_id="tpl-1")

    manager._note_idle_run("child-1")
    assert manager._tasks["tpl-1"]["status"] == "recurring", "第一次空转不应停"
    assert manager._tasks["tpl-1"]["schedule"]["idle_runs"] == 1

    manager._note_idle_run("child-1")
    assert manager._tasks["tpl-1"]["status"] == "cancelled", "连续第二次空转才停"
    assert "2 idle run" in manager._tasks["tpl-1"]["message"]


def test_a_productive_run_resets_the_idle_streak(monkeypatch):
    monkeypatch.setenv("RECURRING_IDLE_RUNS_BEFORE_STOP", "2")
    manager = _make_manager()
    _noop_store(monkeypatch)
    _recurring_child(manager)
    _template(manager, task_id="tpl-1")

    manager._note_idle_run("child-1")
    assert manager._tasks["tpl-1"]["schedule"]["idle_runs"] == 1

    manager._reset_idle_run_counter("child-1")
    assert manager._tasks["tpl-1"]["schedule"]["idle_runs"] == 0

    manager._note_idle_run("child-1")
    assert manager._tasks["tpl-1"]["status"] == "recurring", "清零后重新计数"


def test_the_idle_threshold_defaults_to_one(monkeypatch):
    monkeypatch.delenv("RECURRING_IDLE_RUNS_BEFORE_STOP", raising=False)
    assert lm._idle_runs_before_stop() == 1
    monkeypatch.setenv("RECURRING_IDLE_RUNS_BEFORE_STOP", "garbage")
    assert lm._idle_runs_before_stop() == 1, "非法值回退默认"
    monkeypatch.setenv("RECURRING_IDLE_RUNS_BEFORE_STOP", "0")
    assert lm._idle_runs_before_stop() == 1, "0 表示非法，回退默认"


def test_idle_predicate_judges_by_result_not_by_code_path():
    """判据是「有没有成果」，而不是「有没有进入执行流程」。

    章节任务已完成（跳过）和测验一题未命中（执行了但零收获）都是空转；
    线上就出现过「章节全跳过 + 测验零命中」却被判成有活干的情况。
    """
    idle = lm._idle_run_completed

    # 全部跳过、测验也零命中 → 空转
    assert idle(0, {"total": 5, "skipped": 5, "quizzes": 0, "answers": 0, "work": 0}) is True
    # 有章节执行了测验但零命中 → 仍是空转（这正是线上的情形）
    assert idle(0, {"total": 5, "skipped": 3, "quizzes": 2, "answers": 0, "work": 0}) is True
    # 命中过题目 → 有成果
    assert idle(0, {"total": 5, "skipped": 3, "quizzes": 2, "answers": 4, "work": 0}) is False
    # 播过视频 → 有成果
    assert idle(0, {"total": 5, "skipped": 5, "quizzes": 0, "answers": 0, "work": 12}) is False
    # 一个章节都没取到 → 不能推断课程已完成
    assert idle(0, {"total": 0, "skipped": 0, "quizzes": 0, "answers": 0, "work": 0}) is False
    # 有失败课程 → 可能是临时故障，明天该重试
    assert idle(1, {"total": 5, "skipped": 5, "quizzes": 0, "answers": 0, "work": 0}) is False
