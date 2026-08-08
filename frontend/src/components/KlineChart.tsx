import { useCallback, useEffect, useRef, useState } from "react";
import * as echarts from "echarts";
import type { ECharts, EChartsOption } from "echarts";
import type { BarSeries, KdjIndicator, MacdDivergence, MacdIndicator } from "../api/types";
import {
  formatMarketNumber,
  formatShanghaiAxisLabel,
  formatShanghaiDateTime,
  isFiniteNumber,
} from "../utils/marketFormat";

const DEFAULT_ZOOM_END = 100;
const DEFAULT_VISIBLE_BAR_COUNT = 60;
const MACD_LEGEND_LABELS: Record<string, string> = {
  DIFF: "DIFF 蓝线",
  DEA: "DEA 黄线",
  "MACD 柱": "MACD 红绿柱",
};
const PERIOD_LABELS: Record<string, string> = {
  "1m": "1分",
  "5m": "5分",
  "15m": "15分",
  "30m": "30分",
  "60m": "60分",
  "1d": "日线",
  "1w": "周线",
  "1mo": "月线",
};

interface ZoomWindow {
  start: number;
  end: number;
}

interface DataZoomEvent {
  start?: number;
  end?: number;
  batch?: Array<{ start?: number; end?: number }>;
}

function seriesIdentity(series: BarSeries) {
  return `${series.market}/${series.code}/${series.period}/${series.range}/${series.adjustment}`;
}

function defaultZoomForSeries(series: BarSeries): ZoomWindow {
  const total = series.items.length;
  if (total <= DEFAULT_VISIBLE_BAR_COUNT) return { start: 0, end: DEFAULT_ZOOM_END };
  return {
    start: ((total - DEFAULT_VISIBLE_BAR_COUNT) / total) * 100,
    end: DEFAULT_ZOOM_END,
  };
}

function kdjZoneLabel(value: KdjIndicator["items"][number]["current_zone"]) {
  if (value === "oversold") return "超卖区";
  if (value === "overbought") return "超买区";
  if (value === "neutral") return "普通区";
  return "区域未知";
}

function kdjSignalLabel(value: KdjIndicator["items"][number]["signal_type"]) {
  if (value === "golden_cross") return "金叉";
  if (value === "death_cross") return "死叉";
  return "无交叉";
}

function kdjSignalZoneLabel(value: KdjIndicator["items"][number]["signal_zone"]) {
  if (value === "low") return "低位";
  if (value === "middle") return "中位";
  if (value === "high") return "高位";
  return "";
}

function sameBarTime(left: string, right: string) {
  const leftTime = Date.parse(left);
  const rightTime = Date.parse(right);
  if (!Number.isNaN(leftTime) && !Number.isNaN(rightTime)) return leftTime === rightTime;
  return left === right;
}

function chartTimeFor(eventTime: string, categories: string[]) {
  return categories.find((time) => sameBarTime(time, eventTime)) ?? eventTime;
}

function shortShanghaiDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(5, 10);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(date).replace("/", "-");
}

function macdEventTone(direction: "bottom" | "top") {
  return direction === "bottom"
    ? { color: "#e5484d", weakColor: "rgba(229, 72, 77, 0.6)", rotate: 0 }
    : { color: "#16a36f", weakColor: "rgba(22, 163, 111, 0.6)", rotate: 180 };
}

function namedMarkPoint({
  name,
  time,
  value,
  label,
  direction,
  weak = false,
  symbolSize = 7,
  showLabel = false,
}: {
  name: string;
  time: string;
  value: number | null | undefined;
  label: string;
  direction: "bottom" | "top";
  weak?: boolean;
  symbolSize?: number;
  showLabel?: boolean;
}) {
  if (!isFiniteNumber(value)) return null;
  const tone = macdEventTone(direction);
  return {
    name,
    coord: [time, value] as [string, number],
    value: label,
    symbol: "circle",
    symbolSize,
    itemStyle: {
      color: weak ? tone.weakColor : tone.color,
      borderColor: "#fff",
      borderWidth: 1,
    },
    label: {
      show: showLabel,
      formatter: "{c}",
      position: direction === "bottom" ? "bottom" as const : "top" as const,
      color: tone.color,
      fontSize: 10,
      fontWeight: 800,
      backgroundColor: "rgba(255, 255, 255, 0.88)",
      borderRadius: 3,
      padding: [2, 4] as [number, number],
    },
    tooltip: {
      formatter: `${name}<br />${formatShanghaiDateTime(time)}<br />数值 ${formatMarketNumber(value)}`,
    },
  };
}

