"""Per-user Chaoxing cookie vault.

QR-code login (``qr_login.py``) captures the full Chaoxing cookie jar without
ever seeing the account password. That jar has to live *somewhere* until the
user starts a task, and the one place it must NOT live is the process-wide
``cookies.json`` file that ``cookies.py`` manages: with ``uvicorn --workers 1``
every tenant shares this process, so a single file would let one user's login
silently overwrite another's.

This module keeps the jars in memory, keyed by the application ``user_id``.
That matches how the sign-in clients (``ChaoxingSigninManager._clients``) and
the Zhihuishu adapters are already stored, so a restart drops every session
uniformly instead of leaving a half-stale file behind.

Scheduled learning tasks are the one exception: their credentials are
persisted (encrypted) so they survive a restart, and ``cookies_json`` is
carried in that same encrypted payload — see ``learning_manager``.
"""

from __future__ import annotations

import threading
import time
from typing import Any

# Chaoxing keeps a desktop session alive for a long time, but an entry that
# outlives its usefulness only produces confusing "logged in" reports followed
# by upstream 401s. Expire quietly and let the user scan again.
_VAULT_TTL_SECONDS = 7 * 24 * 60 * 60


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
        return self.summary(normalized_user_id)

    def get(self, user_id: str) -> dict[str, str] | None:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return None
        with self._lock:
            entry = self._entries.get(normalized_user_id)
            if entry is None:
                return None
            if time.time() - float(entry.get("updated_at") or 0) > _VAULT_TTL_SECONDS:
                self._entries.pop(normalized_user_id, None)
                return None
            cookies = entry.get("cookies")
            return dict(cookies) if isinstance(cookies, dict) and cookies else None

    def summary(self, user_id: str) -> dict[str, Any]:
        normalized_user_id = str(user_id or "").strip()
        with self._lock:
            entry = self._entries.get(normalized_user_id)
            if entry is not None and time.time() - float(entry.get("updated_at") or 0) > _VAULT_TTL_SECONDS:
                self._entries.pop(normalized_user_id, None)
                entry = None
            nickname = str(entry.get("nickname") or "") if entry else ""
            uid = str(entry.get("uid") or "") if entry else ""
            updated_at = float(entry.get("updated_at") or 0) if entry else 0.0
        return {
            "logged_in": entry is not None,
            "nickname": nickname,
            "uid": uid,
            "updated_at": updated_at,
        }

    def clear(self, user_id: str) -> None:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return
        with self._lock:
            self._entries.pop(normalized_user_id, None)


chaoxing_cookie_vault = ChaoxingCookieVault()
