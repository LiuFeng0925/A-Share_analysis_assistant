from datetime import datetime, time
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
DAILY_FINAL_TIME = time(15, 20)


def _as_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("K 线时间必须包含时区")
    return value.astimezone(SHANGHAI)


def _is_valid_minute_end_label(period: str, bar_time: datetime) -> bool:
    duration_seconds = int(period.removesuffix("m")) * 60
    sessions = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))
    for session_start_time, session_end_time in sessions:
        session_start = datetime.combine(
            bar_time.date(), session_start_time, tzinfo=SHANGHAI
        )
        session_end = datetime.combine(
            bar_time.date(), session_end_time, tzinfo=SHANGHAI
        )
        if session_start < bar_time <= session_end:
            elapsed_seconds = int((bar_time - session_start).total_seconds())
            return elapsed_seconds % duration_seconds == 0
    return False


def bar_is_complete(period: str, bar_time: datetime, acquired_at: datetime) -> bool:
    """按东财周期结束标签及上海交易时段判断柱是否已经完成。"""

    localized_bar_time = _as_shanghai(bar_time)
    localized_acquired_at = _as_shanghai(acquired_at)
    if period.endswith("m"):
        return (
            _is_valid_minute_end_label(period, localized_bar_time)
            and localized_bar_time < localized_acquired_at
        )
    if period == "1d":
        return localized_bar_time.date() < localized_acquired_at.date() or (
            localized_bar_time.date() == localized_acquired_at.date()
            and localized_acquired_at.time() >= DAILY_FINAL_TIME
        )
    if period == "1w":
        return (
            localized_bar_time.isocalendar()[:2]
            < localized_acquired_at.isocalendar()[:2]
        )
    if period == "1mo":
        return (localized_bar_time.year, localized_bar_time.month) < (
            localized_acquired_at.year,
            localized_acquired_at.month,
        )
    return True