interface VerticalLineDraft {
  name: string;
  time: string;
  label: string;
  direction: "bottom" | "top";
  weak?: boolean;
  showLabel?: boolean;
}

function namedVerticalLine({
  name,
  time,
  label,
  direction,
  weak = false,
  showLabel = true,
}: VerticalLineDraft) {
  const tone = macdEventTone(direction);
  return {
    name,
    xAxis: time,
    label: {
      show: showLabel,
      formatter: `${label}\n${shortShanghaiDate(time)}`,
      position: "end" as const,
      color: tone.color,
      fontSize: 10,
      fontWeight: 800,
      backgroundColor: direction === "bottom"
        ? "rgba(229, 72, 77, 0.08)"
        : "rgba(22, 163, 111, 0.08)",
      borderRadius: 4,
      padding: [2, 4],
    },
    lineStyle: {
      color: weak ? tone.weakColor : tone.color,
      type: "dashed" as const,
      width: 1,
      opacity: weak ? 0.28 : 0.45,
    },
    tooltip: {
      formatter: `${name}<br />${formatShanghaiDateTime(time)}`,
    },
  };
}

function mergeVerticalLines(lines: VerticalLineDraft[]) {
  const byTime = new Map<string, VerticalLineDraft & { labels: string[]; names: string[] }>();
  lines.forEach((line) => {
    const current = byTime.get(line.time);
    if (!current) {
      byTime.set(line.time, {
        ...line,
        labels: [line.label],
        names: [line.name],
      });
      return;
    }
    if (!current.labels.includes(line.label)) current.labels.push(line.label);
    current.names.push(line.name);
    current.weak = current.weak && line.weak;
    current.showLabel = (current.showLabel ?? true) && (line.showLabel ?? true);
  });
  return Array.from(byTime.values()).map((line) => namedVerticalLine({
    name: line.names.join(" / "),
    time: line.time,
    label: line.labels.join("/"),
    direction: line.direction,
    weak: line.weak,
    showLabel: line.showLabel,
  }));
}

function divergenceSignalLabel(event: MacdDivergence) {
  if (event.corresponding_signal === "golden_cross") return "已出现金叉";
  if (event.corresponding_signal === "death_cross") return "已出现死叉";
  return "尚未出现对应交叉";
}

function divergenceTooltipContent(event: MacdDivergence) {
  const direction = event.direction === "bottom" ? "底背离" : "顶背离";
  const status = event.status === "forming" ? "形成中" : "已确认";
  const anchorLabel = event.direction === "bottom"
    ? { one: "前低1", two: "前低2", pivot: "背离低点" }
    : { one: "前高1", two: "前高2", pivot: "背离高点" };
  return [
    `${direction}${status}`,
    `${anchorLabel.one} ${formatShanghaiDateTime(event.anchor_one_time)}　价格 ${formatMarketNumber(event.anchor_one_price)}　DIFF ${formatMarketNumber(event.anchor_one_diff)}`,
    `${anchorLabel.two} ${formatShanghaiDateTime(event.anchor_two_time)}　价格 ${formatMarketNumber(event.anchor_two_price)}　DIFF ${formatMarketNumber(event.anchor_two_diff)}`,
    `${anchorLabel.pivot}价格 ${formatMarketNumber(event.pivot_price)}　DIFF ${formatMarketNumber(event.pivot_diff)}`,
    event.confirmed_at ? `确认时间 ${formatShanghaiDateTime(event.confirmed_at)}` : null,
    `对应交叉：${divergenceSignalLabel(event)}`,
    event.corresponding_signal_time ? `交叉时间 ${formatShanghaiDateTime(event.corresponding_signal_time)}` : null,
    event.status === "forming" ? "该背离仍在形成，后续创新低或创新高时可能更新或失效" : null,
  ];
}

function relatedDivergenceTime(event: MacdDivergence, barTime: string) {
  return [
    event.anchor_one_time,
    event.anchor_two_time,
    event.pivot_time,
    event.confirmed_at,
    event.corresponding_signal_time,
  ].some((time) => Boolean(time && sameBarTime(time, barTime)));
}

