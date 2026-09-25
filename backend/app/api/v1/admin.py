"""Admin endpoints for cross-user task overview and management.

Only mounted in server mode (PROFILE != "local"). Every endpoint requires
administrator authentication via :func:`app.services.admin.is_admin_user`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import settings
from app.dependencies import get_current_user
from app.db.session import get_db_session
from app.services.admin import is_admin_user
from app.services.course.chaoxing.task_admission import (
    ACTIVE_TASK_STATUSES,
    TaskAdmissionError,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_LEARNING_TASK_KIND = "chaoxing_learning"
_VALID_TASK_STATUS_FILTERS = frozenset(
    {"running", "pending", "paused", "cancelling", "stopping", "scheduled",
     "completed", "failed", "cancelled", "error"}
)
# How many recent log lines /admin/events aggregates across tasks.
_EVENTS_LIMIT = 60


class AdminTaskStartRequest(BaseModel):
    """Admin-initiated task for another user.

    The administrator supplies the target user's Chaoxing credentials because
    the app has no shared credential store; the same values a user would type
    on the 泛雅 page are needed to run the job on their behalf. They are
    encrypted at rest by task_store like any other task payload.
    """

    user_id: str
    username: str
    password: str
    course_ids: list[str] | None = None
    start_at: str | None = None
    stop_at: str | None = None
    start_jitter_min: int = 0
    stop_jitter_min: int = 0
    speed: float = Field(default=1.0, ge=1.0, le=2.0)
    concurrency: int = Field(default=4, ge=1, le=16)
    unopened_strategy: str = "retry"

    @field_validator("user_id", "username", "password")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("start_jitter_min", "stop_jitter_min", mode="before")
    @classmethod
    def _clamp_jitter(cls, value: object) -> int:
        return max(0, min(int(value or 0), 720))

    @model_validator(mode="after")
    def _validate_schedule(self) -> "AdminTaskStartRequest":
        start_at = (self.start_at or "").strip()
        stop_at = (self.stop_at or "").strip()
        if not start_at and not stop_at:
            return self
        if not start_at:
            raise ValueError("stop_at requires start_at")

        def _parse(raw: str) -> datetime:
            parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed

        try:
            start_dt = _parse(start_at)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid start_at: {exc}") from exc
        if start_dt < datetime.now(timezone.utc) - timedelta(seconds=60):
            raise ValueError("start_at must be in the future (or within 60s ago)")
        if stop_at:
            try:
                stop_dt = _parse(stop_at)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid stop_at: {exc}") from exc
            if stop_dt <= start_dt:
                raise ValueError("stop_at must be after start_at")
        return self


async def _require_admin(current_user: dict) -> int:
    """Enforce administrator identity; raises 403 otherwise.

    Returns the integer user id for use in downstream queries.
    """
    try:
        uid = int(current_user.get("user_id"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )
    if not await asyncio.to_thread(is_admin_user, uid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator only",
        )
    return uid


@router.get("/overview")
async def admin_overview(current_user: dict = Depends(get_current_user)):
    await _require_admin(current_user)
    result: dict[str, Any] = {
        "users": 0,
        "active_tasks": 0,
        "scheduled_tasks": 0,
        "failed_24h": 0,
    }
    try:
        with get_db_session() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM users")
            row = cur.fetchone()
            if row:
                result["users"] = int(row["cnt"] or 0)

            active_placeholders = ", ".join(["%s"] * len(ACTIVE_TASK_STATUSES))
            active_params = [s.lower() for s in ACTIVE_TASK_STATUSES]
            cur.execute(
                f"SELECT COUNT(*) AS cnt FROM course_task_store WHERE LOWER(status) IN ({active_placeholders})",
                active_params,
            )
            row = cur.fetchone()
            if row:
                result["active_tasks"] = int(row["cnt"] or 0)

            cur.execute(
                "SELECT COUNT(*) AS cnt FROM course_task_store WHERE LOWER(status) = %s",
                ("scheduled",),
            )
            row = cur.fetchone()
            if row:
                result["scheduled_tasks"] = int(row["cnt"] or 0)
                # scheduled is included in active; exclude for a cleaner split.
                result["active_tasks"] = max(0, result["active_tasks"] - result["scheduled_tasks"])

            cur.execute(
                "SELECT COUNT(*) AS cnt FROM course_task_store"
                " WHERE LOWER(status) = 'failed' AND updated_at >= NOW() - INTERVAL '24 hours'",
            )
            row = cur.fetchone()
            if row:
                result["failed_24h"] = int(row["cnt"] or 0)
    except Exception:
        logger.exception("admin overview query failed — returning zeroes")

    return {"status": "success", "data": result}


@router.get("/users")
async def admin_users(
    limit: int = 200,
    current_user: dict = Depends(get_current_user),
):
    await _require_admin(current_user)
    limit = max(1, min(limit, 500))
    try:
        with get_db_session() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, email, tenant_db_name, created_at FROM users ORDER BY id LIMIT %s",
                (limit,),
            )
            users = [dict(row) for row in cur.fetchall()]

            if not users:
                return {"status": "success", "data": []}

            # Gather task counts per user
            active_placeholders = ", ".join(["%s"] * len(ACTIVE_TASK_STATUSES))
            active_params = [s.lower() for s in ACTIVE_TASK_STATUSES]
            cur.execute(
                f"SELECT user_id,"
                f" COUNT(*) FILTER (WHERE LOWER(status) IN ({active_placeholders})) AS active_tasks,"
                f" COUNT(*) FILTER (WHERE LOWER(status) = 'scheduled') AS scheduled_tasks"
                f" FROM course_task_store GROUP BY user_id",
                active_params,
            )
            task_rows = {str(row["user_id"]): dict(row) for row in cur.fetchall()}

            for user in users:
                uid = str(user.get("id", ""))
                counts = task_rows.get(uid, {})
                active = int(counts.get("active_tasks", 0) or 0)
                scheduled = int(counts.get("scheduled_tasks", 0) or 0)
                user["active_tasks"] = active
                user["scheduled_tasks"] = scheduled
                if isinstance(user.get("created_at"), (datetime, type(None))):
                    user["created_at"] = user["created_at"].isoformat() if user.get("created_at") else None
                else:
                    user["created_at"] = str(user["created_at"]) if user.get("created_at") else None
    except Exception:
        logger.exception("admin users query failed")
        return {"status": "error", "message": "Failed to load users"}

    return {"status": "success", "data": users}


@router.get("/tasks")
async def admin_tasks(
    limit: int = 100,
    status: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    await _require_admin(current_user)
    limit = max(1, min(limit, 500))
    if status is not None:
        normalized = status.strip().lower()
        if normalized not in _VALID_TASK_STATUS_FILTERS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status filter. Allowed: {', '.join(sorted(_VALID_TASK_STATUS_FILTERS))}",
            )

    try:
        with get_db_session() as conn, conn.cursor() as cur:
            params: list[Any] = []
            query = (
                "SELECT t.task_id, t.user_id, t.status, t.message, t.started_at, t.updated_at,"
                " t.payload, u.username"
                " FROM course_task_store t LEFT JOIN users u ON u.id::text = t.user_id"
            )
            if status is not None:
                query += " WHERE LOWER(t.status) = %s"
                params.append(status.lower())
            query += " ORDER BY t.updated_at DESC NULLS LAST LIMIT %s"
            params.append(limit)
            cur.execute(query, params)
            tasks: list[dict[str, Any]] = []
            for row in cur.fetchall():
                row_dict = dict(row)
                payload = row_dict.get("payload")
                if isinstance(payload, dict):
                    schedule = payload.get("schedule")
                    progress = payload.get("progress")
                else:
                    schedule = None
                    progress = None
                item: dict[str, Any] = {
                    "task_id": row_dict.get("task_id"),
                    "user_id": row_dict.get("user_id"),
                    "username": row_dict.get("username"),
                    "task_kind": _LEARNING_TASK_KIND,
                    "status": row_dict.get("status"),
                    "message": row_dict.get("message"),
                    "started_at": row_dict.get("started_at"),
                    "updated_at": row_dict.get("updated_at"),
                    "schedule": schedule,
                    "progress": progress,
                }
                # Convert datetime objects to ISO strings
                for key in ("started_at", "updated_at"):
                    val = item.get(key)
                    if isinstance(val, datetime):
                        item[key] = val.isoformat()
                    elif isinstance(val, str):
                        pass
                    else:
                        item[key] = None
                tasks.append(item)
    except Exception:
        logger.exception("admin tasks query failed")
        return {"status": "error", "message": "Failed to load tasks"}

    return {"status": "success", "data": tasks}


@router.post("/task/{task_id}/stop")
async def admin_stop_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
):
    await _require_admin(current_user)
    try:
        with get_db_session() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT task_kind, user_id FROM course_task_store WHERE task_id = %s",
                (task_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
            task_kind = str(row["task_kind"] or "")
            if task_kind != _LEARNING_TASK_KIND:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This task type does not support admin stop",
                )
            user_id = str(row.get("user_id") or "")

        from app.services.course.chaoxing.learning_manager import learning_manager

        result = await asyncio.to_thread(
            learning_manager.stop_task,
            user_id=user_id,
            task_id=task_id,
        )
        if result.get("status") == "error":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result.get("message", "Task not found"))
        return {"status": "success", "message": result.get("message", "Task stop requested"), "data": result}
    except HTTPException:
        raise
    except Exception:
        logger.exception("admin task stop failed: task_id=%s", task_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to stop task")


@router.get("/health")
async def admin_health(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """System status for the admin console.

    Mirrors GET /health but stays inside the admin router so the console needs
    no separate public probe, and adds the scheduler/queue counters the
    prototype's status page shows. Every probe is best-effort: a failing
    dependency is reported, never raised, so the page can render a partial view.
    """
    await _require_admin(current_user)

    checks: list[dict[str, Any]] = []

    # --- database ---------------------------------------------------------
    db_ok = False
    db_detail = ""
    try:
        from app.storage.factory import get_storage

        def _ping() -> bool:
            return bool(get_storage().probe.ping())

        db_ok = await asyncio.to_thread(_ping)
    except Exception as exc:  # pragma: no cover - defensive
        db_detail = type(exc).__name__
    checks.append({
        "name": "database",
        "status": "ok" if db_ok else "error",
        "detail": db_detail or ("reachable" if db_ok else "unreachable"),
    })

    # --- schema (registration readiness) ---------------------------------
    schema = "unknown"
    if settings.PROFILE != "local" and settings.STORAGE_BACKEND == "postgres":
        try:
            from app.db.bootstrap import cached_schema_status

            schema = await asyncio.to_thread(cached_schema_status)
        except Exception:  # pragma: no cover - defensive
            schema = "unknown"
    checks.append({
        "name": "schema",
        "status": "ok" if schema == "ok" else "warning",
        "detail": schema,
    })

    # --- background loops -------------------------------------------------
    cleanup_task = getattr(request.app.state, "cleanup_task", None)
    cleanup_alive = bool(cleanup_task and not cleanup_task.done())
    checks.append({
        "name": "cleanup_loop",
        "status": "ok" if cleanup_alive else "error",
        "detail": "alive" if cleanup_alive else "dead",
    })

    dispatch_task = getattr(request.app.state, "schedule_dispatch_task", None)
    dispatch_alive = bool(dispatch_task and not dispatch_task.done())
    checks.append({
        "name": "schedule_dispatch_loop",
        "status": "ok" if dispatch_alive else "error",
        "detail": "alive (15s interval)" if dispatch_alive else "dead",
    })

    # --- queue counters ---------------------------------------------------
    counters: dict[str, Any] = {
        "active_tasks": 0,
        "scheduled_tasks": 0,
        "users": 0,
    }
    if db_ok:
        try:
            with get_db_session() as conn, conn.cursor() as cur:
                placeholders = ", ".join(["%s"] * len(ACTIVE_TASK_STATUSES))
                cur.execute(
                    f"SELECT COUNT(*) AS cnt FROM course_task_store"
                    f" WHERE LOWER(status) IN ({placeholders})",
                    [s.lower() for s in ACTIVE_TASK_STATUSES],
                )
                row = cur.fetchone()
                counters["active_tasks"] = int(row["cnt"] or 0) if row else 0

                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM course_task_store WHERE LOWER(status) = 'scheduled'"
                )
                row = cur.fetchone()
                counters["scheduled_tasks"] = int(row["cnt"] or 0) if row else 0

                cur.execute("SELECT COUNT(*) AS cnt FROM users")
                row = cur.fetchone()
                counters["users"] = int(row["cnt"] or 0) if row else 0
        except Exception:
            logger.exception("admin health counters failed")

    degraded = any(c["status"] == "error" for c in checks)
    return {
        "status": "success",
        "data": {
            "overall": "degraded" if degraded else "ok",
            "version": request.app.version,
            "profile": settings.PROFILE,
            "storage_backend": settings.STORAGE_BACKEND,
            "checks": checks,
            "counters": counters,
        },
    }


@router.get("/events")
async def admin_events(
    limit: int = _EVENTS_LIMIT,
    current_user: dict = Depends(get_current_user),
):
    """Recent log lines aggregated across every user's tasks.

    Reads the stored task payloads and flattens their `logs` arrays into one
    reverse-chronological feed, which is what the prototype's "system events"
    panel shows. Only the log fields are exposed; credentials and schedule
    arguments in the payload never leave this function.
    """
    await _require_admin(current_user)
    limit = max(1, min(limit, 200))

    try:
        with get_db_session() as conn, conn.cursor() as cur:
            # Bound the scan: logs live inside the JSONB payload, so we take the
            # most recently updated rows and merge their tails.
            cur.execute(
                "SELECT t.task_id, t.user_id, t.status, t.payload, u.username"
                " FROM course_task_store t LEFT JOIN users u ON u.id::text = t.user_id"
                " ORDER BY t.updated_at DESC NULLS LAST LIMIT 50"
            )
            rows = cur.fetchall()
    except Exception:
        logger.exception("admin events query failed")
        return {"status": "error", "message": "Failed to load events"}

    events: list[dict[str, Any]] = []
    for row in rows:
        row_dict = dict(row)
        payload = row_dict.get("payload")
        if not isinstance(payload, dict):
            continue
        logs = payload.get("logs")
        if not isinstance(logs, list):
            continue
        for entry in logs:
            if not isinstance(entry, dict):
                continue
            events.append({
                "timestamp": entry.get("timestamp"),
                "level": str(entry.get("level") or "info").lower(),
                "message": entry.get("message") or "",
                "task_id": row_dict.get("task_id"),
                "user_id": row_dict.get("user_id"),
                "username": row_dict.get("username"),
                "status": row_dict.get("status"),
            })

    # Newest first. Timestamps are ISO strings; sorting them lexically is safe
    # because they are all UTC with the same shape, and any malformed value
    # simply sorts to the end.
    events.sort(key=lambda e: str(e.get("timestamp") or ""), reverse=True)
    return {"status": "success", "data": events[:limit]}


@router.post("/task/start")
async def admin_start_task(
    payload: AdminTaskStartRequest,
    current_user: dict = Depends(get_current_user),
):
    """Start a task on behalf of another user.

    Without `start_at` the task begins immediately; with it the task is
    scheduled and the 15s dispatcher fires it, exactly like a user-initiated
    scheduled task. Runs through the same admission checks, so an existing
    active task for that user still yields 409.
    """
    await _require_admin(current_user)

    target_user_id = payload.user_id.strip()
    start_at = (payload.start_at or "").strip()

    request_payload: dict[str, Any] = {
        "platform": "chaoxing",
        "username": payload.username.strip(),
        "password": payload.password,
        "course_ids": payload.course_ids or [],
        "speed": payload.speed,
        "concurrency": payload.concurrency,
        "unopened_strategy": payload.unopened_strategy,
        "tiku_config": {},
        "notify_config": {},
    }
    if start_at:
        request_payload.update({
            "start_at": start_at,
            "stop_at": (payload.stop_at or "").strip() or None,
            "start_jitter_min": payload.start_jitter_min,
            "stop_jitter_min": payload.stop_jitter_min,
        })

    from app.services.course.chaoxing.learning_manager import learning_manager

    try:
        task_id = await asyncio.to_thread(
            learning_manager.start_task,
            target_user_id,
            request_payload,
        )
    except TaskAdmissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except Exception:
        logger.exception("admin start task failed: user=%s", target_user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start task",
        )

    return {
        "status": "success",
        "message": "Scheduled task created" if start_at else "Task started",
        "data": {
            "task_id": task_id,
            "user_id": target_user_id,
            "scheduled": bool(start_at),
        },
    }
