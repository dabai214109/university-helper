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


# ── soft delete (requirements: 任务队列 移除) ──────────────────────────────────


def test_hide_task_marks_the_row_hidden(monkeypatch):
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)
    manager._tasks["t1"]["status"] = "completed"

    result = manager.hide_task("u1", "t1")

    assert result["status"] == "hidden"
    assert manager._tasks["t1"]["hidden"] is True


def test_hidden_tasks_disappear_from_the_queue(monkeypatch):
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)
    manager._tasks["t1"]["status"] = "completed"
    manager.hide_task("u1", "t1")

    listed = manager.list_tasks("u1")

    assert [task["task_id"] for task in listed] == []


def test_hide_refuses_while_the_task_is_active(monkeypatch):
    """Removing a running task would orphan its worker, so it must be stopped first."""
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)
    manager._tasks["t1"]["status"] = "running"

    result = manager.hide_task("u1", "t1")

    assert result.get("code") == "invalid_status"
    assert not manager._tasks["t1"].get("hidden")


def test_hide_refuses_a_recurring_template(monkeypatch):
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)
    manager._tasks["t1"]["status"] = "recurring"

    result = manager.hide_task("u1", "t1")

    assert result.get("code") == "invalid_status"


def test_hide_rejects_another_users_task(monkeypatch):
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)
    manager._tasks["t1"]["status"] = "completed"

    result = manager.hide_task("intruder", "t1")

    assert result.get("code") == "not_found"
    assert not manager._tasks["t1"].get("hidden")


# ── cancel_all_for_user (requirements: 退出学习通) ────────────────────────────


def test_cancel_all_for_user_targets_only_that_user(monkeypatch):
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)
    manager._tasks["t1"]["status"] = "running"
    manager._tasks["other"] = {
        "task_id": "other",
        "user_id": "u2",
        "platform": "chaoxing",
        "status": "running",
        "message": "",
        "current_task": "",
        "progress": manager._default_progress(),
        "logs": [],
        "_log_cursor": 0,
    }

    cancelled = manager.cancel_all_for_user("u1")

    assert cancelled == 1
    assert manager._tasks["t1"]["status"] == "cancelling"
    # The other tenant's run must be untouched.
    assert manager._tasks["other"]["status"] == "running"


def test_cancel_all_for_user_includes_scheduled_and_recurring(monkeypatch):
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)
    manager._tasks["t1"]["status"] = "scheduled"
    manager._tasks["t1"]["schedule"] = {"repeat": "once", "start_at": None, "fire_at": None}
    manager._tasks["t2"] = {
        "task_id": "t2",
        "user_id": "u1",
        "platform": "chaoxing",
        "status": "recurring",
        "message": "",
        "current_task": "recurring",
        "progress": manager._default_progress(),
        "logs": [],
        "_log_cursor": 0,
        "schedule": {"repeat": "daily", "time_of_day": "08:00", "next_fire_at": None},
    }

    cancelled = manager.cancel_all_for_user("u1")

    assert cancelled == 2
    assert manager._tasks["t1"]["status"] == "cancelled"
    assert manager._tasks["t2"]["status"] == "cancelled"


def test_cancel_all_for_user_skips_finished_tasks(monkeypatch):
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)
    manager._tasks["t1"]["status"] = "completed"

    assert manager.cancel_all_for_user("u1") == 0


def test_cancel_all_for_user_survives_a_mutating_registry(monkeypatch):
    """The registry is snapshotted before cancelling (dict-mutation guard)."""
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)
    for index in range(5):
        manager._tasks[f"t{index}"] = {
            "task_id": f"t{index}",
            "user_id": "u1",
            "platform": "chaoxing",
            "status": "running",
            "message": "",
            "current_task": "",
            "progress": manager._default_progress(),
            "logs": [],
            "_log_cursor": 0,
        }

    cancelled = manager.cancel_all_for_user("u1")

    assert cancelled == 5


