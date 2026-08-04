from datetime import date, datetime, time


class MarketClock:
    def __init__(self, trading_days: set[date]):
        self.trading_days = trading_days

    def is_open(self, at: datetime) -> bool:
        if at.date() not in self.trading_days:
            return False
        current = at.timetz().replace(tzinfo=None)
        return time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0)
