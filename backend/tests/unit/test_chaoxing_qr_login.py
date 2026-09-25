"""Tests for the Chaoxing QR-code login flow.

The protocol itself is third-party and unreachable from CI, so these tests pin
the *parsing and state-machine* contract instead:

- the hidden ``uuid`` / ``enc`` inputs are read regardless of attribute order,
- each documented ``getauthstatus`` reply maps to the right session state,
- a successful scan yields a cookie jar, never a password,
- the per-user vault never leaks one tenant's jar to another.
"""

import json

import requests

from app.services.course.chaoxing import qr_login
from app.services.course.chaoxing.cookie_vault import ChaoxingCookieVault


class _FakeResponse:
    def __init__(self, *, text="", content=b"", status_code=200):
        self.text = text
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise qr_login.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self.content.decode("utf-8"))


class _FakeSession:
    """Scripted stand-in for ``requests.Session``."""

    def __init__(self, *, login_html, qr_bytes=b"\x89PNG-qr", statuses=(), cookies=None):
        self.headers = {}
        # A real jar, because ``_collect_cookies`` iterates cookie objects.
        self.cookies = requests.cookies.RequestsCookieJar()
        for name, value in (cookies or {}).items():
            self.cookies.set(name, value, domain=".chaoxing.com")
        self._login_html = login_html
        self._qr_bytes = qr_bytes
        self._statuses = list(statuses)
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if url == qr_login.CLOUD_SCAN_LOGIN_URL:
            return _FakeResponse(text=self._login_html)
        return _FakeResponse(content=self._qr_bytes)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        payload = self._statuses.pop(0) if self._statuses else {"type": "3", "status": False}
        if payload.get("status") is True:
            # Mirror Chaoxing: a confirmed scan attaches the login jar to the
            # very session that polled for it.
            self.cookies.set("_uid", str(payload.get("uid") or "1"), domain=".chaoxing.com")
        return _FakeResponse(content=json.dumps(payload).encode("utf-8"))


# ── hidden-input parsing ──────────────────────────────────────────────────────


def test_extract_hidden_input_handles_either_attribute_order():
    html = (
        '<input type = "hidden" value="UUID-VALUE" id = "uuid"/>'
        '<input id="enc" type="hidden" value="ENC-VALUE">'
    )
    assert qr_login._extract_hidden_input(html, "uuid") == "UUID-VALUE"
    assert qr_login._extract_hidden_input(html, "enc") == "ENC-VALUE"


def test_extract_hidden_input_returns_empty_when_absent():
    assert qr_login._extract_hidden_input("<html></html>", "uuid") == ""


# ── cookie jar flattening ─────────────────────────────────────────────────────


def test_collect_cookies_prefers_the_broadest_domain():
    """``mooc2-ans`` needs the ``.chaoxing.com`` value, not passport2's."""

    class _Cookie:
        def __init__(self, name, value, domain):
            self.name = name
            self.value = value
            self.domain = domain

    class _Jar:
        def __iter__(self):
            # passport2 (narrow) is yielded last, so a naive get_dict() would
            # pick the wrong value.
            return iter(
                [
                    _Cookie("_uid", "BROAD", ".chaoxing.com"),
                    _Cookie("_uid", "NARROW", "passport2.chaoxing.com"),
                ]
            )

    class _Session:
        cookies = _Jar()

    assert qr_login._collect_cookies(_Session()) == {"_uid": "BROAD"}


# ── state machine ─────────────────────────────────────────────────────────────


def _run(monkeypatch, *, login_html, statuses, validate=True):
    session = _FakeSession(login_html=login_html, statuses=statuses)
    monkeypatch.setattr(qr_login.requests, "Session", lambda: session)
    monkeypatch.setattr(qr_login, "_validate_cookies", lambda cookies: validate)

    images = []
    states = []
    result = qr_login.login_with_qr(
        images.append,
        status_callback=lambda state, message: states.append(state),
        poll_interval=0,
        ttl_seconds=30,
    )
    return result, images, states, session


_LOGIN_HTML = (
    '<input type = "hidden" value="U-1" id = "uuid"/>'
    '<input type = "hidden" value="E-1" id = "enc"/>'
)


