"""A container restart empties the in-memory client registry but not the vault.

Before this, the read paths consulted only memory, so after every deploy the
page reported "logged in" (the vault still had the jar) while /courses returned
an empty list, and the UI fell back to the login card until the user re-scanned.

These tests load the real ``signin`` module through a namespace package, the
same way tools/run_qr_tests.py does: the app's ``__init__`` chain needs
``pydantic_settings``, which this machine does not have, and the behaviour under
test needs none of it.
"""

import time
import types

import app.services.course.chaoxing.signin as signin_module
from app.services.course.chaoxing.signin import ChaoxingSigninManager


def _manager():
    # __new__ skips __init__, which restores task/history state from the store.
    manager = ChaoxingSigninManager.__new__(ChaoxingSigninManager)
    import threading

    manager._lock = threading.Lock()
    manager._clients = {}
    manager._rebuild_failed_at = {}
    manager._history = {}
    manager._tasks = {}
    manager._loaded_task_users = set()
    manager._loaded_history_users = set()
    return manager


def _fake_client(courses=("C1", "C2"), uid="472030121"):
    class _Client:
        def __init__(self):
            self.uid = uid

        def get_courses(self):
            return [{"courseId": c} for c in courses]

        def get_class_subjects(self):
            return []

    return _Client()


def _vault(jar):
    return types.SimpleNamespace(get=lambda uid: jar)


def test_get_courses_rebuilds_from_the_vault_after_a_restart(monkeypatch):
    manager = _manager()
    adopted = {}

    def fake_login_with_cookie_jar(user_id, cookies):
        adopted[user_id] = dict(cookies)
        manager._clients[user_id] = _fake_client()
        return {"status": True}

    monkeypatch.setattr(signin_module, "chaoxing_cookie_vault", _vault({"_uid": "472030121"}))
    monkeypatch.setattr(manager, "login_with_cookie_jar", fake_login_with_cookie_jar)

    courses = manager.get_courses("4")

    assert adopted == {"4": {"_uid": "472030121"}}, "the stored jar must be adopted"
    assert [c["courseId"] for c in courses] == ["C1", "C2"], "courses must come back, not []"


def test_has_client_reports_a_restored_session(monkeypatch):
    manager = _manager()

    def fake_login(user_id, cookies):
        manager._clients[user_id] = _fake_client()
        return {"status": True}

    monkeypatch.setattr(signin_module, "chaoxing_cookie_vault", _vault({"_uid": "9"}))
    monkeypatch.setattr(manager, "login_with_cookie_jar", fake_login)

    assert manager.has_client("4") is True


def test_no_vault_entry_means_no_rebuild_attempt(monkeypatch):
    manager = _manager()
    called = []
    monkeypatch.setattr(signin_module, "chaoxing_cookie_vault", _vault(None))
    monkeypatch.setattr(
        manager, "login_with_cookie_jar", lambda u, c: called.append(u) or {"status": True}
    )

    assert manager.get_courses("4") == []
    assert called == [], "without a jar there is nothing to adopt"


def test_an_unusable_jar_is_not_revalidated_on_every_poll(monkeypatch):
    """/login-status is polled; a dead jar must not hit Chaoxing each time."""
    manager = _manager()
    attempts = []
    monkeypatch.setattr(signin_module, "chaoxing_cookie_vault", _vault({"_uid": "dead"}))
    monkeypatch.setattr(
        manager,
        "login_with_cookie_jar",
        lambda user_id, cookies: attempts.append(user_id) or {"status": False},
    )

    assert manager.get_courses("4") == []
    assert manager.get_courses("4") == []
    assert manager.get_courses("4") == []
    assert len(attempts) == 1, "the retry window must suppress repeat validation"


def test_a_later_poll_retries_once_the_window_expires(monkeypatch):
    manager = _manager()
    attempts = []
    monkeypatch.setattr(signin_module, "chaoxing_cookie_vault", _vault({"_uid": "x"}))
    monkeypatch.setattr(
        manager,
        "login_with_cookie_jar",
        lambda user_id, cookies: attempts.append(user_id) or {"status": False},
    )

    manager.get_courses("4")
    # Pretend the retry deadline has passed.
    manager._rebuild_failed_at["4"] = time.monotonic() - 1
    manager.get_courses("4")

    assert len(attempts) == 2


def test_fresh_login_bypasses_the_retry_window(monkeypatch):
    """A new scan installs a client directly, so it must not wait out the block."""
    manager = _manager()
    monkeypatch.setattr(signin_module, "chaoxing_cookie_vault", _vault({"_uid": "old"}))
    monkeypatch.setattr(manager, "login_with_cookie_jar", lambda u, c: {"status": False})

    manager.get_courses("4")  # blocks rebuilds for a while
    assert manager._rebuild_failed_at.get("4")

    # Simulate the user re-scanning: login_with_cookie_jar installs a client.
    manager._clients["4"] = _fake_client()

    assert [c["courseId"] for c in manager.get_courses("4")] == ["C1", "C2"]