function tooltipContent(
  series: BarSeries,
  dataIndex: number,
  macd?: MacdIndicator | null,
  kdj?: KdjIndicator | null,
) {
  const bar = series.items[dataIndex];
  if (!bar) return "暂无 K 线数据";
  const macdPoint = macd?.items.find((item) => sameBarTime(item.bar_time, bar.bar_time));
  const kdjPoint = kdj?.items.find((item) => sameBarTime(item.bar_time, bar.bar_time));
  const divergences = series.period === "1d"
    ? macd?.divergences.filter((event) => relatedDivergenceTime(event, bar.bar_time)) ?? []
    : [];
  return [
    `<strong>${formatShanghaiDateTime(bar.bar_time)}</strong>`,
    `开 ${formatMarketNumber(bar.open_price)}　高 ${formatMarketNumber(bar.high_price)}`,
    `低 ${formatMarketNumber(bar.low_price)}　收 ${formatMarketNumber(bar.close_price)}`,
    `成交量 ${formatMarketNumber(bar.volume, 0)} 股`,
    `成交额 ${formatMarketNumber(bar.amount)}`,
    macdPoint
      ? `DIFF ${formatMarketNumber(macdPoint.diff)}　DEA ${formatMarketNumber(macdPoint.dea)}`
      : null,
    kdjPoint
      ? `K ${formatMarketNumber(kdjPoint.k_value)}　D ${formatMarketNumber(kdjPoint.d_value)}　J ${formatMarketNumber(kdjPoint.j_value)}`
      : null,
    kdjPoint
      ? `${kdjZoneLabel(kdjPoint.current_zone)}　${kdjSignalZoneLabel(kdjPoint.signal_zone)}${kdjSignalLabel(kdjPoint.signal_type)}　${kdjPoint.is_intraday ? "盘中动态" : "已确认"}`
      : null,
    ...divergences.flatMap((event) => divergenceTooltipContent(event)),
    bar.is_complete ? "已完成" : "动态柱",
  ].filter(Boolean).join("<br />");
}

function buildDivergencePriceMarks(events: MacdDivergence[], categories: string[]) {
  return events.flatMap((event) => {
    const directionName = event.direction === "bottom" ? "底背离" : "顶背离";
    const labels = event.direction === "bottom"
      ? { previous: "前低", fresh: "新低" }
      : { previous: "前高", fresh: "新高" };

    return [
      namedMarkPoint({
        name: `${directionName}${labels.previous}`,
        time: chartTimeFor(event.anchor_one_time, categories),
        value: event.anchor_one_price,
        label: labels.previous,
        direction: event.direction,
        weak: true,
        symbolSize: 6,
        showLabel: true,
      }),
      namedMarkPoint({
        name: `${directionName}${labels.fresh}`,
        time: chartTimeFor(event.anchor_two_time, categories),
        value: event.anchor_two_price,
        label: labels.fresh,
        direction: event.direction,
        symbolSize: 7,
        showLabel: true,
      }),
    ].filter((mark): mark is NonNullable<typeof mark> => mark !== null);
  });
}

function isPlainPivot(
  series: BarSeries,
  index: number,
  direction: "bottom" | "top",
  sideBars = 3,
) {
  const item = series.items[index];
  if (!item || index < sideBars || index + sideBars >= series.items.length) return false;
  const price = direction === "bottom" ? item.low_price : item.high_price;
  return series.items.every((bar, barIndex) => {
    if (barIndex < index - sideBars || barIndex > index + sideBars || barIndex === index) {
      return true;
    }
    const nearbyPrice = direction === "bottom" ? bar.low_price : bar.high_price;
    return direction === "bottom" ? price < nearbyPrice : price > nearbyPrice;
  });
}

function buildPlainPivotMarks(series: BarSeries) {
  if (series.period !== "1d") return [];
  return series.items.flatMap((bar, index) => [
    isPlainPivot(series, index, "bottom")
      ? namedMarkPoint({
          name: "普通低点",
          time: bar.bar_time,
          value: bar.low_price,
          label: "低",
          direction: "bottom",
          weak: true,
          symbolSize: 4,
        })
      : null,
    isPlainPivot(series, index, "top")
      ? namedMarkPoint({
          name: "普通高点",
          time: bar.bar_time,
          value: bar.high_price,
          label: "高",
          direction: "top",
          weak: true,
          symbolSize: 4,
        })
      : null,
  ].filter((mark): mark is NonNullable<typeof mark> => mark !== null));
}

