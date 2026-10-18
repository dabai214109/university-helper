"""Tests for cookie-vault persistence.

The vault used to be memory-only, so every image update logged everyone out.
It now writes the jar through task_store (which encrypts the `credentials`
container) and restores it lazily on first access after a restart.

These tests use an in-memory fake store so they exercise the real vault code
without a database.
"""

import sys
import threading
import time
import types

import pytest

from app.services.course.chaoxing import cookie_vault as vault_mod
from app.services.course.chaoxing.cookie_vault import ChaoxingCookieVault


class FakeTaskStore:
    """Mimics TaskStore.upsert_task/get_task for the vault's kind only."""

    def __init__(self):
        self.rows: dict[tuple[str, str], dict] = {}

    def upsert_task(self, kind, payload):
        self.rows[(kind, payload["task_id"])] = dict(payload)
        return True

    def get_task(self, kind, task_id, user_id=None):
        return self.rows.get((kind, task_id))


@pytest.fixture
def fake_store(monkeypatch):
    store = FakeTaskStore()
    module = types.ModuleType("app.services.course.task_store")
    module.task_store = store
    monkeypatch.setitem(sys.modules, "app.services.course.task_store", module)
    return store


def test_set_persists_the_jar(fake_store):
    vault = ChaoxingCookieVault()
    vault.set("u1", {"_uid": "A"}, nickname="甲", uid="1")

    rows = list(fake_store.rows.values())
    assert len(rows) == 1
    assert rows[0]["credentials"]["cookies"] == {"_uid": "A"}


def test_a_fresh_vault_restores_from_the_store(fake_store):
    """模拟进程重启：新实例的内存是空的，但能读回持久化的 jar。"""
    first = ChaoxingCookieVault()
    first.set("u1", {"_uid": "A", "fid": "1"}, nickname="甲", uid="9")

    restarted = ChaoxingCookieVault()          # 内存空
    assert restarted.get("u1") == {"_uid": "A", "fid": "1"}
    # 恢复后应进内存，第二次不再读存储
    assert "u1" in restarted._entries


def test_summary_also_restores(fake_store):
    """页面第一次调用是 summary，它也必须能恢复，否则显示未登录。"""
    ChaoxingCookieVault().set("u1", {"_uid": "A"}, nickname="甲", uid="9")

    restarted = ChaoxingCookieVault()
    summary = restarted.summary("u1")

    assert summary["logged_in"] is True
    assert summary["nickname"] == "甲"
    assert summary["uid"] == "9"


def test_clear_forgets_the_persisted_row(fake_store):
    vault = ChaoxingCookieVault()
    vault.set("u1", {"_uid": "A"})
    vault.clear("u1")

    restarted = ChaoxingCookieVault()
    assert restarted.get("u1") is None
    assert restarted.summary("u1")["logged_in"] is False


def test_expired_row_is_not_resurrected(fake_store, monkeypatch):
    vault = ChaoxingCookieVault()
    vault.set("u1", {"_uid": "A"})

    # 把持久化记录的时间往前挪，超过 TTL
    key = (vault_mod._VAULT_TASK_KIND, vault_mod._vault_task_id("u1"))
    fake_store.rows[key]["vault_updated_at"] = time.time() - vault_mod._VAULT_TTL_SECONDS - 10

    restarted = ChaoxingCookieVault()
    assert restarted.get("u1") is None


def test_tenants_stay_separate(fake_store):
    vault = ChaoxingCookieVault()
    vault.set("u1", {"_uid": "A"})
    vault.set("u2", {"_uid": "B"})

    restarted = ChaoxingCookieVault()
    assert restarted.get("u1") == {"_uid": "A"}
    assert restarted.get("u2") == {"_uid": "B"}


def test_store_failure_does_not_break_login(fake_store, monkeypatch):
    """持久化只是便利优化；它坏了也不能让扫码登录失败。"""
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(fake_store, "upsert_task", boom)

    vault = ChaoxingCookieVault()
    vault.set("u1", {"_uid": "A"})     # 不应抛

    # 内存里仍然可用
    assert vault.get("u1") == {"_uid": "A"}


def test_restore_failure_is_treated_as_logged_out(fake_store, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(fake_store, "get_task", boom)

    vault = ChaoxingCookieVault()
    assert vault.get("u1") is None
    assert vault.summary("u1")["logged_in"] is False


def test_empty_cookies_are_not_persisted(fake_store):
    vault = ChaoxingCookieVault()
    vault.set("u1", {})

    assert fake_store.rows == {}
    assert vault.get("u1") is None