def test_successful_scan_returns_cookies_and_reports_scanned(monkeypatch):
    result, images, states, _ = _run(
        monkeypatch,
        login_html=_LOGIN_HTML,
        statuses=[
            {"mes": "已扫描", "type": "4", "status": False, "uid": "82659775", "nickname": "爱花花"},
            {"mes": "验证通过", "status": True, "uid": "82659775", "nickname": "爱花花"},
        ],
    )

    assert result["success"] is True
    assert result["nickname"] == "爱花花"
    assert result["uid"] == "82659775"
    assert result["cookies"]["_uid"] == "82659775"
    # The QR image is rendered once, and the intermediate scan state is surfaced.
    assert images == [b"\x89PNG-qr"]
    assert states == ["pending", "scanned", "success"]


def test_unscanned_qr_reports_pending_without_succeeding(monkeypatch):
    result, _images, states, _ = _run(
        monkeypatch,
        login_html=_LOGIN_HTML,
        statuses=[{"mes": "未登录", "type": "3", "status": False}],
    )
    assert result["success"] is False
    assert "超时" in result["message"]
    assert states == ["pending"]


def test_cancelled_scan_is_reported_as_a_failure(monkeypatch):
    result, _images, _states, _ = _run(
        monkeypatch,
        login_html=_LOGIN_HTML,
        statuses=[{"mes": "用户手机端取消登录", "type": "6", "status": False}],
    )
    assert result["success"] is False
    assert result["message"] == "已在手机端取消登录"


def test_failed_cookie_validation_does_not_report_success(monkeypatch):
    result, _images, states, _ = _run(
        monkeypatch,
        login_html=_LOGIN_HTML,
        statuses=[{"mes": "验证通过", "status": True}],
        validate=False,
    )
    assert result["success"] is False
    assert "校验失败" in result["message"]
    assert "success" not in states


def test_expired_qr_is_regenerated_for_the_caller(monkeypatch):
    result, images, states, session = _run(
        monkeypatch,
        login_html=_LOGIN_HTML,
        statuses=[
            {"mes": "二维码已失效", "type": "2", "status": False},
            {"mes": "验证通过", "status": True, "uid": "1"},
        ],
    )
    assert result["success"] is True
    # A second code was rendered after expiry.
    assert len(images) == 2
    assert "pending" in states
    # Two cloudscanlogin + two createqr GETs.
    assert len([c for c in session.get_calls if c[0] == qr_login.CLOUD_SCAN_LOGIN_URL]) == 2


def test_missing_uuid_reports_setup_failure_instead_of_raising(monkeypatch):
    result, _images, _states, _ = _run(
        monkeypatch,
        login_html="<html>no inputs here</html>",
        statuses=[],
    )
    assert result["success"] is False
    assert "无法获取学习通二维码" in result["message"]


def test_cancel_event_stops_the_loop(monkeypatch):
    import threading

    cancel_event = threading.Event()
    cancel_event.set()

    session = _FakeSession(login_html=_LOGIN_HTML)
    monkeypatch.setattr(qr_login.requests, "Session", lambda: session)
    monkeypatch.setattr(qr_login, "_validate_cookies", lambda cookies: True)

    result = qr_login.login_with_qr(
        lambda _img: None, cancel_event=cancel_event, poll_interval=0, ttl_seconds=30
    )
    assert result["success"] is False
    assert result["message"] == "登录已取消"


# ── per-user vault ────────────────────────────────────────────────────────────


def test_vault_keeps_tenants_separate():
    vault = ChaoxingCookieVault()
    vault.set("user-a", {"_uid": "A"}, nickname="甲")
    vault.set("user-b", {"_uid": "B"}, nickname="乙")

    assert vault.get("user-a") == {"_uid": "A"}
    assert vault.get("user-b") == {"_uid": "B"}

    vault.clear("user-a")
    assert vault.get("user-a") is None
    # Clearing one tenant must never touch another's session.
    assert vault.get("user-b") == {"_uid": "B"}


def test_vault_rejects_empty_input_and_reports_summary():
    vault = ChaoxingCookieVault()
    vault.set("user-a", {})

    assert vault.get("user-a") is None
    assert vault.summary("user-a") == {
        "logged_in": False,
        "nickname": "",
        "uid": "",
        "updated_at": 0.0,
    }

    vault.set("user-a", {"_uid": "A"}, nickname="甲", uid="9")
    summary = vault.summary("user-a")
    assert summary["logged_in"] is True
    assert summary["nickname"] == "甲"
    assert summary["uid"] == "9"


def test_vault_expires_stale_entries():
    vault = ChaoxingCookieVault()
    vault.set("user-a", {"_uid": "A"})

    # Age the entry past the vault's TTL instead of waiting for it.
    vault._entries["user-a"]["updated_at"] -= 8 * 24 * 60 * 60

    assert vault.get("user-a") is None
    assert vault.summary("user-a")["logged_in"] is False
