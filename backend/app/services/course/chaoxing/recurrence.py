"""Recurrence rules for scheduled Chaoxing learning tasks.

A *one-shot* task (`repeat="once"`) fires once at an absolute `start_at` and is
the historical behaviour. A *recurring* task (`daily` / `every_other_day`) is a
long-lived template that fires at a wall-clock time each period; every firing
produces a separate child task so the history stays per-run.

Keeping the date arithmetic here (pure functions, no manager state) means the
scheduling rules can be tested without the storage/notification stack.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

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
        raise RecurrenceError(
            f"Invalid repeat: {raw!r}. Allowed: {', '.join(sorted(VALID_REPEATS))}"
        )
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


def _period_days(repeat: str) -> int:
    return 2 if repeat == REPEAT_EVERY_OTHER_DAY else 1


def first_occurrence(
    repeat: str,
    time_of_day: time,
    *,
    now: datetime,
    anchor_date: datetime.date | None = None,
) -> datetime:
    """Return the first occurrence strictly after ``now``.

    ``every_other_day`` counts in 2-day steps from ``anchor_date`` (defaulting to
    ``now``'s date), so the cadence is stable across restarts instead of drifting
    with whatever moment the task happened to be created.
    """
    now = _as_utc(now)
    candidate = datetime.combine(now.date(), time_of_day, tzinfo=UTC)

    if repeat == REPEAT_DAILY:
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    if repeat == REPEAT_EVERY_OTHER_DAY:
        anchor = anchor_date or now.date()
        # Walk forward from the anchor in 2-day steps to the first slot after now.
        days_ahead = (now.date() - anchor).days
        if days_ahead < 0:
            # The anchor is in the future: the first slot IS the anchor's slot.
            return datetime.combine(anchor, time_of_day, tzinfo=UTC)
        steps = days_ahead // 2
        candidate = datetime.combine(anchor + timedelta(days=steps * 2), time_of_day, tzinfo=UTC)
        while candidate <= now:
            candidate += timedelta(days=2)
        return candidate

    raise RecurrenceError(f"first_occurrence called with non-recurring repeat: {repeat!r}")


def next_occurrence(repeat: str, previous: datetime, time_of_day: time) -> datetime:
    """Advance one period from ``previous``.

    Always returns a time strictly after ``previous``; the caller is responsible
    for skipping past occurrences when catching up after a restart.
    """
    previous = _as_utc(previous)
    step = timedelta(days=_period_days(repeat))
    candidate = previous + step
    # Re-anchor the wall-clock time: `previous` may carry jitter, and the next
    # slot must land on the configured time, not on the jittered one.
    candidate = datetime.combine(candidate.date(), time_of_day, tzinfo=UTC)
    if candidate <= previous:
        candidate += step
    return candidate


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
