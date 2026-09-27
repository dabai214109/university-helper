"""Queue-view (`progress.courses`) tests for the Chaoxing learning manager.

The queue view is what lets the page show every ticked course — pending,
running, done — in the order the user chose. It must stay consistent with the
scalar counters the older UI still reads, and it must never leave an entry stuck
on "running" after the task ends for a reason that is not per-course.
"""

import threading

import app.services.course.chaoxing.learning_manager as lm
from app.services.course.chaoxing.learning_manager import ChaoxingLearningManager


def _make_manager(task_id: str = "t1", courses=None) -> ChaoxingLearningManager:
    manager = ChaoxingLearningManager.__new__(ChaoxingLearningManager)
    manager._lock = threading.Lock()
    manager._tasks = {}
    manager._loaded_task_users = set()
    progress = manager._default_progress()
    progress["courses"] = list(courses or [])
    manager._tasks[task_id] = {
        "task_id": task_id,
        "user_id": "u1",
        "platform": "chaoxing",
        "status": "running",
        "message": "",
        "current_task": "",
        "progress": progress,
        "logs": [],
        "_log_cursor": 0,
    }
    return manager


def _noop_store(monkeypatch):
    monkeypatch.setattr(lm.task_store, "upsert_task", lambda kind, payload: True)


THREE_COURSES = [
    {"name": "线性代数", "status": "pending", "chapters_done": 0, "chapters_total": 0},
    {"name": "大学物理", "status": "pending", "chapters_done": 0, "chapters_total": 0},
    {"name": "高等数学", "status": "pending", "chapters_done": 0, "chapters_total": 0},
]


def test_default_progress_includes_an_empty_queue():
    progress = ChaoxingLearningManager._default_progress()

    assert progress["courses"] == []
    # The scalar counters the existing UI reads must survive the addition.
    assert progress["completed_chapters"] == 0
    assert progress["total_chapters"] == 0


def test_update_course_entry_marks_only_the_targeted_course(monkeypatch):
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)

    manager._update_course_entry("t1", 2, status="running")

    courses = manager._tasks["t1"]["progress"]["courses"]
    assert [course["status"] for course in courses] == ["pending", "running", "pending"]
    # Order is preserved: index 2 is the second course, not the first.
    assert courses[1]["name"] == "大学物理"


def test_update_course_entry_ignores_out_of_range_and_missing_entries(monkeypatch):
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)

    # Must not raise, and must not corrupt the queue.
    manager._update_course_entry("t1", 99, status="completed")
    manager._update_course_entry("t1", 0, status="completed")
    manager._update_course_entry("missing-task", 1, status="completed")

    assert [c["status"] for c in manager._tasks["t1"]["progress"]["courses"]] == [
        "pending",
        "pending",
        "pending",
    ]


def test_mark_chapter_done_updates_both_counters(monkeypatch):
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)

    manager._mark_chapter_done("t1", 2)
    manager._mark_chapter_done("t1", 2)

    progress = manager._tasks["t1"]["progress"]
    # Global counter (existing UI) and the course's own counter stay in step.
    assert progress["completed_chapters"] == 2
    assert progress["courses"][1]["chapters_done"] == 2
    assert progress["courses"][0]["chapters_done"] == 0


def test_mark_chapter_done_is_safe_without_a_queue(monkeypatch):
    """A task restored from an older payload has no `courses` array."""
    manager = _make_manager(courses=[])
    _noop_store(monkeypatch)

    manager._mark_chapter_done("t1", 1)

    progress = manager._tasks["t1"]["progress"]
    assert progress["completed_chapters"] == 1
    assert progress["courses"] == []


def test_settle_course_entries_clears_pending_and_running_only(monkeypatch):
    manager = _make_manager(
        courses=[
            {"name": "A", "status": "completed", "chapters_done": 5, "chapters_total": 5},
            {"name": "B", "status": "running", "chapters_done": 2, "chapters_total": 5},
            {"name": "C", "status": "pending", "chapters_done": 0, "chapters_total": 0},
        ]
    )
    _noop_store(monkeypatch)

    manager._settle_course_entries("t1", "cancelled")

    statuses = [c["status"] for c in manager._tasks["t1"]["progress"]["courses"]]
    # A finished course keeps its result; the in-flight and waiting ones settle.
    assert statuses == ["completed", "cancelled", "cancelled"]


def test_settle_course_entries_does_not_rewrite_settled_entries(monkeypatch):
    manager = _make_manager(
        courses=[{"name": "A", "status": "failed", "chapters_done": 0, "chapters_total": 3}]
    )
    _noop_store(monkeypatch)
    writes: list[dict] = []
    monkeypatch.setattr(
        lm.task_store,
        "upsert_task",
        lambda kind, payload: writes.append(payload) or True,
    )

    manager._settle_course_entries("t1", "cancelled")

    assert manager._tasks["t1"]["progress"]["courses"][0]["status"] == "failed"
    # Nothing changed, so no persist should have been issued.
    assert writes == []


def test_cancel_task_settles_the_queue(monkeypatch):
    """A cancelled run must not leave the last course showing as running."""
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)

    manager._cancel_task("t1", "Task cancelled by user")

    task = manager._tasks["t1"]
    assert task["status"] == "cancelled"
    assert {c["status"] for c in task["progress"]["courses"]} == {"cancelled"}


def test_fail_task_settles_the_queue(monkeypatch):
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)

    manager._fail_task("t1", "Login failed")

    task = manager._tasks["t1"]
    assert task["status"] == "failed"
    assert {c["status"] for c in task["progress"]["courses"]} == {"failed"}


def test_queue_helpers_do_not_deadlock_when_task_is_absent(monkeypatch):
    """The lock must be released on every early-return path."""
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)

    manager._update_course_entry("nope", 1, status="running")
    manager._mark_chapter_done("nope", 1)
    manager._settle_course_entries("nope", "cancelled")

    # If any early return had leaked the lock, this would hang.
    assert manager._lock.acquire(timeout=1) is True
    manager._lock.release()


# ── finished_at (admin learning history) ──────────────────────────────────────


def test_finished_at_is_stamped_on_a_terminal_status(monkeypatch):
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)

    manager._update_task("t1", status="completed", message="done")

    assert manager._tasks["t1"]["finished_at"]


def test_finished_at_is_absent_while_the_task_is_still_active(monkeypatch):
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)

    manager._update_task("t1", status="running", message="working")
    manager._update_task("t1", status="paused", message="paused")

    assert "finished_at" not in manager._tasks["t1"]


def test_finished_at_is_not_overwritten_by_a_later_terminal_update(monkeypatch):
    """The first terminal transition wins, so the end time is stable."""
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)

    manager._update_task("t1", status="cancelled", message="cancelled")
    first = manager._tasks["t1"]["finished_at"]

    manager._update_task("t1", status="failed", message="late failure")

    assert manager._tasks["t1"]["finished_at"] == first


def test_finished_at_is_set_for_every_terminal_status(monkeypatch):
    for status in ("completed", "failed", "cancelled"):
        manager = _make_manager(courses=THREE_COURSES)
        _noop_store(monkeypatch)

        manager._update_task("t1", status=status, message=status)

        assert manager._tasks["t1"]["finished_at"], f"missing for {status}"


def test_cancel_and_fail_helpers_stamp_finished_at(monkeypatch):
    for helper in ("_cancel_task", "_fail_task"):
        manager = _make_manager(courses=THREE_COURSES)
        _noop_store(monkeypatch)

        getattr(manager, helper)("t1", "stopped")

        assert manager._tasks["t1"]["finished_at"], f"missing for {helper}"
