import { useCallback, useEffect, useRef, useState } from "react";
import * as echarts from "echarts";
import type { ECharts, EChartsOption } from "echarts";
import type { BarSeries } from "../api/types";

const DEFAULT_ZOOM_START = 70;

export function buildKlineOption(series: BarSeries): EChartsOption {
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

  return {
    animation: false,
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    tooltip: { trigger: "axis" },
    grid: [
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
        axisLabel: { color: "#687386", hideOverlap: true },
      },
      {
        type: "category",
        data: categories,
        boundaryGap: false,
        gridIndex: 1,
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: "#dce2ec" } },
      },
    ],
    yAxis: [
      {
        scale: true,
        gridIndex: 0,
        splitLine: { lineStyle: { color: "#edf0f5" } },
        axisLabel: { color: "#687386" },
      },
      {
        scale: true,
        gridIndex: 1,
        splitLine: { show: false },
        axisLabel: { color: "#687386" },
      },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], start: DEFAULT_ZOOM_START, end: 100 },
      {
        type: "slider",
        xAxisIndex: [0, 1],
        bottom: 8,
        start: DEFAULT_ZOOM_START,
        end: 100,
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
    ],
  };
}

interface KlineChartProps {
  series: BarSeries;
}

export function KlineChart({ series }: KlineChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ECharts | null>(null);
  const [zoomStart, setZoomStart] = useState(DEFAULT_ZOOM_START);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = echarts.init(container);
    chartRef.current = chart;
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => chart.resize());
    resizeObserver?.observe(container);

    return () => {
      resizeObserver?.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    setZoomStart(DEFAULT_ZOOM_START);
    chartRef.current?.setOption(buildKlineOption(series), true);
  }, [series]);

  const updateZoom = useCallback((nextStart: number) => {
    const boundedStart = Math.min(95, Math.max(0, nextStart));
    setZoomStart(boundedStart);
    chartRef.current?.dispatchAction({
      type: "dataZoom",
      start: boundedStart,
      end: 100,
    });
  }, []);

  return (
    <div className="kline-chart-shell">
      <div className="chart-tools" aria-label="K 线缩放">
        <button type="button" aria-label="缩小 K 线" onClick={() => updateZoom(zoomStart - 10)}>−</button>
        <button type="button" aria-label="显示全部 K 线" onClick={() => updateZoom(0)}>100%</button>
        <button type="button" aria-label="放大 K 线" onClick={() => updateZoom(zoomStart + 10)}>＋</button>
      </div>
      <div
        ref={containerRef}
        className="kline-chart"
        role="img"
        aria-label={`${series.code} ${series.period} K 线图，共 ${series.items.length} 根`}
      />
    </div>
  );
}