def test_cancel_all_for_user_is_a_noop_for_an_unknown_user(monkeypatch):
    manager = _make_manager(courses=THREE_COURSES)
    _noop_store(monkeypatch)

    assert manager.cancel_all_for_user("nobody") == 0
    assert manager.cancel_all_for_user("") == 0


# ── 任务级链式队列（已有任务在跑时排队） ──────────────────────────────────────


def _running_task(manager, task_id="t1", user="u1"):
    manager._tasks[task_id] = {
        "task_id": task_id,
        "user_id": user,
        "platform": "chaoxing",
        "status": "running",
        "message": "",
        "current_task": "",
        "progress": manager._default_progress(),
        "logs": [],
        "_log_cursor": 0,
    }
    return manager._tasks[task_id]


def test_queue_behind_attaches_to_the_active_task(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    _running_task(manager)

    queued_id = manager.start_task(
        "u1",
        {"platform": "chaoxing", "course_ids": ["1_2_3"], "queue_behind": True},
    )

    chain = manager._tasks["t1"].get("queued_next")
    assert isinstance(chain, list) and len(chain) == 1
    assert chain[0]["task_id"] == queued_id

    # 排队项必须是一条可寻址的真实记录。以前只有 id 挂在链上，于是 /resume
    # 和 /start-now 一律回 "Task not found"（那个 id 哪儿都不存在）。
    queued_task = manager._tasks[queued_id]
    assert queued_task["status"] == "queued"
    assert queued_task["user_id"] == "u1"
    assert queued_task["queue_anchor"] == "t1"
    assert "payload" not in queued_task, "启动参数留在链上，避免凭据重复落库"


def test_queue_behind_appends_in_order(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    _running_task(manager)

    first = manager.start_task("u1", {"platform": "chaoxing", "queue_behind": True})
    second = manager.start_task("u1", {"platform": "chaoxing", "queue_behind": True})

    chain = manager._tasks["t1"]["queued_next"]
    assert [entry["task_id"] for entry in chain] == [first, second]


def test_queue_behind_falls_back_when_the_anchor_finishes_mid_flight(monkeypatch):
    """前端以为有任务在跑、点了排队，但那一刻它刚好结束了。

    这时不能报错（用户的任务会凭空消失），必须降级成普通启动。
    用一个已结束的任务模拟「调度前恰好完成」的窗口。
    """
    manager = _make_manager()
    _noop_store(monkeypatch)
    # t1 已进入终态 —— 队列找不到可挂靠的活跃任务。
    manager._tasks["t1"]["status"] = "completed"
    monkeypatch.setattr(manager, "_run_task_worker_guarded", lambda *a, **k: None)

    task_id = manager.start_task("u1", {"platform": "chaoxing", "queue_behind": True})

    assert task_id in manager._tasks, "找不到活跃任务时必须降级为普通启动"
    assert manager._tasks[task_id]["status"] == "pending"


def test_finishing_a_task_starts_the_next_queued(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    _running_task(manager, "t1")

    queued = manager.start_task("u1", {"platform": "chaoxing", "queue_behind": True})
    assert manager._tasks["t1"]["queued_next"][0]["task_id"] == queued

    started: list[tuple[str, str]] = []
    monkeypatch.setattr(
        manager,
        "_run_task_worker_guarded",
        lambda task_id, user_id, payload: started.append((task_id, user_id)),
    )
    # 真实调用点在 worker 的 finally 里，此时锚任务已经是终态。
    manager._tasks["t1"]["status"] = "completed"

    manager._start_next_queued("t1", "u1")

    # 同一个 id 就地提升，不再另起一个新任务：前端看到的排队卡片直接变成进行中。
    assert started == [(queued, "u1")], "队列里的任务应该被启动一次"
    assert manager._tasks[queued]["status"] == "pending"
    assert "queue_anchor" not in manager._tasks[queued]
    # 队列已消费
    assert manager._tasks["t1"]["queued_next"] == []


def test_a_recurring_template_is_not_a_queue_anchor(monkeypatch):
    """模板是日程，不是正在跑的任务：挂在它下面的任务永远等不到启动。

    准入检查只认 active 状态，所以只有「检查时有活跃任务、入队时它恰好结束」
    这个竞态才会把模板当锚任务 —— 一旦发生，那个排队项就永久卡死。
    """
    manager = _make_manager()
    _noop_store(monkeypatch)
    manager._tasks.clear()
    manager._tasks["tpl"] = {
        "task_id": "tpl",
        "user_id": "u1",
        "platform": "chaoxing",
        "status": "recurring",
        "message": "",
        "current_task": "recurring",
        "progress": manager._default_progress(),
        "logs": [],
        "_log_cursor": 0,
    }
    monkeypatch.setattr(manager, "_run_task_worker_guarded", lambda *a, **k: None)

    task_id = manager.start_task("u1", {"platform": "chaoxing", "queue_behind": True})

    assert manager._tasks["tpl"].get("queued_next") in (None, []), "模板不该被当作锚任务"
    assert manager._tasks[task_id]["status"] == "pending", "必须降级成普通启动"


def test_stopping_a_queued_task_cancels_it_and_detaches_it(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    _running_task(manager, "t1")
    queued = manager.start_task("u1", {"platform": "chaoxing", "queue_behind": True})

    result = manager.stop_task("u1", queued)

    assert result["status"] == "cancelled"
    assert manager._tasks[queued]["status"] == "cancelled"
    assert manager._tasks[queued]["message"] == "Queued task cancelled"
    # 从链里摘掉，否则锚任务结束时它还会被拉起来
    assert manager._tasks["t1"]["queued_next"] == []


def test_stopping_the_anchor_cancels_its_queued_children(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    _running_task(manager, "t1")
    first = manager.start_task("u1", {"platform": "chaoxing", "queue_behind": True})
    second = manager.start_task("u1", {"platform": "chaoxing", "queue_behind": True})
    # 锚任务用「定时中」这条会走状态变更分支的路径，避免拉起真实的 worker 停止。
    manager._tasks["t1"]["status"] = "scheduled"

    manager.stop_task("u1", "t1")

    assert manager._tasks[first]["status"] == "cancelled"
    assert manager._tasks[second]["status"] == "cancelled"
    assert manager._tasks["t1"]["queued_next"] == []


def test_resume_and_pause_reject_a_queued_task(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    _running_task(manager, "t1")
    queued = manager.start_task("u1", {"platform": "chaoxing", "queue_behind": True})

    resumed = manager.resume_task("u1", queued)
    paused = manager.pause_task("u1", queued)

    # 409 语义（invalid_status），不是误导性的 404 "Task not found"
    assert resumed["status"] == "error"
    assert resumed["code"] == "invalid_status"
    assert paused["status"] == "error"
    assert paused["code"] == "invalid_status"


def test_restore_drops_queued_children_of_a_finished_anchor(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    _running_task(manager, "t1")
    queued = manager.start_task("u1", {"platform": "chaoxing", "queue_behind": True})
    # 服务重启：锚任务已经在停机前结束，收尾逻辑没机会跑
    manager._tasks["t1"]["status"] = "completed"

    with manager._lock:
        requests = manager._cancel_stalled_queued_locked()
    for request in requests:
        manager._persist_task_state(*request)

    assert manager._tasks[queued]["status"] == "cancelled"
    assert "gone" in manager._tasks[queued]["message"]


def test_hiding_a_queued_task_is_refused(monkeypatch):
    """隐身会让排队项从列表消失却仍在链里，之后突然开始、没人知道它哪来的。"""
    manager = _make_manager()
    _noop_store(monkeypatch)
    _running_task(manager, "t1")
    queued = manager.start_task("u1", {"platform": "chaoxing", "queue_behind": True})

    result = manager.hide_task("u1", queued)

    assert result["status"] == "error"
    assert result.get("code") == "invalid_status"
    assert manager._tasks[queued]["status"] == "queued"


def test_restore_drops_children_queued_behind_a_recurring_template(monkeypatch):
    """模板永远不会结束，挂在它下面的排队项等不到任何东西。

    这正是线上那个卡死的 252730c2：入队时抢在活跃任务结束的瞬间，锚定到了
    周期模板上，于是既不会启动、也无法取消。
    """
    manager = _make_manager()
    _noop_store(monkeypatch)
    manager._tasks.clear()
    manager._tasks["tpl"] = {
        "task_id": "tpl",
        "user_id": "u1",
        "platform": "chaoxing",
        "status": "recurring",
        "message": "",
        "current_task": "recurring",
        "progress": manager._default_progress(),
        "logs": [],
        "_log_cursor": 0,
        "queued_next": [
            {"task_id": "ghost", "payload": {"platform": "chaoxing"}, "repeat": "once", "enqueued_at": ""}
        ],
    }
    manager._tasks["ghost"] = {
        "task_id": "ghost",
        "user_id": "u1",
        "platform": "chaoxing",
        "status": "queued",
        "message": "",
        "current_task": "queued",
        "progress": manager._default_progress(),
        "logs": [],
        "_log_cursor": 0,
        "queue_anchor": "tpl",
    }

    with manager._lock:
        requests = manager._cancel_stalled_queued_locked()
    for request in requests:
        manager._persist_task_state(*request)

    assert manager._tasks["ghost"]["status"] == "cancelled"
    assert "gone" in manager._tasks["ghost"]["message"]


def test_a_live_anchor_keeps_its_queued_children(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    _running_task(manager, "t1")
    queued = manager.start_task("u1", {"platform": "chaoxing", "queue_behind": True})

    with manager._lock:
        requests = manager._cancel_stalled_queued_locked()

    assert requests == []
    assert manager._tasks[queued]["status"] == "queued"


def test_finishing_a_task_with_empty_queue_does_nothing(monkeypatch):
    manager = _make_manager()
    _noop_store(monkeypatch)
    _running_task(manager, "t1")
    started = []
    monkeypatch.setattr(manager, "start_task", lambda uid, p: started.append(1) or "x")

    manager._start_next_queued("t1", "u1")

    assert started == []


def test_queue_entries_survive_persistence(monkeypatch):
    """排队信息是存在前一个任务上的，跟着它一起落库/恢复。"""
    manager = _make_manager()
    saved: list[dict] = []
    monkeypatch.setattr(lm.task_store, "upsert_task",
                        lambda kind, payload: saved.append(payload) or True)
    _running_task(manager, "t1")

    manager.start_task("u1", {"platform": "chaoxing", "queue_behind": True})

    assert any(p.get("queued_next") for p in saved), "排队项必须写进持久化快照"


def test_non_queued_start_still_rejects_when_busy(monkeypatch):
    """不要求排队的普通启动，仍然保持 409 语义。"""
    manager = _make_manager()
    _noop_store(monkeypatch)
    _running_task(manager, "t1")

    try:
        manager.start_task("u1", {"platform": "chaoxing"})
    except Exception as exc:
        assert type(exc).__name__ == "TaskAlreadyActiveError"
    else:  # pragma: no cover
        raise AssertionError("expected TaskAlreadyActiveError")


def test_queued_payload_drops_the_queue_flag(monkeypatch):
    """queue_behind 只是准入提示，不能传给 worker 当作真实参数。"""
    manager = _make_manager()
    _noop_store(monkeypatch)
    _running_task(manager, "t1")

    manager.start_task("u1", {"platform": "chaoxing", "queue_behind": True, "speed": 1.5})

    entry = manager._tasks["t1"]["queued_next"][0]
    assert entry["payload"]["speed"] == 1.5