function buildMacdCrossLines(macd: MacdIndicator | null | undefined, categories: string[]): VerticalLineDraft[] {
  return buildIndicatorCrossLines("MACD", macd?.items ?? [], categories);
}

function buildIndicatorCrossLines(
  indicatorName: "MACD" | "KDJ",
  items: Array<{ bar_time: string; signal_type: "none" | "golden_cross" | "death_cross" }>,
  categories: string[],
): VerticalLineDraft[] {
  return items.flatMap((item) => {
    if (item.signal_type !== "golden_cross" && item.signal_type !== "death_cross") return [];
    const direction = item.signal_type === "golden_cross" ? "bottom" : "top";
    return {
      name: `${indicatorName} ${item.signal_type === "golden_cross" ? "金叉" : "死叉"}`,
      time: chartTimeFor(item.bar_time, categories),
      label: item.signal_type === "golden_cross" ? "金叉" : "死叉",
      direction,
    };
  });
}

export function buildKlineOption(
  series: BarSeries,
  zoom: ZoomWindow = defaultZoomForSeries(series),
  macd?: MacdIndicator | null,
  kdj?: KdjIndicator | null,
): EChartsOption {
  const activeMacd = macd?.period === series.period ? macd : null;
  const activeKdj = kdj?.period === series.period ? kdj : null;
  const hasMacd = Boolean(activeMacd?.items.length);
  const hasKdj = Boolean(activeKdj?.items.length);
  const hasIndicators = hasMacd || hasKdj;
  const categories = series.items.map((bar) => bar.bar_time);
  const candles = series.items.map((bar) => [
    bar.open_price,
    bar.close_price,
    bar.low_price,
    bar.high_price,
  ]);
  const volumes = series.items.map((bar) => ({
    value: bar.volume,
    itemStyle: { color: bar.close_price >= bar.open_price ? "#e5484d" : "#16a36f" },
  }));
  const macdByTime = new Map(activeMacd?.items.map((item) => [item.bar_time, item]) ?? []);
  const diff = categories.map((time) => macdByTime.get(time)?.diff ?? null);
  const dea = categories.map((time) => macdByTime.get(time)?.dea ?? null);
  const histogram = categories.map((time) => {
    const value = macdByTime.get(time)?.histogram ?? null;
    return {
      value,
      itemStyle: { color: value !== null && value >= 0 ? "#e5484d" : "#16a36f" },
    };
  });
  const kdjByTime = new Map(activeKdj?.items.map((item) => [item.bar_time, item]) ?? []);
  const kValues = categories.map((time) => kdjByTime.get(time)?.k_value ?? null);
  const dValues = categories.map((time) => kdjByTime.get(time)?.d_value ?? null);
  const jValues = categories.map((time) => kdjByTime.get(time)?.j_value ?? null);
  const kdjAxisIndex = hasMacd ? 3 : 2;
  const divergences = series.period === "1d" ? activeMacd?.divergences ?? [] : [];
  const kdjCrossLines = mergeVerticalLines(buildIndicatorCrossLines(
    "KDJ",
    activeKdj?.items ?? [],
    categories,
  ));
  const pricePivotMarks = [
    ...buildPlainPivotMarks(series),
    ...buildDivergencePriceMarks(divergences, categories),
  ];
  const diffDivergenceLines = mergeVerticalLines(buildMacdCrossLines(activeMacd, categories));
  const axisLabelFormatter = (value: string) =>
    formatShanghaiAxisLabel(value, series.period, series.range);
  const linkedAxes = Array.from({ length: 2 + Number(hasMacd) + Number(hasKdj) }, (_, i) => i);

  const titles = [
    ...(hasMacd
      ? [{
          text: `MACD（${PERIOD_LABELS[series.period] ?? series.period}）`,
          left: 56,
          top: hasKdj ? "55.5%" : "70.5%",
          textStyle: { color: "#172033", fontSize: 12, fontWeight: 700 },
        }]
      : []),
    ...(hasKdj
      ? [{
          text: `KDJ（${PERIOD_LABELS[series.period] ?? series.period}）`,
          left: 56,
          top: "72%",
          textStyle: { color: "#172033", fontSize: 12, fontWeight: 700 },
        }]
      : []),
  ];
  const legends = [
    ...(hasMacd
      ? [{
          data: ["DIFF", "DEA", "MACD 柱"],
          formatter: (name: string) => MACD_LEGEND_LABELS[name] ?? name,
          left: 148,
          top: hasKdj ? "55.2%" : "70.2%",
          itemWidth: 18,
          itemHeight: 8,
          textStyle: { color: "#687386", fontSize: 11 },
        }]
      : []),
    ...(hasKdj
      ? [{
          data: ["K", "D", "J"],
          left: 138,
          top: "71.7%",
          itemWidth: 18,
          itemHeight: 8,
          textStyle: { color: "#687386", fontSize: 11 },
        }]
      : []),
  ];

  return {
    animation: false,
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    title: titles.length === 1 ? titles[0] : titles.length > 1 ? titles : undefined,
    legend: legends.length === 1 ? legends[0] : legends.length > 1 ? legends : undefined,
    tooltip: {
      trigger: "axis",
      formatter: (params: unknown) => {
        const items = Array.isArray(params) ? params : [params];
        const first = items[0] as { dataIndex?: number } | undefined;
        return tooltipContent(series, first?.dataIndex ?? -1, activeMacd, activeKdj);
      },
    },
    grid: hasMacd && hasKdj
      ? [
          { left: 56, right: 78, top: 24, height: "35%" },
          { left: 56, right: 78, top: "42%", height: "8%" },
          { left: 56, right: 78, top: "59%", height: "9%" },
          { left: 56, right: 78, top: "75.5%", height: "13%" },
        ]
      : hasIndicators
      ? [
          { left: 56, right: 64, top: 24, height: "48%" },
          { left: 56, right: 64, top: "58%", height: "11%" },
          { left: 56, right: 64, top: "75%", height: "13%" },
        ]
      : [
          { left: 56, right: 64, top: 24, height: "62%" },
          { left: 56, right: 64, top: "74%", height: "14%" },
        ],
    xAxis: [
      {
        type: "category",
        data: categories,
        boundaryGap: false,
        gridIndex: 0,
        axisLine: { lineStyle: { color: "#dce2ec" } },
        axisLabel: {
          show: !hasIndicators,
          color: "#687386",
          hideOverlap: true,
          formatter: axisLabelFormatter,
        },
      },
      {
        type: "category",
        data: categories,
        boundaryGap: false,
        gridIndex: 1,
        axisLabel: { show: false, formatter: axisLabelFormatter },
        axisLine: { lineStyle: { color: "#dce2ec" } },
      },
      ...(hasMacd
        ? [
            {
              type: "category" as const,
              data: categories,
              boundaryGap: false,
              gridIndex: 2,
              axisLabel: {
                show: !hasKdj,
                color: "#687386",
                hideOverlap: true,
                formatter: axisLabelFormatter,
              },
              axisLine: { lineStyle: { color: "#dce2ec" } },
            },
          ]
        : []),
      ...(hasKdj
        ? [
            {
              type: "category" as const,
              data: categories,
              boundaryGap: false,
              gridIndex: kdjAxisIndex,
              axisLabel: {
                color: "#687386",
                hideOverlap: true,
                formatter: axisLabelFormatter,
              },
              axisLine: { lineStyle: { color: "#dce2ec" } },
            },
          ]
        : []),
    ],
    yAxis: [
      {
        scale: true,
        gridIndex: 0,
        splitLine: { lineStyle: { color: "#edf0f5" } },
        axisLabel: { color: "#687386" },
      },
      {
        min: 0,
        gridIndex: 1,
        splitLine: { show: false },
        axisLabel: { color: "#687386" },
      },
      ...(hasMacd
        ? [
            {
              scale: true,
              gridIndex: 2,
              splitLine: { lineStyle: { color: "#edf0f5" } },
              axisLabel: { color: "#687386" },
            },
          ]
        : []),
      ...(hasKdj
        ? [
            {
              scale: true,
              gridIndex: kdjAxisIndex,
              position: "left" as const,
              min: (extent: { min: number }) => Math.min(0, Math.floor(extent.min - 5)),
              max: (extent: { max: number }) => Math.max(100, Math.ceil(extent.max + 5)),
              splitLine: { show: false },
              axisLabel: { color: "#687386" },
            },
          ]
        : []),
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: linkedAxes, start: zoom.start, end: zoom.end },
      {
        type: "slider",
        xAxisIndex: linkedAxes,
        bottom: 8,
        start: zoom.start,
        end: zoom.end,
        borderColor: "#e4e9f2",
        fillerColor: "rgba(49, 87, 213, 0.12)",
        handleStyle: { color: "#3157d5" },
      },
    ],
    series: [
      {
        name: "K 线",
        type: "candlestick",
        data: candles,
        itemStyle: {
          color: "#e5484d",
          color0: "#16a36f",
          borderColor: "#e5484d",
          borderColor0: "#16a36f",
        },
        ...(pricePivotMarks.length
          ? { markPoint: { data: pricePivotMarks } }
          : {}),
      },
      {
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumes,
      },
      ...(hasMacd
        ? [
            {
              name: "DIFF",
              type: "line" as const,
              xAxisIndex: 2,
              yAxisIndex: 2,
              showSymbol: false,
              smooth: true,
              lineStyle: { width: 1.4, color: "#3157d5" },
              data: diff,
              ...(diffDivergenceLines.length
                ? { markLine: { symbol: "none" as const, data: diffDivergenceLines } }
                : {}),
            },
            {
              name: "DEA",
              type: "line" as const,
              xAxisIndex: 2,
              yAxisIndex: 2,
              showSymbol: false,
              smooth: true,
              lineStyle: { width: 1.4, color: "#f59f00" },
              data: dea,
            },
            {
              name: "MACD 柱",
              type: "bar" as const,
              xAxisIndex: 2,
              yAxisIndex: 2,
              data: histogram,
            },
          ]
        : []),
      ...(hasKdj
        ? [
            {
              name: "K",
              type: "line" as const,
              xAxisIndex: kdjAxisIndex,
              yAxisIndex: kdjAxisIndex,
              showSymbol: false,
              smooth: true,
              lineStyle: { width: 1.5, color: "#3157d5" },
              data: kValues,
              markLine: {
                silent: true,
                symbol: "none",
                label: {
                  show: false,
                },
                data: [
                  {
                    name: "20 超卖线",
                    yAxis: 20,
                    lineStyle: { type: "dashed" as const, width: 1.6, color: "#16a36f" },
                  },
                  {
                    name: "80 超买线",
                    yAxis: 80,
                    lineStyle: { type: "dashed" as const, width: 1.6, color: "#e5484d" },
                  },
                  ...kdjCrossLines,
                ],
              },
              markArea: {
                silent: true,
                label: { show: false },
                data: [
                  [
                    { name: "超卖区", yAxis: 0, itemStyle: { color: "rgba(22, 163, 111, 0.08)" } },
                    { yAxis: 20 },
                  ] as [
                    { name: string; yAxis: number; itemStyle: { color: string } },
                    { yAxis: number },
                  ],
                  [
                    { name: "普通区", yAxis: 20, itemStyle: { color: "rgba(49, 87, 213, 0.018)" } },
                    { yAxis: 80 },
                  ] as [
                    { name: string; yAxis: number; itemStyle: { color: string } },
                    { yAxis: number },
                  ],
                  [
                    { name: "超买区", yAxis: 80, itemStyle: { color: "rgba(229, 72, 77, 0.08)" } },
                    { yAxis: 100 },
                  ] as [
                    { name: string; yAxis: number; itemStyle: { color: string } },
                    { yAxis: number },
                  ],
                ],
              },
            },
            {
              name: "D",
              type: "line" as const,
              xAxisIndex: kdjAxisIndex,
              yAxisIndex: kdjAxisIndex,
              showSymbol: false,
              smooth: true,
              lineStyle: { width: 1.5, color: "#f59f00" },
              data: dValues,
            },
            {
              name: "J",
              type: "line" as const,
              xAxisIndex: kdjAxisIndex,
              yAxisIndex: kdjAxisIndex,
              showSymbol: false,
              smooth: true,
              lineStyle: { width: 1.25, color: "#8b5cf6" },
              data: jValues,
            },
          ]
        : []),
    ],
  };
}

