import logging
import random
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.services.notification import NotificationFactory
from app.services.notification.providers import validate_notification_url

from ..task_store import task_store
from .cookie_vault import chaoxing_cookie_vault
from .endpoint_security import validate_tiku_config
from .learning import ChapterTask, JobProcessor, init_chaoxing
from .payload_mapper import normalize_tiku_config
from .recurrence import (
    REPEAT_ONCE,
    RecurrenceError,
    describe_schedule,
    first_occurrence,
    next_occurrence,
    normalize_repeat,
    parse_anchor_date,
    parse_time_of_day,
)
from .task_admission import (
    MAX_ACTIVE_TASKS,
    TaskAlreadyActiveError,
    TaskCapacityError,
    cleanup_task_records,
    count_active_tasks,
    is_active_status,
    is_recurring_status,
    is_terminal_status,
    sort_task_records,
)

logger = logging.getLogger(__name__)
LEARNING_TASK_KIND = "chaoxing_learning"
INTERRUPTED_STATUSES = {"running", "pending", "paused", "cancelling", "stopping"}
_NOTIFICATION_SERVICE_LABELS = frozenset({"ServerChan", "Qmsg", "Bark", "Telegram"})
RESTART_INTERRUPTED_MESSAGE = "Task interrupted due to service restart"
UNEXPECTED_WORKER_ERROR_PREFIX = "Unexpected task failure"
USER_TASK_LOAD_LIMIT = 2000
THREAD_START_FAILURE_MESSAGE = (
    "Server cannot start a new background thread. Stop existing tasks and retry, "
    "or restart the service if the problem persists."
)
TASK_PERSIST_FAILURE_MESSAGE = "Failed to persist learning task state"
# Minimum seconds between throttled (high-frequency progress) main-DB upserts of
# a single task's payload. Video progress callbacks fire ~1/sec per task and
# each upsert re-serializes the whole growing logs+progress payload as JSONB, so
# coalesce them. Status changes / logs / terminal writes bypass this throttle
# (force=True) so final state is never lost. (F31)
PROGRESS_PERSIST_INTERVAL = 5.0


