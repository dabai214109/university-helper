"""Recurrence rules for scheduled Chaoxing learning tasks.

A *one-shot* task (`repeat="once"`) fires once at an absolute `start_at` and is
the historical behaviour. A *recurring* task (`daily` / `every_other_day`) is a
long-lived template that fires at a wall-clock time each period; every firing
produces a separate child task so the history stays per-run.

Keeping the date arithmetic here (pure functions, no manager state) means the
scheduling rules can be tested without the storage/notification stack.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

REPEAT_ONCE = "once"
REPEAT_DAILY = "daily"
REPEAT_EVERY_OTHER_DAY = "every_other_day"

VALID_REPEATS = frozenset({REPEAT_ONCE, REPEAT_DAILY, REPEAT_EVERY_OTHER_DAY})

# Safety rails on a single occurrence. A task that runs longer than this is
# almost certainly stuck, and an unbounded run would hold a worker slot.
MIN_DURATION_MIN = 1
MAX_DURATION_MIN = 24 * 60


class RecurrenceError(ValueError):
    """Raised for a malformed recurrence specification."""


def normalize_repeat(raw: object) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return REPEAT_ONCE
    if text not in VALID_REPEATS:
        raise RecurrenceError(f"Invalid repeat: {raw!r}. Allowed: {', '.join(sorted(VALID_REPEATS))}")
    return text


def is_recurring(repeat: object) -> bool:
    return normalize_repeat(repeat) != REPEAT_ONCE


def parse_time_of_day(raw: object) -> time:
    """Parse ``"HH:MM"`` (seconds optional) into a ``datetime.time``."""
    text = str(raw or "").strip()
    if not text:
        raise RecurrenceError("time_of_day is required for a recurring task")
    parts = text.split(":")
    if len(parts) not in (2, 3):
        raise RecurrenceError(f"Invalid time_of_day: {raw!r}. Expected HH:MM")
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except (TypeError, ValueError) as exc:
        raise RecurrenceError(f"Invalid time_of_day: {raw!r}. Expected HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise RecurrenceError(f"Invalid time_of_day: {raw!r}. Expected HH:MM")
    return time(hour=hour, minute=minute, second=second)


def parse_anchor_date(raw: object) -> datetime.date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except (TypeError, ValueError) as exc:
        raise RecurrenceError(f"Invalid anchor_date: {raw!r}. Expected YYYY-MM-DD") from exc


def clamp_duration_minutes(raw: object) -> int | None:
    """Clamp the per-run duration cap; ``None``/blank means "no cap"."""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(float(raw))
    except (TypeError, ValueError) as exc:
        raise RecurrenceError(f"Invalid max_duration_min: {raw!r}") from exc
    return max(MIN_DURATION_MIN, min(value, MAX_DURATION_MIN))


DEFAULT_SCHEDULE_TIMEZONE = "Asia/Shanghai"


def resolve_timezone(name: object = None) -> tzinfo:
    """把 IANA 时区名解析成 tzinfo；无效名字回退到默认值。

    用户的「每天 08:00」指的是**本地**早上 8 点。存储层统一用 UTC，
    所以只在「把 HH:MM 落到某一天」时用这个时区换算。

    用 ZoneInfo 而不是固定偏移，夏令时地区才不会在切换日错一小时。

    注意：slim 镜像**不一定自带 tzdata**。缺了它 ZoneInfo 抛异常，
    此时回退到 UTC 会让「每天 8 点」又偏 8 小时 —— 所以这里打 warning，
    而不是静默降级。Dockerfile.server 已显式安装 tzdata。
    """
    candidates = [str(name or "").strip(), DEFAULT_SCHEDULE_TIMEZONE, "UTC"]
    failures: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            failures.append(candidate)
            continue
    logger.warning(
        "SCHEDULE_TIMEZONE could not be resolved (%s); falling back to UTC. "
        "Scheduled wall-clock times will be interpreted as UTC — install tzdata "
        "or set a valid IANA name.",
        ", ".join(failures) or "(empty)",
    )
    return UTC


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _period_days(repeat: str) -> int:
    return 2 if repeat == REPEAT_EVERY_OTHER_DAY else 1


def first_occurrence(
    repeat: str,
    time_of_day: time,
    *,
    now: datetime,
    anchor_date: datetime.date | None = None,
    tz: tzinfo | None = None,
) -> datetime:
    """Return the first occurrence strictly after ``now``.

    ``every_other_day`` counts in 2-day steps from ``anchor_date`` (defaulting to
    ``now``'s date), so the cadence is stable across restarts instead of drifting
    with whatever moment the task happened to be created.

    ``tz`` is the timezone the user meant when they typed the wall-clock time.
    The arithmetic is done in that zone (so "08:00" means 8am *there*), and the
    result is returned in UTC because that is how occurrences are stored and
    compared everywhere else. Passing ``tz=None`` keeps the old UTC-only
    behaviour, which is what the unit tests assert for pure date logic.
    """
    zone = tz or UTC
    now_utc = _as_utc(now)
    # Work in local wall-clock terms: "today" and "08:00" are the user's.
    local_now = now_utc.astimezone(zone)

    def slot(day: datetime.date) -> datetime:
        return datetime.combine(day, time_of_day, tzinfo=zone)

    if repeat == REPEAT_DAILY:
        candidate = slot(local_now.date())
        if candidate <= local_now:
            candidate = slot(local_now.date() + timedelta(days=1))
        return candidate.astimezone(UTC)

    if repeat == REPEAT_EVERY_OTHER_DAY:
        anchor = anchor_date or local_now.date()
        days_ahead = (local_now.date() - anchor).days
        if days_ahead < 0:
            # The anchor is in the future: the first slot IS the anchor's slot.
            return slot(anchor).astimezone(UTC)
        steps = days_ahead // 2
        candidate = slot(anchor + timedelta(days=steps * 2))
        while candidate <= local_now:
            candidate = slot(candidate.date() + timedelta(days=2))
        return candidate.astimezone(UTC)

    raise RecurrenceError(f"first_occurrence called with non-recurring repeat: {repeat!r}")


def next_occurrence(
    repeat: str,
    previous: datetime,
    time_of_day: time,
    *,
    tz: tzinfo | None = None,
) -> datetime:
    """Advance one period from ``previous``.

    Always returns a time strictly after ``previous``; the caller is responsible
    for skipping past occurrences when catching up after a restart.

    ``tz`` must be the same zone ``first_occurrence`` used, otherwise the day
    boundary shifts and a "daily 08:00" task can drift by a day.
    """
    zone = tz or UTC
    previous_utc = _as_utc(previous)
    local_previous = previous_utc.astimezone(zone)
    step = timedelta(days=_period_days(repeat))
    # Re-anchor the wall-clock time: `previous` may carry jitter, and the next
    # slot must land on the configured time, not on the jittered one.
    candidate = datetime.combine(local_previous.date() + step, time_of_day, tzinfo=zone)
    if candidate <= local_previous:
        candidate = datetime.combine(candidate.date() + step, time_of_day, tzinfo=zone)
    return candidate.astimezone(UTC)


def describe_schedule(schedule: dict[str, Any]) -> str:
    """Human-readable one-liner for logs and task messages."""
    repeat = str(schedule.get("repeat") or REPEAT_ONCE)
    if repeat == REPEAT_DAILY:
        label = "每天"
    elif repeat == REPEAT_EVERY_OTHER_DAY:
        label = "隔天"
    else:
        return "仅一次"
    return f"{label} {schedule.get('time_of_day') or '--'}"
