import { useCallback, useEffect, useRef, useState } from "react";
import * as echarts from "echarts";
import type { ECharts, EChartsOption } from "echarts";
import type { BarSeries, MacdIndicator } from "../api/types";
import {
  formatMarketNumber,
  formatShanghaiAxisLabel,
  formatShanghaiDateTime,
  isFiniteNumber,
} from "../utils/marketFormat";

const DEFAULT_ZOOM_START = 70;
const DEFAULT_ZOOM_END = 100;
const MACD_LEGEND_LABELS: Record<string, string> = {
  DIFF: "DIFF 蓝线",
  DEA: "DEA 黄线",
  "MACD 柱": "MACD 红绿柱",
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

function tooltipContent(series: BarSeries, dataIndex: number, macd?: MacdIndicator | null) {
  const bar = series.items[dataIndex];
  if (!bar) return "暂无 K 线数据";
  const macdPoint = macd?.items.find((item) => item.bar_time === bar.bar_time);
  return [
    `<strong>${formatShanghaiDateTime(bar.bar_time)}</strong>`,
    `开 ${formatMarketNumber(bar.open_price)}　高 ${formatMarketNumber(bar.high_price)}`,
    `低 ${formatMarketNumber(bar.low_price)}　收 ${formatMarketNumber(bar.close_price)}`,
    `成交量 ${formatMarketNumber(bar.volume, 0)} 股`,
    `成交额 ${formatMarketNumber(bar.amount)}`,
    macdPoint
      ? `DIFF ${formatMarketNumber(macdPoint.diff)}　DEA ${formatMarketNumber(macdPoint.dea)}`
      : null,
    bar.is_complete ? "已完成" : "动态柱",
  ].filter(Boolean).join("<br />");
}

export function buildKlineOption(
  series: BarSeries,
  zoom: ZoomWindow = { start: DEFAULT_ZOOM_START, end: DEFAULT_ZOOM_END },
  macd?: MacdIndicator | null,
): EChartsOption {
  const hasMacd = Boolean(macd?.items.length);
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
  const macdByTime = new Map(macd?.items.map((item) => [item.bar_time, item]) ?? []);
  const diff = categories.map((time) => macdByTime.get(time)?.diff ?? null);
  const dea = categories.map((time) => macdByTime.get(time)?.dea ?? null);
  const histogram = categories.map((time) => {
    const value = macdByTime.get(time)?.histogram ?? null;
    return {
      value,
      itemStyle: { color: value !== null && value >= 0 ? "#e5484d" : "#16a36f" },
    };
  });
  const axisLabelFormatter = (value: string) =>
    formatShanghaiAxisLabel(value, series.period, series.range);
  const linkedAxes = hasMacd ? [0, 1, 2] : [0, 1];

  return {
    animation: false,
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    title: hasMacd
      ? {
          text: "MACD（日线）",
          left: 56,
          top: "70.5%",
          textStyle: {
            color: "#172033",
            fontSize: 12,
            fontWeight: 700,
          },
        }
      : undefined,
    legend: hasMacd
      ? {
          data: ["DIFF", "DEA", "MACD 柱"],
          formatter: (name: string) => MACD_LEGEND_LABELS[name] ?? name,
          left: 148,
          top: "70.2%",
          itemWidth: 18,
          itemHeight: 8,
          textStyle: {
            color: "#687386",
            fontSize: 11,
          },
        }
      : undefined,
    tooltip: {
      trigger: "axis",
      formatter: (params: unknown) => {
        const items = Array.isArray(params) ? params : [params];
        const first = items[0] as { dataIndex?: number } | undefined;
        return tooltipContent(series, first?.dataIndex ?? -1, macd);
      },
    },
    grid: hasMacd
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
        axisLabel: { color: "#687386", hideOverlap: true, formatter: axisLabelFormatter },
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
              axisLabel: { color: "#687386", hideOverlap: true, formatter: axisLabelFormatter },
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
    ],
  };
}

interface KlineChartProps {
  series: BarSeries;
  macd?: MacdIndicator | null;
}

export function KlineChart({ series, macd = null }: KlineChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ECharts | null>(null);
  const zoomRef = useRef<ZoomWindow>({ start: DEFAULT_ZOOM_START, end: DEFAULT_ZOOM_END });
  const [zoomWindow, setZoomWindow] = useState<ZoomWindow>({
    start: DEFAULT_ZOOM_START,
    end: DEFAULT_ZOOM_END,
  });
  const identityRef = useRef("");

  const commitZoomWindow = useCallback((next: ZoomWindow) => {
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
        commitZoomWindow({ start: next.start, end: next.end });
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
    if (identityRef.current !== identity) {
      identityRef.current = identity;
      commitZoomWindow({ start: DEFAULT_ZOOM_START, end: DEFAULT_ZOOM_END });
    }
    chartRef.current?.setOption(buildKlineOption(series, zoomRef.current, macd), true);
  }, [commitZoomWindow, macd, series]);

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
        className={macd ? "kline-chart has-macd" : "kline-chart"}
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
