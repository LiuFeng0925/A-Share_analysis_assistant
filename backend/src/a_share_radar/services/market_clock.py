from datetime import date, datetime, time
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


class MarketClock:
    def __init__(self, trading_days: set[date]):
        self.trading_days = trading_days

    def replace_trading_days(self, trading_days: set[date]) -> None:
        self.trading_days = set(trading_days)

    def is_open(self, at: datetime) -> bool:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("市场时间必须包含时区信息")
        shanghai_at = at.astimezone(SHANGHAI)
        if shanghai_at.date() not in self.trading_days:
            return False
        current = time(shanghai_at.hour, shanghai_at.minute)
        return time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0)
