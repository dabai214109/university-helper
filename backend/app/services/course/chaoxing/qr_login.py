"""Chaoxing (学习通) QR-code login.

Chaoxing has no official QR-login API; this follows the protocol the mobile
app itself drives, which the community has documented and which still works
against ``passport2.chaoxing.com`` today:

1. ``GET /cloudscanlogin`` renders a page carrying a ``uuid`` and an ``enc``
   hidden input, and sets the ``JSESSIONID`` / ``route`` cookies that every
   later call must reuse.
2. ``GET /createqr?uuid=...`` returns the QR image as a raw stream.
3. ``POST /getauthstatus`` with ``uuid`` + ``enc`` reports progress; once the
   user confirms on their phone the same session has the full login cookie jar
   attached.

Step 3 is classified by the ``type`` field rather than the human-readable
``mes`` string: the endpoint answers in a legacy Chinese encoding, so the
ASCII-safe ``type`` / ``status`` pair is the only reliable signal.

The result of a successful scan is a cookie jar — never a password — which is
exactly what ``ChaoxingAuthService.login(login_with_cookies=True)`` already
consumes. This module therefore does not reimplement any login logic; it only
produces the credential that the existing code path expects.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)

CLOUD_SCAN_LOGIN_URL = "http://passport2.chaoxing.com/cloudscanlogin"
CREATE_QR_URL = "http://passport2.chaoxing.com/createqr"
AUTH_STATUS_URL = "http://passport2.chaoxing.com/getauthstatus"
PC_REFER = "http://i.chaoxing.com"
MOBILE_TIP = "电脑端登录确认"

_QR_LOGIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# getauthstatus `type` codes. `status: true` is the success signal; the codes
# below describe the not-yet-successful states.
_TYPE_UNSCANNED = "3"
_TYPE_EXPIRED = "2"
_TYPE_SCANNED = "4"
_TYPE_CANCELLED = "6"

_QR_TTL_SECONDS = 6 * 60
_POLL_INTERVAL_SECONDS = 1.5
_MAX_QR_REGENERATIONS = 4
# How long a single successful-looking session is trusted before the caller
# should treat the cookies as stale.
_COOKIE_VALIDATION_URL = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata"


class ChaoxingQrLoginError(RuntimeError):
    """Raised when the QR session cannot even be created."""


def _domain_rank(domain: str) -> int:
    """Higher is broader. ``.chaoxing.com`` outranks ``passport2.chaoxing.com``."""
    text = str(domain or "").lstrip(".").strip()
    if not text:
        return 0
    return -text.count(".")


def _collect_cookies(session: requests.Session) -> dict[str, str]:
    """Flatten a cookie jar into a name -> value map, broadest domain wins.

    ``RequestsCookieJar.get_dict()`` keeps one entry per name and which one
    wins depends on jar order. Chaoxing sets the same names on both
    ``passport2.chaoxing.com`` and ``.chaoxing.com``; the API hosts
    (``mooc2-ans``, ``mobilelearn``) need the broad-domain value, so rank by
    domain breadth instead of trusting iteration order.
    """
    best: dict[str, tuple[int, str]] = {}
    for cookie in session.cookies:
        name = str(cookie.name or "")
        if not name:
            continue
        rank = _domain_rank(str(cookie.domain or ""))
        value = str(cookie.value or "")
        current = best.get(name)
        if current is None or rank >= current[0]:
            best[name] = (rank, value)
    return {name: value for name, (_rank, value) in best.items()}


def _validate_cookies(cookies: dict[str, str]) -> bool:
    """Confirm the jar is a live Chaoxing session.

    Mirrors ``ChaoxingAuthService._validate_cookie_session``: the presence of
    ``_uid`` is necessary but not sufficient, so one authenticated call decides.
    """
    if not cookies.get("_uid") and not cookies.get("UID"):
        return False

    probe = requests.Session()
    probe.headers.update(_QR_LOGIN_HEADERS)
    probe.cookies.update(cookies)
    try:
        resp = probe.post(
            _COOKIE_VALIDATION_URL,
            data={"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0},
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.debug("QR cookie validation request failed: %s", exc)
        return False

    if resp.status_code != 200:
        return False
    body = resp.text
    if "passport2.chaoxing.com" in body or "login" in body.lower():
        return False
    return True


def _extract_hidden_input(html_text: str, input_id: str) -> str:
    """Read a hidden ``<input>`` value by id, tolerating attribute order."""
    pattern = re.compile(
        r"<input\b[^>]*\bid\s*=\s*[\"']?" + re.escape(input_id) + r"[\"']?[^>]*>",
        re.IGNORECASE,
    )
    for match in pattern.finditer(html_text):
        tag = match.group(0)
        value = re.search(r"\bvalue\s*=\s*[\"']([^\"']*)[\"']", tag, re.IGNORECASE)
        if value and value.group(1).strip():
            return value.group(1).strip()
    return ""


def _start_qr_session(session: requests.Session) -> tuple[str, str]:
    """Fetch a fresh ``uuid`` / ``enc`` pair, reusing the caller's cookie jar."""
    resp = session.get(
        CLOUD_SCAN_LOGIN_URL,
        params={"mobiletip": MOBILE_TIP, "pcrefer": PC_REFER},
        headers={"Referer": PC_REFER},
        timeout=15,
    )
    resp.raise_for_status()

    uuid = _extract_hidden_input(resp.text, "uuid")
    enc = _extract_hidden_input(resp.text, "enc")
    if not uuid or not enc:
        raise ChaoxingQrLoginError("Chaoxing QR login page did not contain uuid/enc")
    return uuid, enc


