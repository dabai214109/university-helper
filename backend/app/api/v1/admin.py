"""Admin endpoints for cross-user task overview and management.

Only mounted in server mode (PROFILE != "local"). Every endpoint requires
administrator authentication via :func:`app.services.admin.is_admin_user`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_current_user
from app.db.session import get_db_session
from app.services.admin import is_admin_user
from app.services.course.chaoxing.task_admission import ACTIVE_TASK_STATUSES

router = APIRouter()
logger = logging.getLogger(__name__)

_LEARNING_TASK_KIND = "chaoxing_learning"
# Keep in sync with the learning_manager terminal set so we offer the stop button
# only for tasks that are still live.
_ALLOWED_STOP_STATUSES = frozenset(
    s.lower() for s in ACTIVE_TASK_STATUSES if s.lower() not in {"cancelling", "stopping"}
)
_VALID_TASK_STATUS_FILTERS = frozenset(
    {"running", "pending", "paused", "cancelling", "stopping", "scheduled",
     "completed", "failed", "cancelled", "error"}
)


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