interface KlineChartProps {
  series: BarSeries;
  macd?: MacdIndicator | null;
  kdj?: KdjIndicator | null;
}

export function KlineChart({ series, macd = null, kdj = null }: KlineChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ECharts | null>(null);
  const initialZoom = defaultZoomForSeries(series);
  const zoomRef = useRef<ZoomWindow>(initialZoom);
  const [zoomWindow, setZoomWindow] = useState<ZoomWindow>(initialZoom);
  const manualZoomRef = useRef(false);
  const identityRef = useRef("");

  const commitZoomWindow = useCallback((next: ZoomWindow, manual = false) => {
    if (manual) manualZoomRef.current = true;
    zoomRef.current = next;
    setZoomWindow(next);
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = echarts.init(container);
    chartRef.current = chart;
    const handleDataZoom = (rawEvent: unknown) => {
      const event = rawEvent as DataZoomEvent;
      const next = event.batch?.[0] ?? event;
      if (isFiniteNumber(next.start) && isFiniteNumber(next.end)) {
        commitZoomWindow({ start: next.start, end: next.end }, true);
      }
    };
    chart.on("datazoom", handleDataZoom);
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => chart.resize());
    resizeObserver?.observe(container);

    return () => {
      resizeObserver?.disconnect();
      chart.off("datazoom", handleDataZoom);
      chart.dispose();
      chartRef.current = null;
    };
  }, [commitZoomWindow]);

  useEffect(() => {
    const identity = seriesIdentity(series);
    const defaultZoom = defaultZoomForSeries(series);
    if (identityRef.current !== identity) {
      identityRef.current = identity;
      manualZoomRef.current = false;
      commitZoomWindow(defaultZoom);
    } else if (!manualZoomRef.current) {
      commitZoomWindow(defaultZoom);
    }
    chartRef.current?.setOption(buildKlineOption(series, zoomRef.current, macd, kdj), true);
  }, [commitZoomWindow, kdj, macd, series]);

  const updateZoom = useCallback((nextStart: number, nextEnd: number) => {
    const boundedStart = Math.min(95, Math.max(0, nextStart));
    const boundedEnd = Math.min(100, Math.max(boundedStart + 5, nextEnd));
    chartRef.current?.dispatchAction({
      type: "dataZoom",
      start: boundedStart,
      end: boundedEnd,
    });
  }, []);

  const zoomOut = () => {
    const { start, end } = zoomRef.current;
    updateZoom(start - 10, end + 10);
  };

  const zoomIn = () => {
    const { start, end } = zoomRef.current;
    if (end - start <= 20) return;
    updateZoom(start + 10, end - 10);
  };

  return (
    <div className="kline-chart-shell">
      <div className="chart-tools" aria-label="K 线缩放">
        <button type="button" aria-label="缩小 K 线" onClick={zoomOut}>−</button>
        <button type="button" aria-label="显示全部 K 线" onClick={() => updateZoom(0, 100)}>100%</button>
        <button type="button" aria-label="放大 K 线" onClick={zoomIn}>＋</button>
        <output
          className="sr-only"
          aria-label="K 线当前可见区间"
          data-start={zoomWindow.start}
          data-end={zoomWindow.end}
          data-window={zoomWindow.end - zoomWindow.start}
        >
          当前显示 {zoomWindow.start}% 至 {zoomWindow.end}%，窗口宽度 {zoomWindow.end - zoomWindow.start}%
        </output>
      </div>
      <div
        ref={containerRef}
        className={kdj ? "kline-chart has-kdj" : macd ? "kline-chart has-macd" : "kline-chart"}
        role="img"
        aria-label={`${series.code} ${series.period} K 线图，共 ${series.items.length} 根`}
      />
      <table className="sr-only" aria-label="K 线数据明细">
        <thead>
          <tr>
            <th>时间</th><th>开</th><th>高</th><th>低</th><th>收</th><th>成交量（股）</th><th>成交额</th><th>状态</th>
          </tr>
        </thead>
        <tbody>
          {series.items.map((bar) => (
            <tr key={bar.bar_time}>
              <td>{formatShanghaiDateTime(bar.bar_time)}</td>
              <td>{isFiniteNumber(bar.open_price) ? bar.open_price.toFixed(2) : "—"}</td>
              <td>{isFiniteNumber(bar.high_price) ? bar.high_price.toFixed(2) : "—"}</td>
              <td>{isFiniteNumber(bar.low_price) ? bar.low_price.toFixed(2) : "—"}</td>
              <td>{isFiniteNumber(bar.close_price) ? bar.close_price.toFixed(2) : "—"}</td>
              <td>{isFiniteNumber(bar.volume) ? bar.volume.toFixed(0) : "—"}</td>
              <td>{isFiniteNumber(bar.amount) ? bar.amount.toFixed(2) : "—"}</td>
              <td>{bar.is_complete ? "已完成" : "动态柱"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
