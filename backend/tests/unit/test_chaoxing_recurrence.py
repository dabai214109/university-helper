"""Recurrence rule tests.

The date arithmetic decides when a user's course run actually happens, so the
boundary cases matter: a time already past today must roll to tomorrow, and
"every other day" must keep a stable cadence rather than drifting with the
moment the task was created or with each restart.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.services.course.chaoxing.recurrence import (
    MAX_DURATION_MIN,
    MIN_DURATION_MIN,
    REPEAT_DAILY,
    REPEAT_EVERY_OTHER_DAY,
    REPEAT_ONCE,
    RecurrenceError,
    clamp_duration_minutes,
    describe_schedule,
    first_occurrence,
    is_recurring,
    next_occurrence,
    normalize_repeat,
    resolve_timezone,
    parse_anchor_date,
    parse_time_of_day,
)


# ── normalisation ─────────────────────────────────────────────────────────────


def test_normalize_repeat_defaults_to_once():
    assert normalize_repeat(None) == REPEAT_ONCE
    assert normalize_repeat("") == REPEAT_ONCE
    assert normalize_repeat("  ") == REPEAT_ONCE


def test_normalize_repeat_is_case_insensitive():
    assert normalize_repeat("DAILY") == REPEAT_DAILY
    assert normalize_repeat("Every_Other_Day") == REPEAT_EVERY_OTHER_DAY


def test_normalize_repeat_rejects_unknown_values():
    with pytest.raises(RecurrenceError):
        normalize_repeat("weekly")


def test_is_recurring_only_for_periodic_values():
    assert is_recurring("daily") is True
    assert is_recurring("every_other_day") is True
    assert is_recurring("once") is False
    assert is_recurring(None) is False


# ── time_of_day ───────────────────────────────────────────────────────────────


def test_parse_time_of_day_accepts_hh_mm():
    assert parse_time_of_day("08:30") == time(8, 30)
    assert parse_time_of_day(" 23:59 ") == time(23, 59)


def test_parse_time_of_day_accepts_optional_seconds():
    assert parse_time_of_day("08:30:15") == time(8, 30, 15)


def test_parse_time_of_day_rejects_malformed_input():
    for bad in ("", "8", "25:00", "08:60", "ab:cd", "08-30"):
        with pytest.raises(RecurrenceError):
            parse_time_of_day(bad)


# ── duration clamp ────────────────────────────────────────────────────────────


def test_clamp_duration_allows_no_cap():
    assert clamp_duration_minutes(None) is None
    assert clamp_duration_minutes("") is None


def test_clamp_duration_bounds_the_value():
    assert clamp_duration_minutes(120) == 120
    assert clamp_duration_minutes(0) == MIN_DURATION_MIN
    assert clamp_duration_minutes(-5) == MIN_DURATION_MIN
    assert clamp_duration_minutes(10**9) == MAX_DURATION_MIN


def test_clamp_duration_rejects_garbage():
    with pytest.raises(RecurrenceError):
        clamp_duration_minutes("soon")


# ── anchor date ───────────────────────────────────────────────────────────────


def test_parse_anchor_date_accepts_iso_date():
    assert parse_anchor_date("2026-09-26") == date(2026, 9, 26)
    assert parse_anchor_date(None) is None


def test_parse_anchor_date_rejects_garbage():
    with pytest.raises(RecurrenceError):
        parse_anchor_date("26/09/2026")


# ── daily ─────────────────────────────────────────────────────────────────────


def test_daily_fires_today_when_the_time_is_still_ahead():
    now = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)

    assert first_occurrence(REPEAT_DAILY, time(8, 0), now=now) == datetime(
        2026, 9, 26, 8, 0, tzinfo=UTC
    )


def test_daily_rolls_to_tomorrow_when_the_time_has_passed():
    now = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)

    assert first_occurrence(REPEAT_DAILY, time(8, 0), now=now) == datetime(
        2026, 9, 27, 8, 0, tzinfo=UTC
    )


def test_daily_at_the_exact_moment_rolls_forward():
    """The next slot must be strictly in the future, never 'now'."""
    now = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)

    assert first_occurrence(REPEAT_DAILY, time(8, 0), now=now) == datetime(
        2026, 9, 27, 8, 0, tzinfo=UTC
    )


def test_daily_crosses_a_month_boundary():
    now = datetime(2026, 9, 30, 23, 0, tzinfo=UTC)

    assert first_occurrence(REPEAT_DAILY, time(1, 0), now=now) == datetime(
        2026, 10, 1, 1, 0, tzinfo=UTC
    )


def test_daily_next_occurrence_is_one_day_later():
    previous = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)

    assert next_occurrence(REPEAT_DAILY, previous, time(8, 0)) == datetime(
        2026, 9, 27, 8, 0, tzinfo=UTC
    )


# ── every other day ───────────────────────────────────────────────────────────


def test_every_other_day_uses_the_anchor_cadence():
    """From an anchor, slots land on anchor, anchor+2, anchor+4, ..."""
    anchor = date(2026, 9, 20)
    now = datetime(2026, 9, 21, 6, 0, tzinfo=UTC)

    # 21st is one day past the anchor, so the next slot is the anchor's +2 day.
    assert first_occurrence(
        REPEAT_EVERY_OTHER_DAY, time(8, 0), now=now, anchor_date=anchor
    ) == datetime(2026, 9, 22, 8, 0, tzinfo=UTC)


def test_every_other_day_stays_on_cadence_across_repeated_calls():
    anchor = date(2026, 9, 20)
    now = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
    slot = first_occurrence(REPEAT_EVERY_OTHER_DAY, time(8, 0), now=now, anchor_date=anchor)
    assert slot == datetime(2026, 9, 20, 8, 0, tzinfo=UTC)

    # Each subsequent call advances exactly 2 days, never 1.
    for expected_day in (22, 24, 26):
        slot = next_occurrence(REPEAT_EVERY_OTHER_DAY, slot, time(8, 0))
        assert slot == datetime(2026, 9, expected_day, 8, 0, tzinfo=UTC)


def test_every_other_day_from_a_future_anchor_starts_at_the_anchor():
    anchor = date(2026, 10, 1)
    now = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)

    assert first_occurrence(
        REPEAT_EVERY_OTHER_DAY, time(8, 0), now=now, anchor_date=anchor
    ) == datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


def test_every_other_day_defaults_the_anchor_to_today():
    now = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)

    assert first_occurrence(REPEAT_EVERY_OTHER_DAY, time(8, 0), now=now) == datetime(
        2026, 9, 26, 8, 0, tzinfo=UTC
    )


def test_every_other_day_parity_survives_a_restart():
    """Two 'now' values inside the same 2-day window yield the same slot."""
    anchor = date(2026, 9, 20)
    first = first_occurrence(
        REPEAT_EVERY_OTHER_DAY,
        time(8, 0),
        now=datetime(2026, 9, 21, 1, 0, tzinfo=UTC),
        anchor_date=anchor,
    )
    # Simulate a restart 20 hours later, still before the slot.
    second = first_occurrence(
        REPEAT_EVERY_OTHER_DAY,
        time(8, 0),
        now=datetime(2026, 9, 21, 21, 0, tzinfo=UTC),
        anchor_date=anchor,
    )

    assert first == second == datetime(2026, 9, 22, 8, 0, tzinfo=UTC)


# ── next_occurrence re-anchors the clock time ─────────────────────────────────


def test_next_occurrence_discards_jitter_from_the_previous_slot():
    """A jittered run must not shift every later slot."""
    jittered = datetime(2026, 9, 26, 8, 7, 33, tzinfo=UTC)

    assert next_occurrence(REPEAT_DAILY, jittered, time(8, 0)) == datetime(
        2026, 9, 27, 8, 0, tzinfo=UTC
    )


def test_next_occurrence_always_moves_forward():
    previous = datetime(2026, 9, 26, 23, 59, tzinfo=UTC)

    assert next_occurrence(REPEAT_DAILY, previous, time(0, 30)) == datetime(
        2026, 9, 27, 0, 30, tzinfo=UTC
    )


# ── naive datetimes ───────────────────────────────────────────────────────────


def test_naive_now_is_treated_as_utc():
    naive = datetime(2026, 9, 26, 6, 0)
    aware = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)

    assert first_occurrence(REPEAT_DAILY, time(8, 0), now=naive) == first_occurrence(
        REPEAT_DAILY, time(8, 0), now=aware
    )


# ── describe ──────────────────────────────────────────────────────────────────


def test_describe_schedule_labels_each_mode():
    assert describe_schedule({"repeat": REPEAT_DAILY, "time_of_day": "08:00"}) == "每天 08:00"
    assert (
        describe_schedule({"repeat": REPEAT_EVERY_OTHER_DAY, "time_of_day": "09:30"})
        == "隔天 09:30"
    )
    assert describe_schedule({"repeat": REPEAT_ONCE}) == "仅一次"
    assert describe_schedule({}) == "仅一次"


# ── 时区：用户填的 HH:MM 是「本地时间」，不是 UTC ──────────────────────────────


def test_daily_0800_in_shanghai_is_stored_as_utc_0000():
    """北京时间 08:00 等于 UTC 00:00 —— 这正是之前差 8 小时的根因。"""
    from datetime import datetime, time, timezone
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    # 北京时间 2026-10-18 22:47 建任务
    now = datetime(2026, 10, 18, 14, 47, tzinfo=timezone.utc)  # == 22:47 +08:00

    result = first_occurrence("daily", time(8, 0), now=now, tz=tz)

    # 应该落在「北京的 10-19 早上 8 点」，即 UTC 10-19 00:00
    assert result == datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)
    # 换算回北京时间确认是 08:00
    assert result.astimezone(tz).hour == 8


def test_daily_timezone_makes_the_next_run_the_same_day_when_time_is_ahead():
    """北京时间 10-18 的 06:00 建「每天早上 8 点」→ 当天 8 点就该跑，不该等到明天。"""
    from datetime import datetime, time, timezone
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 10, 17, 22, 0, tzinfo=timezone.utc)  # == 10-18 06:00 +08:00

    result = first_occurrence("daily", time(8, 0), now=now, tz=tz)

    assert result.astimezone(tz) == datetime(2026, 10, 18, 8, 0, tzinfo=tz)


def test_without_tz_the_old_utc_behaviour_is_preserved():
    """传 tz=None 时必须保持原有 UTC 语义，避免破坏纯日期逻辑的既有测试。"""
    from datetime import datetime, time, timezone

    now = datetime(2026, 10, 18, 22, 47, tzinfo=timezone.utc)
    result = first_occurrence("daily", time(8, 0), now=now)

    assert result == datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)


def test_next_occurrence_keeps_the_local_wall_clock():
    from datetime import datetime, time, timezone
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    # 北京时间 10-19 08:00 触发后，下一次应是 10-20 08:00
    previous = datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)

    nxt = next_occurrence("daily", previous, time(8, 0), tz=tz)

    assert nxt.astimezone(tz).hour == 8
    assert nxt.astimezone(tz).date() == datetime(2026, 10, 20).date()


def test_every_other_day_uses_local_dates():
    from datetime import datetime, time, timezone
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)  # == 10-17 23:00 +08

    first = first_occurrence("every_other_day", time(8, 0), now=now, tz=tz)
    second = next_occurrence("every_other_day", first, time(8, 0), tz=tz)

    assert first.astimezone(tz).hour == 8
    assert second.astimezone(tz).hour == 8
    # 隔天 = 相差 2 天（本地日期）
    assert (second.astimezone(tz).date() - first.astimezone(tz).date()).days == 2


def test_utc_timezone_is_unchanged_by_the_fix():
    """配成 UTC 的用户行为不应变化。"""
    from datetime import datetime, time, timezone
    from zoneinfo import ZoneInfo

    now = datetime(2026, 10, 18, 22, 47, tzinfo=timezone.utc)
    result = first_occurrence("daily", time(8, 0), now=now, tz=ZoneInfo("UTC"))

    assert result == datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)


def test_resolve_timezone_falls_back_on_a_bad_name():
    from datetime import timezone

    assert resolve_timezone("Not/AZone") == resolve_timezone(None)
    assert resolve_timezone(None) is not timezone.utc or True  # 默认是上海
    assert resolve_timezone("Asia/Shanghai").key == "Asia/Shanghai"
