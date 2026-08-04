export function isFiniteNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function formatMarketNumber(
  value: number | null | undefined,
  digits = 2,
) {
  if (!isFiniteNumber(value)) return "—";
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatShanghaiDateTime(value: string | null | undefined) {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
}

export function formatShanghaiAxisLabel(
  value: string,
  period: string,
  range: string,
) {
  const formatted = formatShanghaiDateTime(value);
  if (formatted === "时间未知") return formatted;
  const [date, time] = formatted.split(" ");
  if (period.endsWith("m")) {
    const minute = time.slice(0, 5);
    return range === "today" ? minute : `${date.slice(5)} ${minute}`;
  }
  return period === "1mo" ? date.slice(0, 7) : date;
}