class _TaskPersistSequencer:
    """Run one task's reserved snapshots in mutation order.

    Snapshot revisions are reserved while the manager lock still protects the
    corresponding state mutation. Store I/O happens after that lock is released,
    so unrelated tasks remain independent while concurrent callers for the same
    task cannot overtake one another.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._last_reserved_revision = 0
        self._last_finished_revision = 0

    def reserve(self) -> int:
        with self._condition:
            self._last_reserved_revision += 1
            return self._last_reserved_revision

    def run(self, revision: int, writer: Callable[[], None]) -> None:
        with self._condition:
            self._condition.wait_for(lambda: revision == self._last_finished_revision + 1)
        try:
            writer()
        finally:
            # A failed write must release the next revision. Persistence is
            # best-effort, and later snapshots still need a chance to recover.
            with self._condition:
                self._last_finished_revision = revision
                self._condition.notify_all()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _parse_course_selector(raw: str) -> tuple[str, str | None, str | None]:
    text = str(raw or "").strip()
    if not text:
        return "", None, None
    parts = [part for part in text.split("_") if part]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], None
    return parts[0], None, None


def _course_label(course: dict[str, Any]) -> str:
    return (
        str(course.get("title") or "").strip()
        or str(course.get("courseName") or "").strip()
        or f"{course.get('courseId', 'course')}"
    )


class ChaoxingLearningManager:
    """Background task manager for Chaoxing course-learning jobs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._loaded_task_users: set[str] = set()
        self._last_full_restore_ts = 0.0
        self._restore_tasks_from_store()

    def start_task(self, user_id: str, payload: dict[str, Any]) -> str:
        # Validate user-controlled answer-provider destinations before creating
        # persistent state or a worker thread.  The API maps the safe,
        # detail-free exception to a 4xx response; direct callers get the same
        # fail-closed behavior.
        validate_tiku_config((payload or {}).get("tiku_config"))

        normalized_user_id = str(user_id or "").strip()
        schedule_start_at = (payload or {}).get("start_at")
        raw_repeat = (payload or {}).get("repeat")
        try:
            repeat = normalize_repeat(raw_repeat)
        except RecurrenceError as exc:
            raise ValueError(str(exc)) from exc
        is_recurring = repeat != REPEAT_ONCE
        is_scheduled = (
            isinstance(schedule_start_at, str) and schedule_start_at.strip()
        ) or is_recurring
        with self._lock:
            cleanup_task_records(self._tasks)
            # A recurring template is NOT active, so it does not block the user
            # from running anything else — that is the whole point of keeping it
            # out of ACTIVE_TASK_STATUSES.
            if any(
                str(task.get("user_id") or "").strip() == normalized_user_id and is_active_status(task.get("status"))
                for task in self._tasks.values()
            ):
                raise TaskAlreadyActiveError()
            if count_active_tasks(self._tasks) >= MAX_ACTIVE_TASKS:
                raise TaskCapacityError()

            task_id = uuid4().hex
            pause_event = threading.Event()
            pause_event.set()
            stop_event = threading.Event()
            now = _utc_now_iso()
            base_progress = self._default_progress()
            if is_scheduled:
                schedule_dict = self._build_schedule_dict(payload, repeat=repeat, now_iso=now)
                credentials_dict: dict[str, Any] = {
                    "username": (payload or {}).get("username", ""),
                    "password": (payload or {}).get("password", ""),
                    "tiku_config": (payload or {}).get("tiku_config") or {},
                }
                schedule_args_dict: dict[str, Any] = {
                    "platform": (payload or {}).get("platform"),
                    "course_ids": (payload or {}).get("course_ids") or [],
                    "speed": (payload or {}).get("speed"),
                    "concurrency": (payload or {}).get("concurrency"),
                    "unopened_strategy": (payload or {}).get("unopened_strategy"),
                    "notify_config": (payload or {}).get("notify_config") or {},
                }
                if is_recurring:
                    task_state: dict[str, Any] = {
                        "task_id": task_id,
                        "user_id": user_id,
                        "platform": "chaoxing",
                        "status": "recurring",
                        "message": f"Recurring template ({describe_schedule(schedule_dict)})",
                        "current_task": "recurring",
                        "progress": base_progress,
                        "created_at": now,
                        "started_at": now,
                        "updated_at": now,
                        "logs": [],
                        "_log_cursor": 0,
                        "_pause_event": pause_event,
                        "_stop_event": stop_event,
                        "schedule": schedule_dict,
                        "credentials": credentials_dict,
                        "schedule_args": schedule_args_dict,
                    }
                else:
                    task_state: dict[str, Any] = {
                        "task_id": task_id,
                        "user_id": user_id,
                        "platform": "chaoxing",
                        "status": "scheduled",
                        "message": "Scheduled, waiting for dispatch",
                        "current_task": "scheduled",
                        "progress": base_progress,
                        "created_at": now,
                        "started_at": now,
                        "updated_at": now,
                        "logs": [],
                        "_log_cursor": 0,
                        "_pause_event": pause_event,
                        "_stop_event": stop_event,
                        "schedule": schedule_dict,
                        "credentials": credentials_dict,
                        "schedule_args": schedule_args_dict,
                    }
            else:
                task_state: dict[str, Any] = {
                    "task_id": task_id,
                    "user_id": user_id,
                    "platform": "chaoxing",
                    "status": "pending",
                    "message": "Task created",
                    "current_task": "preparing",
                    "progress": base_progress,
                    "created_at": now,
                    "started_at": now,
                    "updated_at": now,
                    "logs": [],
                    "_log_cursor": 0,
                    "_pause_event": pause_event,
                    "_stop_event": stop_event,
                }
            self._tasks[task_id] = task_state
            cleanup_task_records(self._tasks)
            persist_request = self._prepare_persist_locked(task_state)
        if persist_request:
            try:
                persisted = self._persist_task_state(*persist_request)
                if persisted is not True:
                    raise RuntimeError(TASK_PERSIST_FAILURE_MESSAGE)
            except Exception:
                self._record_admission_persist_failure(task_id)
                raise

        if is_scheduled:
            return task_id
        try:
            threading.Thread(
                target=self._run_task_worker_guarded,
                args=(task_id, user_id, dict(payload or {})),
                daemon=True,
            ).start()
        except Exception as exc:
            self._fail_task(task_id, THREAD_START_FAILURE_MESSAGE)
            raise RuntimeError(THREAD_START_FAILURE_MESSAGE) from exc
        return task_id

    def get_task(self, user_id: str, task_id: str) -> dict[str, Any] | None:
        normalized_user_id = str(user_id or "").strip()
        normalized_task_id = str(task_id or "").strip()
        with self._lock:
            cleanup_task_records(self._tasks)
            task = self._tasks.get(normalized_task_id)
            if task and str(task.get("user_id")) == normalized_user_id:
                return self._public_view(task)

        self._load_task_from_store(normalized_user_id, normalized_task_id)
        with self._lock:
            task = self._tasks.get(normalized_task_id)
            if not task or str(task.get("user_id")) != normalized_user_id:
                return None
            return self._public_view(task)

    @staticmethod
    def _build_schedule_dict(
        payload: dict[str, Any],
        *,
        repeat: str,
        now_iso: str,
    ) -> dict[str, Any]:
        """Assemble the persisted `schedule` block for a scheduled/recurring task.

        One-shot tasks keep the historical absolute `start_at` semantics; a
        recurring template stores its wall-clock rule plus the resolved next
        firing so the dispatcher does not have to recompute it every tick.
        """
        schedule: dict[str, Any] = {
            "start_at": (payload or {}).get("start_at"),
            "stop_at": (payload or {}).get("stop_at"),
            "start_jitter_min": int((payload or {}).get("start_jitter_min", 0) or 0),
            "stop_jitter_min": int((payload or {}).get("stop_jitter_min", 0) or 0),
            "fire_at": None,
            "actual_stop_at": None,
            "repeat": repeat,
        }
        if repeat == REPEAT_ONCE:
            return schedule

        time_of_day = parse_time_of_day((payload or {}).get("time_of_day"))
        anchor_date = parse_anchor_date((payload or {}).get("anchor_date"))
        schedule["time_of_day"] = time_of_day.strftime("%H:%M")
        schedule["anchor_date"] = anchor_date.isoformat() if anchor_date else None
        schedule["max_duration_min"] = (payload or {}).get("max_duration_min")

        now_dt = datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=UTC)
        schedule["next_fire_at"] = first_occurrence(
            repeat, time_of_day, now=now_dt, anchor_date=anchor_date
        ).isoformat()
        schedule["last_fired_at"] = None
        schedule["fired_count"] = 0
        return schedule

    def _spawn_recurring_child_locked(
        self,
        *,
        template: dict[str, Any],
        payload: dict[str, Any],
        schedule: dict[str, Any],
        now: datetime,
    ) -> str | None:
        """Create the child task for one recurring occurrence. Caller holds the lock.

        The child is a normal task (status ``pending``) so it runs through the
        existing worker path and shows up in the user's task list. Admission
        checks are deliberately NOT re-run here: the occurrence is due, and the
        user's own one-active-task rule must not silently swallow a scheduled
        run. Capacity is still respected.
        """
        if count_active_tasks(self._tasks) >= MAX_ACTIVE_TASKS:
            logger.warning(
                "recurring occurrence skipped: task capacity reached (template=%s)",
                template.get("task_id"),
            )
            return None

        child_id = uuid4().hex
        pause_event = threading.Event()
        pause_event.set()
        now_iso = now.isoformat()
        child_schedule: dict[str, Any] = {
            "repeat": REPEAT_ONCE,
            "start_at": None,
            "stop_at": None,
            "start_jitter_min": 0,
            "stop_jitter_min": 0,
            "fire_at": None,
            "actual_stop_at": None,
            # Lineage: lets the admin console group a run back to its schedule.
            "parent_task_id": str(template.get("task_id") or ""),
        }
        max_duration = schedule.get("max_duration_min")
        if max_duration:
            try:
                child_schedule["actual_stop_at"] = (
                    now + timedelta(minutes=int(max_duration))
                ).isoformat()
            except (TypeError, ValueError):
                pass

        child: dict[str, Any] = {
            "task_id": child_id,
            "user_id": str(template.get("user_id") or ""),
            "platform": "chaoxing",
            "status": "pending",
            "message": "Recurring occurrence starting",
            "current_task": "preparing",
            "progress": self._default_progress(),
            "created_at": now_iso,
            "started_at": now_iso,
            "updated_at": now_iso,
            "logs": [],
            "_log_cursor": 0,
            "_pause_event": pause_event,
            "_stop_event": threading.Event(),
            "schedule": child_schedule,
        }
        self._tasks[child_id] = child
        return child_id

    def list_tasks(self, user_id: str) -> list[dict[str, Any]]:
        self._ensure_tasks_loaded_for_user(user_id)
        with self._lock:
            cleanup_task_records(self._tasks)
            tasks: list[dict[str, Any]] = []
            for task in self._tasks.values():
                if str(task.get("user_id")) != str(user_id):
                    continue
                public_task = self._public_view(task)
                public_task.pop("user_id", None)
                started_at = (
                    public_task.get("started_at") or public_task.get("start_time") or public_task.get("created_at")
                )
                if started_at:
                    public_task["started_at"] = started_at
                if not public_task.get("updated_at") and started_at:
                    public_task["updated_at"] = started_at
                tasks.append(public_task)
        return sort_task_records(tasks)

    def get_task_logs(self, user_id: str, task_id: str, cursor: int | None = None) -> dict[str, Any] | None:
        normalized_user_id = str(user_id or "").strip()
        normalized_task_id = str(task_id or "").strip()
        with self._lock:
            cleanup_task_records(self._tasks)
            task = self._tasks.get(normalized_task_id)
            if task and str(task.get("user_id")) == normalized_user_id:
                start = int(cursor) if cursor is not None else int(task.get("_log_cursor", 0))
                logs = list(task.get("logs", [])[start:])
                next_cursor = len(task.get("logs", []))
                if cursor is None:
                    task["_log_cursor"] = next_cursor
                return {"logs": logs, "cursor": next_cursor}

        self._load_task_from_store(normalized_user_id, normalized_task_id)
        with self._lock:
            cleanup_task_records(self._tasks)
            task = self._tasks.get(normalized_task_id)
            if not task or str(task.get("user_id")) != normalized_user_id:
                return None
            start = int(cursor) if cursor is not None else int(task.get("_log_cursor", 0))
            logs = list(task.get("logs", [])[start:])
            next_cursor = len(task.get("logs", []))
            if cursor is None:
                task["_log_cursor"] = next_cursor
            return {"logs": logs, "cursor": next_cursor}

    def pause_task(self, user_id: str, task_id: str) -> dict[str, Any]:
        normalized_user_id = str(user_id or "").strip()
        normalized_task_id = str(task_id or "").strip()
        with self._lock:
            task = self._tasks.get(normalized_task_id)
            if task and str(task.get("user_id")) == normalized_user_id:
                pass
            else:
                task = None
        if task is None:
            self._load_task_from_store(normalized_user_id, normalized_task_id)
        with self._lock:
            task = self._tasks.get(normalized_task_id)
            if not task or str(task.get("user_id")) != normalized_user_id:
                return {"status": "error", "message": "Task not found"}
            if task.get("status") in {"completed", "failed", "error", "cancelled"}:
                return {"status": task.get("status", "completed"), "message": "Task already finished"}
            if task.get("status") == "scheduled":
                return {
                    "status": "error",
                    "message": "Scheduled task: use stop or reschedule",
                    "code": "invalid_status",
                }
            if is_recurring_status(task.get("status")):
                # A template has no run in flight to pause; pausing it would
                # overwrite the `recurring` status and silently destroy the
                # schedule. Stopping the template is the supported action.
                return {
                    "status": "error",
                    "message": "Recurring task: stop the schedule instead",
                    "code": "invalid_status",
                }
            pause_event: threading.Event = task["_pause_event"]
            pause_event.clear()
            task["status"] = "paused"
            task["message"] = "Task paused"
            task["updated_at"] = _utc_now_iso()
        self._append_task_log(task_id, "Task paused", "warning")
        return {"status": "paused", "message": "Task paused"}

    def resume_task(self, user_id: str, task_id: str) -> dict[str, Any]:
        normalized_user_id = str(user_id or "").strip()
        normalized_task_id = str(task_id or "").strip()
        with self._lock:
            task = self._tasks.get(normalized_task_id)
            if task and str(task.get("user_id")) == normalized_user_id:
                pass
            else:
                task = None
        if task is None:
            self._load_task_from_store(normalized_user_id, normalized_task_id)
        with self._lock:
            task = self._tasks.get(normalized_task_id)
            if not task or str(task.get("user_id")) != normalized_user_id:
                return {"status": "error", "message": "Task not found"}
            if task.get("status") in {"completed", "failed", "error", "cancelled"}:
                return {"status": task.get("status", "completed"), "message": "Task already finished"}
            if is_recurring_status(task.get("status")):
                # "Resuming" a template would flip it to `running`, which the
                # dispatcher no longer recognises as a schedule.
                return {
                    "status": "error",
                    "message": "Recurring task: it is always active; stop it to cancel",
                    "code": "invalid_status",
                }
            pause_event: threading.Event = task["_pause_event"]
            pause_event.set()
            task["status"] = "running"
            task["message"] = "Task resumed"
            task["updated_at"] = _utc_now_iso()
        self._append_task_log(task_id, "Task resumed", "info")
        return {"status": "running", "message": "Task resumed"}

    def stop_task(self, user_id: str, task_id: str) -> dict[str, Any]:
        normalized_user_id = str(user_id or "").strip()
        normalized_task_id = str(task_id or "").strip()
        with self._lock:
            task = self._tasks.get(normalized_task_id)
            if task and str(task.get("user_id")) == normalized_user_id:
                pass
            else:
                task = None
        if task is None:
            self._load_task_from_store(normalized_user_id, normalized_task_id)
        persist_request = None
        is_scheduled_cancel = False
        with self._lock:
            task = self._tasks.get(normalized_task_id)
            if not task or str(task.get("user_id")) != normalized_user_id:
                return {"status": "error", "message": "Task not found"}
            if task.get("status") in {"completed", "failed", "error", "cancelled"}:
                return {"status": task.get("status", "completed"), "message": "Task already finished"}
            if task.get("status") == "scheduled" or is_recurring_status(task.get("status")):
                # Both a pending one-shot task and a recurring template are
                # "waiting" states: cancelling them is a state change, not a
                # cooperative stop of a running worker. This is also the only way
                # to delete a recurring schedule.
                was_recurring = is_recurring_status(task.get("status"))
                task["status"] = "cancelled"
                task["message"] = (
                    "Recurring schedule cancelled" if was_recurring else "Scheduled task cancelled"
                )
                task["current_task"] = "cancelled"
                task["updated_at"] = _utc_now_iso()
                task.setdefault("finished_at", task["updated_at"])
                persist_request = self._prepare_persist_locked(task)
                is_scheduled_cancel = True
                cancel_message = task["message"]
            else:
                stop_event: threading.Event = task["_stop_event"]
                pause_event: threading.Event = task["_pause_event"]
                stop_event.set()
                pause_event.set()
                if task.get("status") not in {"completed", "failed", "error", "cancelled"}:
                    task["status"] = "cancelling"
                    task["message"] = "Task cancellation requested"
                task["updated_at"] = _utc_now_iso()
                persist_request = self._prepare_persist_locked(task)
        if persist_request:
            self._persist_task_state(*persist_request)
        if is_scheduled_cancel:
            self._append_task_log(task_id, cancel_message, "warning")
            return {"status": "cancelled", "message": cancel_message}
        self._append_task_log(task_id, "Task cancellation requested", "warning")
        return {"status": "cancelling", "message": "Task cancellation requested"}

    def reschedule_task(
        self,
        user_id: str,
        task_id: str,
        start_at_iso: str,
        stop_at_iso: str | None = None,
        start_jitter: int = 0,
        stop_jitter: int = 0,
    ) -> dict[str, Any]:
        normalized_user_id = str(user_id or "").strip()
        normalized_task_id = str(task_id or "").strip()
        with self._lock:
            task = self._tasks.get(normalized_task_id)
            if task and str(task.get("user_id")) == normalized_user_id:
                pass
            else:
                task = None
        if task is None:
            self._load_task_from_store(normalized_user_id, normalized_task_id)
        with self._lock:
            task = self._tasks.get(normalized_task_id)
            if not task or str(task.get("user_id")) != normalized_user_id:
                return {"status": "error", "message": "Task not found", "code": "not_found"}
            if task.get("status") != "scheduled":
                return {
                    "status": "error",
                    "message": "Only scheduled tasks can be rescheduled",
                    "code": "invalid_status",
                }
            schedule = task.get("schedule")
            if not isinstance(schedule, dict):
                schedule = {}
            schedule["start_at"] = str(start_at_iso)
            if stop_at_iso is not None:
                schedule["stop_at"] = stop_at_iso
            else:
                schedule.pop("stop_at", None)
            schedule["start_jitter_min"] = max(0, min(int(start_jitter or 0), 720))
            schedule["stop_jitter_min"] = max(0, min(int(stop_jitter or 0), 720))
            schedule["fire_at"] = None
            schedule["actual_stop_at"] = None
            task["schedule"] = schedule
            task["message"] = "Scheduled task rescheduled"
            task["updated_at"] = _utc_now_iso()
            persist_request = self._prepare_persist_locked(task)
        if persist_request:
            self._persist_task_state(*persist_request)
        self._append_task_log(task_id, "Task rescheduled", "info")
        return {"status": "scheduled", "message": "Task rescheduled"}

    def start_now_task(self, user_id: str, task_id: str) -> dict[str, Any]:
        normalized_user_id = str(user_id or "").strip()
        normalized_task_id = str(task_id or "").strip()
        with self._lock:
            task = self._tasks.get(normalized_task_id)
            if task and str(task.get("user_id")) == normalized_user_id:
                pass
            else:
                task = None
        if task is None:
            self._load_task_from_store(normalized_user_id, normalized_task_id)

        child_to_start: tuple[str, str, dict[str, Any]] | None = None
        with self._lock:
            task = self._tasks.get(normalized_task_id)
            if not task or str(task.get("user_id")) != normalized_user_id:
                return {"status": "error", "message": "Task not found", "code": "not_found"}

            status = str(task.get("status") or "").lower()
            if is_recurring_status(status):
                # "Run once now" must not consume the schedule: it spawns the
                # same child task a due occurrence would, and leaves the
                # template's next_fire_at untouched.
                schedule = task.get("schedule")
                if not isinstance(schedule, dict):
                    schedule = {}
                creds = task.get("credentials")
                if not isinstance(creds, dict):
                    creds = {}
                sched_args = task.get("schedule_args")
                if not isinstance(sched_args, dict):
                    sched_args = {}
                child_payload = dict(sched_args)
                child_payload.update(creds)
                child_id = self._spawn_recurring_child_locked(
                    template=task,
                    payload=child_payload,
                    schedule=schedule,
                    now=datetime.now(UTC),
                )
                if child_id is None:
                    return {
                        "status": "error",
                        "message": "Task capacity reached; retry later",
                        "code": "capacity",
                    }
                child_task = self._tasks.get(child_id)
                if child_task is not None:
                    persist_request = self._prepare_persist_locked(child_task)
                else:  # pragma: no cover - defensive
                    persist_request = None
                child_to_start = (child_id, normalized_user_id, child_payload)
            elif status == "scheduled":
                schedule = task.get("schedule")
                if not isinstance(schedule, dict):
                    schedule = {}
                schedule["fire_at"] = _utc_now_iso()
                task["schedule"] = schedule
                task["message"] = "Scheduled task starting now"
                task["updated_at"] = _utc_now_iso()
                persist_request = self._prepare_persist_locked(task)
            else:
                return {
                    "status": "error",
                    "message": "Only scheduled tasks can be started now",
                    "code": "invalid_status",
                }

        if persist_request:
            self._persist_task_state(*persist_request)

        if child_to_start is not None:
            child_id, uid, payload = child_to_start
            try:
                threading.Thread(
                    target=self._run_task_worker_guarded,
                    args=(child_id, uid, payload),
                    daemon=True,
                ).start()
            except Exception as exc:
                self._fail_task(child_id, THREAD_START_FAILURE_MESSAGE)
                logger.exception("start-now: failed to start worker for %s: %s", child_id, exc)
                return {
                    "status": "error",
                    "message": THREAD_START_FAILURE_MESSAGE,
                    "code": "thread_start_failed",
                }
            self._append_task_log(
                task_id, f"Recurring occurrence started manually → task {child_id}", "info"
            )
            return {
                "status": "pending",
                "message": "Recurring occurrence started",
                "child_task_id": child_id,
            }

        self._append_task_log(task_id, "Task start-now requested", "info")
        return {"status": "scheduled", "message": "Task will start immediately"}

    def dispatch_scheduled_tasks(self) -> None:
        """Scan & fire scheduled tasks, auto-pause at stop_at — called every 15s.

        Lock-held scan:
          1. scheduled+fire_at=None → resolve jitter, set fire_at.
          2. scheduled+fire_at<=now → rebuild payload, status→pending, collect for worker start.
          3. (running|pending)+actual_stop_at<=now → pause via event clear.
          4. Every 60s: full restore from store to recover from restart gaps.

        IMPORTANT: _append_task_log acquires self._lock, so we must NOT call it
        while holding the lock. Instead we collect log entries and write them
        after the lock is released.
        """
        pending_logs: list[tuple[str, str, str]] = []  # (task_id, message, level)
        to_fire: list[tuple[str, str, dict[str, Any]]] = []
        to_persist: list[tuple[dict[str, Any], _TaskPersistSequencer, int]] = []
        should_restore = False

        with self._lock:
            cleanup_task_records(self._tasks)
            restore_key = "_last_full_restore_ts"
            now_ts = time.time()
            if now_ts - float(getattr(self, restore_key, 0) or 0) >= 60:
                should_restore = True
                setattr(self, restore_key, now_ts)

            # Snapshot the values: the recurring branch below ADDS child tasks to
            # self._tasks, and mutating a dict while iterating it raises
            # RuntimeError. A child created now is picked up on the next tick.
            for task in list(self._tasks.values()):
                status = str(task.get("status") or "").lower()
                schedule = task.get("schedule")
                if not isinstance(schedule, dict):
                    continue

                task_id = str(task.get("task_id") or "")
                uid = str(task.get("user_id") or "")

                if status == "recurring":
                    # A template is due when its wall-clock slot has arrived. On
                    # each firing it spawns a SEPARATE child task, so per-run
                    # history stays intact and the template keeps living.
                    next_fire_raw = schedule.get("next_fire_at")
                    if not next_fire_raw:
                        continue
                    try:
                        next_fire_dt = datetime.fromisoformat(
                            str(next_fire_raw).strip().replace("Z", "+00:00")
                        )
                        if next_fire_dt.tzinfo is None:
                            next_fire_dt = next_fire_dt.replace(tzinfo=UTC)
                    except (ValueError, TypeError, OverflowError):
                        continue

                    if next_fire_dt > datetime.now(UTC):
                        continue

                    creds = task.get("credentials")
                    has_password = (
                        isinstance(creds, dict)
                        and creds.get("username")
                        and creds.get("password")
                    )
                    has_qr_session = bool(chaoxing_cookie_vault.get(uid))
                    if not has_password and not has_qr_session:
                        # Skip this occurrence rather than killing the schedule:
                        # the user may re-scan and later runs should still work.
                        pending_logs.append(
                            (
                                task_id,
                                "Recurring occurrence skipped: missing credentials "
                                "(re-scan the QR code or provide a password)",
                                "warning",
                            )
                        )
                    else:
                        sched_args = task.get("schedule_args")
                        if not isinstance(sched_args, dict):
                            sched_args = {}
                        child_payload = dict(sched_args)
                        child_payload.update(creds or {})
                        child_id = self._spawn_recurring_child_locked(
                            template=task,
                            payload=child_payload,
                            schedule=schedule,
                            now=datetime.now(UTC),
                        )
                        if child_id:
                            to_fire.append((child_id, uid, child_payload))
                            # Persist the child before its worker starts, exactly
                            # as the one-shot path persists the fired task.
                            child_task = self._tasks.get(child_id)
                            if child_task is not None:
                                pr_child = self._prepare_persist_locked(child_task)
                                if pr_child:
                                    to_persist.append(pr_child)
                            pending_logs.append(
                                (
                                    task_id,
                                    f"Recurring occurrence fired → task {child_id}",
                                    "info",
                                )
                            )

                    # Advance the template regardless: skipping an occurrence must
                    # not leave it stuck on a past slot.
                    try:
                        time_of_day = parse_time_of_day(schedule.get("time_of_day"))
                        next_dt = next_occurrence(
                            str(schedule.get("repeat") or "daily"),
                            next_fire_dt,
                            time_of_day,
                        )
                    except RecurrenceError:
                        continue
                    # Never queue a backlog: if the service was down for days,
                    # jump straight to the next future slot instead of firing
                    # every missed occurrence at once.
                    now_dt = datetime.now(UTC)
                    while next_dt <= now_dt:
                        try:
                            next_dt = next_occurrence(
                                str(schedule.get("repeat") or "daily"),
                                next_dt,
                                parse_time_of_day(schedule.get("time_of_day")),
                            )
                        except RecurrenceError:
                            break
                    schedule["next_fire_at"] = next_dt.isoformat()
                    schedule["last_fired_at"] = _utc_now_iso()
                    schedule["fired_count"] = int(schedule.get("fired_count") or 0) + 1
                    task["updated_at"] = _utc_now_iso()
                    task["message"] = (
                        f"Recurring template ({describe_schedule(schedule)}) — "
                        f"next {next_dt.isoformat()}"
                    )
                    pr = self._prepare_persist_locked(task)
                    if pr:
                        to_persist.append(pr)
                    continue

                if status == "scheduled":
                    if schedule.get("fire_at") is None:
                        try:
                            start_dt = datetime.fromisoformat(
                                str(schedule["start_at"]).strip().replace("Z", "+00:00")
                            )
                            if start_dt.tzinfo is None:
                                start_dt = start_dt.replace(tzinfo=UTC)
                            jitter_min = max(0, int(schedule.get("start_jitter_min", 0) or 0))
                            jitter_s = random.uniform(-jitter_min * 60, jitter_min * 60)
                            fire_dt = start_dt + timedelta(seconds=jitter_s)
                            schedule["fire_at"] = fire_dt.isoformat()
                        except (ValueError, TypeError, OverflowError):
                            schedule["fire_at"] = _utc_now_iso()

                    try:
                        fire_dt = datetime.fromisoformat(
                            str(schedule["fire_at"]).replace("Z", "+00:00")
                        )
                        if fire_dt.tzinfo is None:
                            fire_dt = fire_dt.replace(tzinfo=UTC)
                    except (ValueError, TypeError, OverflowError):
                        fire_dt = datetime.now(UTC)

                    if fire_dt <= datetime.now(UTC):
                        stop_at_raw = schedule.get("stop_at")
                        actual_stop = None
                        if stop_at_raw and str(stop_at_raw).strip():
                            try:
                                stop_dt = datetime.fromisoformat(
                                    str(stop_at_raw).strip().replace("Z", "+00:00")
                                )
                                if stop_dt.tzinfo is None:
                                    stop_dt = stop_dt.replace(tzinfo=UTC)
                                stop_jitter = max(0, int(schedule.get("stop_jitter_min", 0) or 0))
                                jitter_s2 = random.uniform(-stop_jitter * 60, stop_jitter * 60)
                                actual_stop = max(
                                    stop_dt + timedelta(seconds=jitter_s2),
                                    datetime.now(UTC) + timedelta(seconds=60),
                                )
                            except (ValueError, TypeError, OverflowError):
                                pass
                        schedule["actual_stop_at"] = (
                            actual_stop.isoformat() if actual_stop else None
                        )

                        task["status"] = "pending"
                        task["message"] = "Scheduled task starting"
                        task["updated_at"] = _utc_now_iso()
                        pending_logs.append((task_id, f"Scheduled task fired at {_utc_now_iso()}", "info"))

                        creds = task.get("credentials")
                        has_password = (
                            isinstance(creds, dict)
                            and creds.get("username")
                            and creds.get("password")
                        )
                        # A QR-scanned user has no stored password; their cookie
                        # jar may still be live in the vault after a restart.
                        has_qr_session = bool(chaoxing_cookie_vault.get(uid))
                        if not has_password and not has_qr_session:
                            # Mark failed without calling _fail_task (which tries to lock)
                            task["status"] = "failed"
                            task["message"] = (
                                "Scheduled task cannot start after restart: "
                                "missing credentials (re-scan the QR code or provide a password)"
                            )
                            task["current_task"] = "failed"
                            task["updated_at"] = _utc_now_iso()
                            task.setdefault("finished_at", task["updated_at"])
                            pending_logs.append(
                                (task_id, task["message"], "error")
                            )
                            pr = self._prepare_persist_locked(task)
                            if pr:
                                to_persist.append(pr)
                            continue

                        sched_args = task.get("schedule_args")
                        if not isinstance(sched_args, dict):
                            sched_args = {}
                        payload = dict(sched_args)
                        payload.update(creds)
                        to_fire.append((task_id, uid, payload))
                        pr = self._prepare_persist_locked(task)
                        if pr:
                            to_persist.append(pr)
                        continue

                if status in ("running", "pending"):
                    actual_stop_raw = schedule.get("actual_stop_at")
                    if actual_stop_raw and str(actual_stop_raw).strip():
                        try:
                            actual_stop_dt = datetime.fromisoformat(
                                str(actual_stop_raw).strip().replace("Z", "+00:00")
                            )
                            if actual_stop_dt.tzinfo is None:
                                actual_stop_dt = actual_stop_dt.replace(tzinfo=UTC)
                            if actual_stop_dt <= datetime.now(UTC):
                                pause_event = task.get("_pause_event")
                                if isinstance(pause_event, threading.Event):
                                    pause_event.clear()
                                task["status"] = "paused"
                                task["message"] = "Stopped by schedule (stop_at reached)"
                                task["updated_at"] = _utc_now_iso()
                                pending_logs.append(
                                    (task_id, "Auto-paused by schedule stop_at", "info")
                                )
                                pr = self._prepare_persist_locked(task)
                                if pr:
                                    to_persist.append(pr)
                        except (ValueError, TypeError, OverflowError):
                            pass

            # Persist inside the lock since these are reserved snapshots
            for pr in to_persist:
                try:
                    self._persist_task_state(*pr)
                except Exception:
                    logger.warning("dispatch persist failed")

        # Lock-free: write logs
        for log_task_id, log_msg, log_level in pending_logs:
            self._append_task_log(log_task_id, log_msg, log_level)

        # Lock-free: start worker threads
        for task_id, uid, payload in to_fire:
            try:
                threading.Thread(
                    target=self._run_task_worker_guarded,
                    args=(task_id, uid, payload),
                    daemon=True,
                ).start()
            except Exception as exc:
                self._fail_task(task_id, THREAD_START_FAILURE_MESSAGE)
                logger.exception("dispatch: failed to start worker for %s: %s", task_id, exc)

        # Periodic full restore
        if should_restore:
            try:
                self._restore_tasks_from_store()
            except Exception:
                logger.exception("dispatch: periodic restore failed")

    def _run_task_worker(self, task_id: str, user_id: str, payload: dict[str, Any]) -> None:
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "").strip()
        # A QR-scanned session lives in the vault, so a password is optional.
        # The cookie jar is deliberately NOT persisted with the task: it is a
        # live session credential and the vault is the single source of truth.
        cookies = payload.get("cookies")
        if not isinstance(cookies, dict) or not cookies:
            cookies = chaoxing_cookie_vault.get(user_id) or {}
        if not (username and password) and not cookies:
            self._fail_task(
                task_id,
                "Missing credentials: sign in with a QR code or provide an account password",
            )
            return

        course_list = payload.get("course_ids") or payload.get("course_list") or []
        if not isinstance(course_list, list):
            course_list = []

        common_config = {
            "username": username,
            "password": password,
            "course_list": [str(item) for item in course_list if str(item).strip()],
            "speed": _as_float(payload.get("speed"), default=1.5, minimum=1.0, maximum=2.0),
            "jobs": _as_int(
                payload.get("concurrency", payload.get("jobs")),
                default=4,
                minimum=1,
                maximum=16,
            ),
            "notopen_action": str(payload.get("unopened_strategy") or payload.get("notopen_action") or "retry")
            .strip()
            .lower(),
            "use_cookies": bool(cookies),
            "cookies": cookies,
        }
        if common_config["notopen_action"] not in {"retry", "ask", "continue"}:
            common_config["notopen_action"] = "retry"

        tiku_config = normalize_tiku_config(payload.get("tiku_config"))
        notify_config = payload.get("notify_config")
        if not isinstance(notify_config, dict):
            notify_config = {}

        stop_event, pause_event = self._control_events(task_id)
        if stop_event is None or pause_event is None:
            return

        self._update_task(
            task_id,
            status="running",
            message="Logging in to Chaoxing",
            current_task="login",
        )
        self._append_task_log(task_id, "Starting login...", "info")

        try:
            chaoxing = init_chaoxing(common_config, tiku_config)
        except Exception as exc:
            self._fail_task(task_id, f"Initialize chaoxing client failed: {exc}")
            return

        try:
            login_state = chaoxing.login_with_cookie_jar(cookies) if cookies else chaoxing.login(
                login_with_cookies=False
            )
        except Exception as exc:
            self._fail_task(task_id, f"Login request failed: {exc}")
            return

        if not login_state.get("status"):
            self._fail_task(task_id, login_state.get("msg") or login_state.get("message") or "Login failed")
            return

        self._append_task_log(task_id, "Login successful", "success")

        try:
            all_courses = chaoxing.get_course_list()
        except Exception as exc:
            self._fail_task(task_id, f"Fetch course list failed: {exc}")
            return

        selected_courses = self._select_courses(all_courses, common_config["course_list"])
        if not selected_courses:
            self._fail_task(task_id, "No available courses after filtering")
            return

        # Seed the queue view with every selected course, in FIFO order. Names
        # are available here, so the page can render the whole queue before the
        # first course's chapters are even fetched.
        self._update_progress(
            task_id,
            total=len(selected_courses),
            completed=0,
            failed=0,
            current=0,
            total_chapters=0,
            completed_chapters=0,
            current_course="",
            current_chapter="",
            video_progress=None,
            courses=[
                {
                    "name": _course_label(course),
                    "status": "pending",
                    "chapters_done": 0,
                    "chapters_total": 0,
                }
                for course in selected_courses
            ],
        )
        self._append_task_log(
            task_id,
            f"Selected {len(selected_courses)} courses, speed {common_config['speed']}x, jobs {common_config['jobs']}",
            "info",
        )

        completed_courses = 0
        failed_courses = 0

        for index, course in enumerate(selected_courses, start=1):
            if stop_event.is_set():
                self._cancel_task(task_id, "Task cancelled by user")
                return

            if not self._wait_for_resume(task_id, pause_event, stop_event):
                self._cancel_task(task_id, "Task cancelled by user")
                return

            course_name = _course_label(course)
            self._update_task(
                task_id,
                status="running",
                message=f"Learning course {index}/{len(selected_courses)}",
                current_task=f"course:{course_name}",
            )
            self._update_progress(
                task_id,
                current=index,
                current_course=course_name,
                current_chapter="",
                video_progress=None,
            )
            self._update_course_entry(task_id, index, status="running")
            self._append_task_log(task_id, f"Start course: {course_name}", "info")

            try:
                points_payload = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
                points = list(points_payload.get("points") or [])
            except Exception as exc:
                failed_courses += 1
                self._append_task_log(task_id, f"Fetch chapters failed for {course_name}: {exc}", "error")
                self._update_progress(task_id, failed=failed_courses, current=index)
                self._update_course_entry(task_id, index, status="failed")
                continue

            if points:
                self._increase_progress(task_id, "total_chapters", len(points))
                self._update_course_entry(task_id, index, chapters_total=len(points))

            callback_lock = threading.Lock()
            last_video_tick = {"ts": 0.0}

            def chapter_start_callback(_: dict[str, Any], point: dict[str, Any]) -> None:
                if stop_event.is_set():
                    return
                if not pause_event.is_set():
                    self._wait_for_resume(task_id, pause_event, stop_event)
                self._update_progress(
                    task_id,
                    current_course=course_name,
                    current_chapter=str(point.get("title") or ""),
                )
                self._update_task(
                    task_id,
                    current_task=f"chapter:{point.get('title', '')}",
                )

            def chapter_done_callback(
                _: dict[str, Any],
                point: dict[str, Any],
                course_index: int = index,
            ) -> None:
                del point
                self._mark_chapter_done(task_id, course_index)

            def video_progress_callback(
                _: dict[str, Any],
                job: dict[str, Any],
                play_time: float,
                duration: float,
            ) -> None:
                if stop_event.is_set():
                    return
                if not pause_event.is_set():
                    self._wait_for_resume(task_id, pause_event, stop_event)
                now = time.time()
                with callback_lock:
                    if now - last_video_tick["ts"] < 0.8:
                        return
                    last_video_tick["ts"] = now
                self._update_progress(
                    task_id,
                    video_progress={
                        "name": str(job.get("name") or ""),
                        "current": round(float(play_time), 1),
                        "duration": round(float(duration), 1),
                    },
                )

            chapter_tasks = [ChapterTask(point=point, index=i) for i, point in enumerate(points)]
            run_config = dict(common_config)
            run_config["chapter_start_callback"] = chapter_start_callback
            run_config["chapter_done_callback"] = chapter_done_callback
            run_config["video_progress_callback"] = video_progress_callback
            run_config["should_stop"] = stop_event.is_set

            try:
                processor = JobProcessor(chaoxing, course, chapter_tasks, run_config)
                processor.run()
                if stop_event.is_set():
                    self._cancel_task(task_id, "Task cancelled by user")
                    return
                if processor.failed_tasks:
                    failed_courses += 1
                    self._append_task_log(
                        task_id,
                        f"Course finished with {len(processor.failed_tasks)} failed chapters: {course_name}",
                        "warning",
                    )
                    self._update_course_entry(task_id, index, status="failed")
                else:
                    completed_courses += 1
                    self._append_task_log(task_id, f"Course completed: {course_name}", "success")
                    self._update_course_entry(task_id, index, status="completed")
            except Exception as exc:
                failed_courses += 1
                self._append_task_log(task_id, f"Course execution failed {course_name}: {exc}", "error")
                self._update_course_entry(task_id, index, status="failed")

            self._update_progress(
                task_id,
                completed=completed_courses,
                failed=failed_courses,
                current=index,
                current_course=course_name,
            )

        if stop_event.is_set():
            self._cancel_task(task_id, "Task cancelled by user")
            return

        if failed_courses > 0:
            summary = f"Task finished with failures ({failed_courses} failed courses)"
            self._update_task(
                task_id,
                status="failed",
                message=summary,
                current_task="finished",
            )
            self._append_task_log(task_id, "Task finished with failures", "warning")
        else:
            summary = "Task completed"
            self._update_task(
                task_id,
                status="completed",
                message=summary,
                current_task="finished",
            )
            self._append_task_log(task_id, "Task completed", "success")

        self._send_completion_notification(task_id, notify_config, summary)

    def _send_completion_notification(
        self,
        task_id: str,
        notify_config: dict[str, Any],
        summary: str,
    ) -> None:
        """Deliver a completion notification via the configured provider.

        The web `notify_config` carries `service` (provider name, e.g. ServerChan
        /Qmsg/Bark/Telegram) and `url`, matching what app.services.notification
        providers expect as {"provider": ..., "url": ...}. Best-effort: any
        failure is logged into the task log and never propagated.
        """
        if not isinstance(notify_config, dict):
            return
        raw_service = notify_config.get("service")
        service = raw_service.strip() if isinstance(raw_service, str) else ""
        raw_url = notify_config.get("url")
        url = raw_url.strip() if isinstance(raw_url, str) else ""
        if not service or not url:
            return
        if service not in _NOTIFICATION_SERVICE_LABELS:
            self._append_task_log(task_id, "Notification skipped: unsupported service", "warning")
            return

        # SSRF guard: refuse to POST to internal/loopback/metadata hosts, mirroring
        # the /notify/test endpoint guard.
        try:
            url_allowed = validate_notification_url(url)
        except Exception as exc:
            logger.warning("notification URL validation failed: %s", type(exc).__name__)
            self._append_task_log(task_id, "Notification skipped: URL validation failed", "warning")
            return
        if not url_allowed:
            self._append_task_log(
                task_id,
                "Notification skipped: URL must be a public http(s) address",
                "warning",
            )
            return

        provider_config: dict[str, Any] = {"provider": service, "url": url}
        tg_chat_id = notify_config.get("tg_chat_id")
        if tg_chat_id:
            provider_config["tg_chat_id"] = str(tg_chat_id)

        try:
            notifier = NotificationFactory.create_service(provider_config)
            notifier.send(f"chaoxing : {summary}")
            self._append_task_log(task_id, f"Notification sent via {service}", "info")
        except Exception as exc:  # pragma: no cover - best-effort, never fatal
            failure_type = type(exc).__name__
            logger.warning("send learning notification failed: %s", failure_type)
            self._append_task_log(task_id, f"Notification failed ({failure_type})", "warning")

    def _run_task_worker_guarded(self, task_id: str, user_id: str, payload: dict[str, Any]) -> None:
        try:
            self._run_task_worker(task_id, user_id, payload)
        except Exception as exc:  # pragma: no cover - defensive safety net
            logger.exception("Chaoxing learning task crashed: task_id=%s user_id=%s", task_id, user_id)
            message = f"{UNEXPECTED_WORKER_ERROR_PREFIX}: {exc}"
            self._fail_task(task_id, message)

    def _select_courses(self, all_courses: list[dict[str, Any]], selectors: list[str]) -> list[dict[str, Any]]:
        """Resolve selectors to courses, preserving the caller's order (FIFO).

        The selectors are the user's tick order on the course list, so they are
        the OUTER loop: the queue must run in the order the user chose, not in
        the order Chaoxing happens to return courses. Within one selector the
        backend list order still applies, which only matters when a selector is
        broad enough to match several courses (e.g. courseId with no clazz).
        """
        if not selectors:
            return list(all_courses)

        parsed = [_parse_course_selector(item) for item in selectors]
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()

        for target_course, target_clazz, target_cpi in parsed:
            for course in all_courses:
                course_id = str(course.get("courseId") or "")
                clazz_id = str(course.get("clazzId") or "")
                cpi = str(course.get("cpi") or "")
                if target_course and course_id != target_course:
                    continue
                if target_clazz and clazz_id != target_clazz:
                    continue
                if target_cpi and cpi != target_cpi:
                    continue
                identity = f"{course_id}_{clazz_id}_{cpi}"
                if identity in seen:
                    # A course matched by an earlier selector keeps its place in
                    # the queue; dedupe here instead of `break`, so one selector
                    # can still claim several courses.
                    continue
                seen.add(identity)
                selected.append(course)

        return selected

    def _control_events(self, task_id: str) -> tuple[threading.Event | None, threading.Event | None]:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None, None
            return task.get("_stop_event"), task.get("_pause_event")

    def _wait_for_resume(
        self,
        task_id: str,
        pause_event: threading.Event,
        stop_event: threading.Event,
    ) -> bool:
        while not stop_event.is_set() and not pause_event.is_set():
            self._update_task(task_id, status="paused", message="Task paused", current_task="paused")
            time.sleep(0.25)
        return not stop_event.is_set()

    def _cancel_task(self, task_id: str, message: str) -> None:
        self._settle_course_entries(task_id, "cancelled")
        self._update_task(task_id, status="cancelled", message=message, current_task="cancelled")
        self._append_task_log(task_id, message, "warning")

    def _settle_course_entries(self, task_id: str, status: str) -> None:
        """Mark every non-terminal queue entry as ``status``.

        Called when a task ends for a reason that is not per-course (cancel,
        restart). Without it a cancelled run would leave the last course stuck
        on "running" in the queue view forever.
        """
        persist_request: tuple[dict[str, Any], _TaskPersistSequencer, int] | None = None
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            progress = dict(task.get("progress") or {})
            courses = progress.get("courses")
            if not isinstance(courses, list) or not courses:
                return
            changed = False
            settled: list[Any] = []
            for entry in courses:
                if isinstance(entry, dict) and str(entry.get("status") or "") in ("pending", "running"):
                    settled.append({**entry, "status": status})
                    changed = True
                else:
                    settled.append(entry)
            if not changed:
                return
            progress["courses"] = settled
            task["progress"] = progress
            task["updated_at"] = _utc_now_iso()
            persist_request = self._prepare_persist_locked(task)
        if persist_request:
            self._persist_task_state(*persist_request)

    def _fail_task(self, task_id: str, message: str) -> None:
        self._settle_course_entries(task_id, "failed")
        self._update_task(task_id, status="failed", message=message, current_task="failed")
        self._append_task_log(task_id, message, "error")

    def _record_admission_persist_failure(self, task_id: str) -> None:
        """Keep an unstarted task terminal and best-effort overwrite any active row."""
        persist_request: tuple[dict[str, Any], _TaskPersistSequencer, int] | None = None
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            now = _utc_now_iso()
            task.update(
                status="failed",
                message=TASK_PERSIST_FAILURE_MESSAGE,
                current_task="failed",
                updated_at=now,
            )
            task.setdefault("finished_at", now)
            task["logs"].append(
                {
                    "timestamp": now,
                    "message": TASK_PERSIST_FAILURE_MESSAGE,
                    "level": "error",
                }
            )
            if len(task["logs"]) > 1000:
                del task["logs"][:-1000]
            persist_request = self._prepare_persist_locked(task)
        if not persist_request:
            return
        try:
            if self._persist_task_state(*persist_request) is not True:
                logger.warning("failed to compensate learning task admission: task_id=%s", task_id)
        except Exception:  # pragma: no cover - defensive for patched/custom stores
            logger.warning("failed to compensate learning task admission: task_id=%s", task_id, exc_info=True)

    def _append_task_log(self, task_id: str, message: str, level: str = "info") -> None:
        persist_request: tuple[dict[str, Any], _TaskPersistSequencer, int] | None = None
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task["logs"].append(
                {
                    "timestamp": _utc_now_iso(),
                    "message": str(message),
                    "level": level,
                }
            )
            if len(task["logs"]) > 1000:
                del task["logs"][:-1000]
            task["updated_at"] = _utc_now_iso()
            persist_request = self._prepare_persist_locked(task)
        if persist_request:
            self._persist_task_state(*persist_request)

    def _update_task(self, task_id: str, **changes: Any) -> None:
        persist_request: tuple[dict[str, Any], _TaskPersistSequencer, int] | None = None
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            # Skip the (forced) main-DB upsert when nothing actually changed.
            # _wait_for_resume calls this every 0.25s while paused with the same
            # status/message; without this guard each tick would re-serialize and
            # upsert the full payload pointlessly. (F31)
            if all(task.get(key) == value for key, value in changes.items()):
                return
            task.update(changes)
            # Stamp the end time exactly once, on the transition into a terminal
            # status. Every terminal path (completed / failed / cancelled) goes
            # through here, so the admin history has one authoritative field
            # instead of inferring an end time from `updated_at`.
            if "status" in changes and is_terminal_status(changes.get("status")):
                task.setdefault("finished_at", _utc_now_iso())
            task["updated_at"] = _utc_now_iso()
            persist_request = self._prepare_persist_locked(task)
        if persist_request:
            self._persist_task_state(*persist_request)

    def _update_progress(self, task_id: str, **updates: Any) -> None:
        persist_request: tuple[dict[str, Any], _TaskPersistSequencer, int] | None = None
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            progress = dict(task.get("progress") or {})
            progress.update(updates)
            task["progress"] = progress
            task["updated_at"] = _utc_now_iso()
            # High-frequency (video) progress: throttle main-DB writes. The next
            # forced write (status change / terminal) carries the latest
            # progress, so the final state is never lost. (F31)
            persist_request = self._prepare_persist_locked(task, force=False)
        if persist_request:
            self._persist_task_state(*persist_request)

    def _increase_progress(self, task_id: str, key: str, delta: int = 1) -> None:
        persist_request: tuple[dict[str, Any], _TaskPersistSequencer, int] | None = None
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            progress = dict(task.get("progress") or {})
            progress[key] = int(progress.get(key) or 0) + int(delta)
            task["progress"] = progress
            task["updated_at"] = _utc_now_iso()
            persist_request = self._prepare_persist_locked(task, force=False)
        if persist_request:
            self._persist_task_state(*persist_request)

    def _update_course_entry(self, task_id: str, index: int, **changes: Any) -> None:
        """Merge ``changes`` into one entry of ``progress.courses`` (1-based index).

        ``_update_progress`` replaces a key wholesale, so a single entry has to
        be read, patched and written back. Out-of-range indices are ignored:
        the array is only a view, and a missing entry must never break the run.
        """
        persist_request: tuple[dict[str, Any], _TaskPersistSequencer, int] | None = None
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            progress = dict(task.get("progress") or {})
            courses = progress.get("courses")
            if not isinstance(courses, list):
                return
            position = int(index) - 1
            if position < 0 or position >= len(courses):
                return
            entry = courses[position]
            if not isinstance(entry, dict):
                return
            updated = list(courses)
            updated[position] = {**entry, **changes}
            progress["courses"] = updated
            task["progress"] = progress
            task["updated_at"] = _utc_now_iso()
            persist_request = self._prepare_persist_locked(task, force=False)
        if persist_request:
            self._persist_task_state(*persist_request)

    def _mark_chapter_done(self, task_id: str, index: int) -> None:
        """Record one finished chapter: global counter + the course's own counter.

        Both counters live in ``progress``, so they are updated under a single
        lock acquisition. This runs once per chapter from the (concurrent)
        chapter workers, so taking the lock twice would be needless contention.
        """
        persist_request: tuple[dict[str, Any], _TaskPersistSequencer, int] | None = None
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            progress = dict(task.get("progress") or {})
            progress["completed_chapters"] = int(progress.get("completed_chapters") or 0) + 1

            courses = progress.get("courses")
            if isinstance(courses, list):
                position = int(index) - 1
                if 0 <= position < len(courses) and isinstance(courses[position], dict):
                    entry = courses[position]
                    updated = list(courses)
                    updated[position] = {
                        **entry,
                        "chapters_done": int(entry.get("chapters_done") or 0) + 1,
                    }
                    progress["courses"] = updated

            task["progress"] = progress
            task["updated_at"] = _utc_now_iso()
            persist_request = self._prepare_persist_locked(task, force=False)
        if persist_request:
            self._persist_task_state(*persist_request)

    @staticmethod
    def _task_public_payload(task: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in task.items() if not str(k).startswith("_")}

    @staticmethod
    def _public_view(task: dict[str, Any]) -> dict[str, Any]:
        """Return API-safe view: drop private keys, credentials, and schedule_args."""
        return {
            k: v
            for k, v in task.items()
            if not str(k).startswith("_") and k not in ("credentials", "schedule_args")
        }

    @staticmethod
    def _default_progress() -> dict[str, Any]:
        return {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "current": 0,
            "total_chapters": 0,
            "completed_chapters": 0,
            "current_course": "",
            "current_chapter": "",
            "video_progress": None,
            # Per-course queue view, in the order the user ticked them. The
            # scalars above stay authoritative for the existing UI; this array
            # is what lets the page show the whole queue (pending / running /
            # done) instead of only the course currently being worked on.
            "courses": [],
        }

    def _prepare_persist_locked(
        self,
        task: dict[str, Any],
        *,
        force: bool = True,
    ) -> tuple[dict[str, Any], _TaskPersistSequencer, int] | None:
        """Capture and order a snapshot while ``self._lock`` is held."""
        if not force:
            now = time.monotonic()
            last = float(task.get("_last_progress_persist_ts") or 0.0)
            if now - last < PROGRESS_PERSIST_INTERVAL:
                return None
            task["_last_progress_persist_ts"] = now

        snapshot = self._task_public_payload(task)
        sequencer = task.get("_persist_sequencer")
        if not isinstance(sequencer, _TaskPersistSequencer):
            sequencer = _TaskPersistSequencer()
            task["_persist_sequencer"] = sequencer
        revision = sequencer.reserve()
        return snapshot, sequencer, revision

    def _persist_task_state(
        self,
        task_state_public: dict[str, Any],
        sequencer: _TaskPersistSequencer | None = None,
        revision: int | None = None,
    ) -> bool:
        persisted = True

        def write() -> None:
            nonlocal persisted
            try:
                result = task_store.upsert_task(LEARNING_TASK_KIND, task_state_public)
                persisted = result is True
            except Exception as exc:  # pragma: no cover - defensive fallback
                persisted = False
                logger.warning("persist learning task failed: %s", exc)

        if sequencer is None or revision is None:
            # Keep direct private callers compatible; manager-generated snapshots
            # always carry a reservation and therefore use the ordered path.
            write()
            return persisted
        sequencer.run(revision, write)
        return persisted

    def _ensure_tasks_loaded_for_user(self, user_id: str) -> None:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return
        with self._lock:
            if normalized_user_id in self._loaded_task_users:
                return
        self._load_tasks_from_store_for_user(normalized_user_id)

    def _load_task_from_store(self, user_id: str, task_id: str) -> None:
        normalized_user_id = str(user_id or "").strip()
        normalized_task_id = str(task_id or "").strip()
        if not normalized_user_id or not normalized_task_id:
            return
        try:
            stored_task = task_store.get_task(
                task_kind=LEARNING_TASK_KIND,
                task_id=normalized_task_id,
                user_id=normalized_user_id,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning(
                "load learning task failed: user=%s task=%s err=%s", normalized_user_id, normalized_task_id, exc
            )
            stored_task = None
        if stored_task:
            self._merge_task_from_store(stored_task)

    def _load_tasks_from_store_for_user(self, user_id: str) -> None:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return
        try:
            stored_tasks = task_store.list_tasks(
                task_kind=LEARNING_TASK_KIND,
                user_id=normalized_user_id,
                limit=USER_TASK_LOAD_LIMIT,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("load learning tasks failed: user=%s err=%s", normalized_user_id, exc)
            stored_tasks = []

        for item in stored_tasks:
            self._merge_task_from_store(item)

        with self._lock:
            cleanup_task_records(self._tasks)
            self._loaded_task_users.add(normalized_user_id)

    def _merge_task_from_store(self, item: dict[str, Any], now: str | None = None) -> bool:
        task_id = str(item.get("task_id") or "").strip()
        user_id = str(item.get("user_id") or "").strip()
        if not task_id or not user_id:
            return False

        with self._lock:
            if task_id in self._tasks:
                return False

        current_time = now or _utc_now_iso()
        task: dict[str, Any] = dict(item)
        progress = self._default_progress()
        raw_progress = task.get("progress")
        if isinstance(raw_progress, dict):
            progress.update(raw_progress)
        task["progress"] = progress
        task.setdefault("platform", "chaoxing")
        task.setdefault("created_at", task.get("started_at") or current_time)
        task.setdefault("started_at", task.get("created_at") or current_time)
        task.setdefault("updated_at", task.get("started_at") or current_time)

        logs = task.get("logs")
        if not isinstance(logs, list):
            logs = []
        task["logs"] = logs

        interrupted = str(task.get("status") or "").lower() in INTERRUPTED_STATUSES
        if interrupted:
            task["status"] = "failed"
            task["message"] = RESTART_INTERRUPTED_MESSAGE
            task["current_task"] = "failed"
            task["updated_at"] = current_time
            task.setdefault("finished_at", current_time)
            task["logs"].append(
                {
                    "timestamp": current_time,
                    "message": RESTART_INTERRUPTED_MESSAGE,
                    "level": "warning",
                }
            )
            if len(task["logs"]) > 1000:
                del task["logs"][:-1000]
        elif is_recurring_status(task.get("status")):
            # A recurring template survives restart, but its stored next_fire_at
            # may be in the past (the service was down). Recompute it forward —
            # and never replay the missed occurrences, or a long outage would
            # fire a burst of runs the moment the service comes back.
            schedule = task.get("schedule")
            if isinstance(schedule, dict):
                try:
                    time_of_day = parse_time_of_day(schedule.get("time_of_day"))
                    repeat = normalize_repeat(schedule.get("repeat"))
                    anchor = parse_anchor_date(schedule.get("anchor_date"))
                    now_dt = datetime.fromisoformat(str(current_time).replace("Z", "+00:00"))
                    if now_dt.tzinfo is None:
                        now_dt = now_dt.replace(tzinfo=UTC)
                    schedule["next_fire_at"] = first_occurrence(
                        repeat, time_of_day, now=now_dt, anchor_date=anchor
                    ).isoformat()
                except (RecurrenceError, ValueError, TypeError, OverflowError):
                    logger.warning(
                        "recurring task restore: could not recompute next_fire_at (task=%s)",
                        task_id,
                    )
        else:
            # "scheduled" tasks survive restart: restore their pause/stop events.
            pass

        pause_event = threading.Event()
        pause_event.set()
        task["_pause_event"] = pause_event
        task["_stop_event"] = threading.Event()
        task["_log_cursor"] = 0

        with self._lock:
            if task_id in self._tasks:
                return False
            self._tasks[task_id] = task

        if interrupted:
            with self._lock:
                persist_request = self._prepare_persist_locked(task)
            if persist_request:
                self._persist_task_state(*persist_request)
        return True

    def _restore_tasks_from_store(self) -> None:
        try:
            stored_tasks = task_store.list_tasks(task_kind=LEARNING_TASK_KIND, user_id=None, limit=300)
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("restore learning tasks failed: %s", exc)
            stored_tasks = []

        now = _utc_now_iso()
        for item in stored_tasks:
            self._merge_task_from_store(item, now=now)
        with self._lock:
            cleanup_task_records(self._tasks)


learning_manager = ChaoxingLearningManager()