def _fetch_qr_image(session: requests.Session, uuid: str) -> bytes:
    resp = session.get(
        CREATE_QR_URL,
        params={"uuid": uuid, "xxtrefer": "", "type": "1", "mobiletip": MOBILE_TIP},
        headers={"Referer": CLOUD_SCAN_LOGIN_URL},
        timeout=15,
    )
    resp.raise_for_status()
    if not resp.content:
        raise ChaoxingQrLoginError("Chaoxing returned an empty QR image")
    return resp.content


def _read_auth_status(session: requests.Session, uuid: str, enc: str) -> dict[str, Any]:
    resp = session.post(
        AUTH_STATUS_URL,
        data={"uuid": uuid, "enc": enc},
        headers={"Referer": CLOUD_SCAN_LOGIN_URL},
        timeout=15,
    )
    resp.raise_for_status()
    # The endpoint answers in a legacy Chinese encoding; only ASCII fields
    # (`type`, `status`, `uid`) are consumed, so decode permissively.
    try:
        payload = json.loads(resp.content.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ChaoxingQrLoginError(f"Chaoxing returned a non-JSON auth status: {exc}") from exc
    if not isinstance(payload, dict):
        raise ChaoxingQrLoginError("Chaoxing returned an unexpected auth status payload")
    return payload


def login_with_qr(
    qr_callback: Callable[[bytes], None],
    *,
    cancel_event: threading.Event | None = None,
    status_callback: Callable[[str, str], None] | None = None,
    ttl_seconds: int = _QR_TTL_SECONDS,
    poll_interval: float = _POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Drive the scan/confirm handshake and return the resulting cookie jar.

    ``qr_callback`` receives the PNG/JPEG bytes each time a code is rendered
    (including after an expiry-driven regeneration). ``status_callback`` is
    optional and reports the intermediate ``scanned`` state so a polling client
    can tell the user to confirm on their phone.

    Never raises for an ordinary failed scan — the outcome is in the returned
    dict — so the caller can publish it as session state.
    """
    session = requests.Session()
    session.headers.update(_QR_LOGIN_HEADERS)

    deadline = time.monotonic() + max(30, int(ttl_seconds))
    regenerations = 0
    last_reported: tuple[str, str] | None = None

    def _report(state: str, message: str) -> None:
        nonlocal last_reported
        if last_reported == (state, message):
            return
        last_reported = (state, message)
        if status_callback is not None:
            try:
                status_callback(state, message)
            except Exception:  # pragma: no cover - observer must not break login
                logger.exception("Chaoxing QR status callback failed")

    try:
        uuid, enc = _start_qr_session(session)
        qr_callback(_fetch_qr_image(session, uuid))
    except (requests.RequestException, ChaoxingQrLoginError) as exc:
        logger.warning("Chaoxing QR session setup failed: %s", exc)
        return {"success": False, "message": f"无法获取学习通二维码：{exc}"}

    _report("pending", "请使用学习通 App 扫描二维码")

    while True:
        if cancel_event is not None and cancel_event.is_set():
            return {"success": False, "message": "登录已取消"}

        if time.monotonic() >= deadline:
            return {"success": False, "message": "二维码登录超时，请重新获取"}

        try:
            payload = _read_auth_status(session, uuid, enc)
        except (requests.RequestException, ChaoxingQrLoginError) as exc:
            logger.debug("Chaoxing QR status poll failed: %s", exc)
            time.sleep(poll_interval)
            continue

        if payload.get("status") is True:
            cookies = _collect_cookies(session)
            if not _validate_cookies(cookies):
                return {
                    "success": False,
                    "message": "扫码成功但登录态校验失败，请重新扫码或改用账号密码登录",
                }
            nickname = str(payload.get("nickname") or "").strip()
            uid = str(payload.get("uid") or cookies.get("_uid") or "").strip()
            _report("success", "登录成功")
            return {
                "success": True,
                "message": "登录成功",
                "cookies": cookies,
                "nickname": nickname,
                "uid": uid,
            }

        status_type = str(payload.get("type") or "").strip()

        if status_type == _TYPE_SCANNED:
            nickname = str(payload.get("nickname") or "").strip()
            message = f"{nickname} 已扫码，请在手机上确认登录" if nickname else "已扫码，请在手机上确认登录"
            _report("scanned", message)

        elif status_type == _TYPE_EXPIRED:
            regenerations += 1
            if regenerations > _MAX_QR_REGENERATIONS:
                return {"success": False, "message": "二维码多次失效，请重新获取"}
            try:
                uuid, enc = _start_qr_session(session)
                qr_callback(_fetch_qr_image(session, uuid))
            except (requests.RequestException, ChaoxingQrLoginError) as exc:
                logger.warning("Chaoxing QR regeneration failed: %s", exc)
                return {"success": False, "message": f"二维码刷新失败：{exc}"}
            _report("pending", "二维码已刷新，请重新扫描")

        elif status_type == _TYPE_CANCELLED:
            return {"success": False, "message": "已在手机端取消登录"}

        time.sleep(poll_interval)
