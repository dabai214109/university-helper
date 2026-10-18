"""Per-user Chaoxing cookie vault.

QR-code login (``qr_login.py``) captures the full Chaoxing cookie jar without
ever seeing the account password. That jar has to live *somewhere* until the
user starts a task, and the one place it must NOT live is the process-wide
``cookies.json`` file that ``cookies.py`` manages: with ``uvicorn --workers 1``
every tenant shares this process, so a single file would let one user's login
silently overwrite another's.

Two-tier storage:

  · In memory (``_entries``) for the hot path — every task start reads it.
  · Persisted **encrypted** through ``task_store`` under a dedicated task_kind,
    so a restart no longer forces everyone to re-scan.

The persisted tier uses the same ``CREDENTIAL_ENCRYPTION_KEY`` that already
protects account passwords, so a cookie jar is protected exactly as strongly as
the password it replaces. The earlier design kept jars memory-only; that meant
every image update logged everyone out, which is a real usability cost for a
single-tenant self-hosted deployment.

Recovery is lazy: the first ``get()``/``summary()`` for a user who is not in
memory tries the store once. A missing or undecryptable row simply behaves as
"not logged in" — never an error, since a stale jar must not break the page.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# Chaoxing keeps a desktop session alive for a long time, but an entry that
# outlives its usefulness only produces confusing "logged in" reports followed
# by upstream 401s. Expire quietly and let the user scan again.
_VAULT_TTL_SECONDS = 7 * 24 * 60 * 60

# 持久化用的 task_kind。复用 course_task_store 表，不新增表结构。
# 前缀刻意与 LEARNING_TASK_KIND 区分开，避免被任务列表查询扫到。
_VAULT_TASK_KIND = "chaoxing_cookie_vault"
# 一个用户一条记录，task_id 固定成 user_id 便于按主键直接取。
def _vault_task_id(user_id: str) -> str:
    return f"vault-{user_id}"


class ChaoxingCookieVault:
    """Thread-safe ``user_id -> cookie jar`` store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}

    def set(
        self,
        user_id: str,
        cookies: dict[str, str],
        *,
        nickname: str = "",
        uid: str = "",
    ) -> dict[str, Any]:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id or not isinstance(cookies, dict) or not cookies:
            return self.summary(normalized_user_id)
        entry = {
            "cookies": {str(k): str(v) for k, v in cookies.items()},
            "nickname": str(nickname or "").strip(),
            "uid": str(uid or "").strip(),
            "updated_at": time.time(),
        }
        with self._lock:
            self._entries[normalized_user_id] = entry
        self._persist(normalized_user_id, entry)
        return self.summary(normalized_user_id)

    def get(self, user_id: str) -> dict[str, str] | None:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return None
        with self._lock:
            entry = self._entries.get(normalized_user_id)
            if entry is not None and time.time() - float(entry.get("updated_at") or 0) > _VAULT_TTL_SECONDS:
                self._entries.pop(normalized_user_id, None)
                entry = None
            if entry is not None:
                cookies = entry.get("cookies")
                return dict(cookies) if isinstance(cookies, dict) and cookies else None

        # 内存里没有 —— 可能是进程刚重启。试一次持久化存储。
        restored = self._restore(normalized_user_id)
        if restored is None:
            return None
        cookies = restored.get("cookies")
        return dict(cookies) if isinstance(cookies, dict) and cookies else None

    def summary(self, user_id: str) -> dict[str, Any]:
        normalized_user_id = str(user_id or "").strip()
        with self._lock:
            entry = self._entries.get(normalized_user_id)
            if entry is not None and time.time() - float(entry.get("updated_at") or 0) > _VAULT_TTL_SECONDS:
                self._entries.pop(normalized_user_id, None)
                entry = None
        if entry is None and normalized_user_id:
            # 进程重启后 summary 是页面第一次调用，这里也要能恢复，
            # 否则会出现「有 cookie 但显示未登录」。
            entry = self._restore(normalized_user_id)
        # 注意：这两个值必须在所有分支上都算出来 —— 只放在恢复分支里会让
        # 内存命中的路径抛 UnboundLocalError。
        nickname = str(entry.get("nickname") or "") if entry else ""
        uid = str(entry.get("uid") or "") if entry else ""
        updated_at = float(entry.get("updated_at") or 0) if entry else 0.0
        return {
            "logged_in": entry is not None,
            "nickname": nickname,
            "uid": uid,
            "updated_at": updated_at,
        }

    # ── 持久化（加密）──────────────────────────────────────────────────────
    #
    # 用 task_store 落库：它已经用 CREDENTIAL_ENCRYPTION_KEY 加密了
    # password / token 等字段，cookies 放进 credentials 容器里同样会被加密。
    # 失败一律降级为「仅内存」——持久化是便利性优化，不能让它拖垮登录。

    def _persist(self, user_id: str, entry: dict[str, Any]) -> None:
        try:
            from ..task_store import task_store

            task_store.upsert_task(
                _VAULT_TASK_KIND,
                {
                    "task_id": _vault_task_id(user_id),
                    "user_id": user_id,
                    "status": "active",
                    "message": "Chaoxing cookie jar",
                    "started_at": None,
                    "updated_at": None,
                    # credentials 是 task_store 的敏感容器，里面所有字段都会被加密
                    "credentials": {
                        "cookies": entry.get("cookies") or {},
                        "nickname": entry.get("nickname") or "",
                        "uid": entry.get("uid") or "",
                    },
                    "vault_updated_at": entry.get("updated_at") or 0,
                },
            )
        except Exception:
            logger.warning("cookie_vault: persist failed for user=%s", user_id, exc_info=True)

    def _restore(self, user_id: str) -> dict[str, Any] | None:
        """从存储读回一条记录并放回内存；失败/过期返回 None。"""
        try:
            from ..task_store import task_store

            item = task_store.get_task(_VAULT_TASK_KIND, _vault_task_id(user_id), user_id)
        except Exception:
            logger.warning("cookie_vault: restore failed for user=%s", user_id, exc_info=True)
            return None

        if not isinstance(item, dict):
            return None
        creds = item.get("credentials")
        if not isinstance(creds, dict):
            return None
        cookies = creds.get("cookies")
        if not isinstance(cookies, dict) or not cookies:
            return None

        entry = {
            "cookies": {str(k): str(v) for k, v in cookies.items()},
            "nickname": str(creds.get("nickname") or ""),
            "uid": str(creds.get("uid") or ""),
            "updated_at": float(item.get("vault_updated_at") or time.time()),
        }
        # 过期的记录不要复活
        if time.time() - entry["updated_at"] > _VAULT_TTL_SECONDS:
            self._forget(user_id)
            return None

        with self._lock:
            self._entries[user_id] = entry
        return entry

    def _forget(self, user_id: str) -> None:
        """把持久化记录标记为已退出（写一条 cancelled，而不是物理删除）。"""
        try:
            from ..task_store import task_store

            task_store.upsert_task(
                _VAULT_TASK_KIND,
                {
                    "task_id": _vault_task_id(user_id),
                    "user_id": user_id,
                    "status": "cancelled",
                    "message": "Chaoxing cookie jar cleared",
                    "credentials": {},
                    "vault_updated_at": 0,
                },
            )
        except Exception:
            logger.warning("cookie_vault: forget failed for user=%s", user_id, exc_info=True)

    def clear(self, user_id: str) -> None:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return
        with self._lock:
            self._entries.pop(normalized_user_id, None)
        self._forget(normalized_user_id)


chaoxing_cookie_vault = ChaoxingCookieVault()
