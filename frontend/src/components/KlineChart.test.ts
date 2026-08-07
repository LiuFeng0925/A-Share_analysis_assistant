import { act, fireEvent, render, screen } from "@testing-library/react";
import * as echarts from "echarts";
import { createElement } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { dailyBarsFixture, macdIndicatorFixture, todayBarsFixture } from "../test/fixtures";
import { buildKlineOption, KlineChart } from "./KlineChart";

vi.mock("echarts", () => ({ init: vi.fn() }));

function makeBars(count: number) {
  return {
    ...dailyBarsFixture,
    items: Array.from({ length: count }, (_, index) => ({
      ...dailyBarsFixture.items[0],
      bar_time: `2026-${String(Math.floor(index / 28) + 1).padStart(2, "0")}-${String((index % 28) + 1).padStart(2, "0")}T15:00:00+08:00`,
      close_price: dailyBarsFixture.items[0].close_price + index,
    })),
  };
}

describe("KlineChart", () => {
  afterEach(() => {
    vi.mocked(echarts.init).mockReset();
  });

  test("K 线按开收低高顺序传给 ECharts 并开启双缩放", () => {
    const option = buildKlineOption(todayBarsFixture);
    const candleSeries = (option.series as Array<{ data: unknown[] }>)[0];

    expect(candleSeries.data[0]).toEqual([1334.2, 1330.06, 1330.04, 1335.08]);
    expect(option.dataZoom).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ type: "inside" }),
        expect.objectContaining({ type: "slider" }),
      ]),
    );
    const yAxes = option.yAxis as Array<{ min?: number; scale?: boolean }>;
    expect(yAxes[0]).toEqual(expect.objectContaining({ scale: true }));
    expect(yAxes[1]).toEqual(expect.objectContaining({ min: 0 }));
  });

  test("传入 MACD 时在 K 线下方绘制 DIFF、DEA 与红绿柱", () => {
    const option = buildKlineOption(dailyBarsFixture, undefined, macdIndicatorFixture);
    const grids = option.grid as unknown[];
    const zoom = option.dataZoom as Array<{ xAxisIndex?: number[] }>;
    const series = option.series as Array<{ name: string; type: string; data: unknown[] }>;
    const title = option.title as { text: string; left: number; top: string };
    const legend = option.legend as {
      data: string[];
      formatter: (name: string) => string;
      left: number;
      top: string;
    };

    expect(grids).toHaveLength(3);
    expect(zoom[0].xAxisIndex).toEqual([0, 1, 2]);
    expect(title).toEqual(expect.objectContaining({ text: "MACD（日线）", left: 56 }));
    expect(legend).toEqual(expect.objectContaining({
      data: ["DIFF", "DEA", "MACD 柱"],
      left: 148,
    }));
    expect(legend.formatter("DIFF")).toBe("DIFF 蓝线");
    expect(legend.formatter("DEA")).toBe("DEA 黄线");
    expect(legend.formatter("MACD 柱")).toBe("MACD 红绿柱");
    expect(series.map((item) => item.name)).toEqual([
      "K 线",
      "成交量",
      "DIFF",
      "DEA",
      "MACD 柱",
    ]);
    expect(series[2]).toEqual(expect.objectContaining({ type: "line" }));
    expect(series[4]).toEqual(expect.objectContaining({ type: "bar" }));
  });

  test("日K背离同时标记价格节点和DIFF节点", () => {
    const dailyBarsWithBothPivots = {
      ...dailyBarsFixture,
      items: [
        ...dailyBarsFixture.items,
        {
          ...dailyBarsFixture.items[0],
          bar_time: "2026-08-04T15:00:00+08:00",
          close_price: 1330.06,
        },
      ],
    };
    const [bottomDivergence, topDivergence] = macdIndicatorFixture.divergences;
    const macdWithEquivalentTimezone = {
      ...macdIndicatorFixture,
      divergences: [
        { ...bottomDivergence, pivot_time: "2026-08-01T07:00:00Z" },
        { ...topDivergence, pivot_time: "2026-08-04T07:00:00Z" },
      ],
    };
    const option = buildKlineOption(
      dailyBarsWithBothPivots,
      undefined,
      macdWithEquivalentTimezone,
    );
    const series = option.series as Array<{
      name: string;
      markPoint?: {
        data: Array<{
          coord: [string, number];
          symbolSize: number;
          symbolRotate: number;
          itemStyle: { color: string };
        }>;
      };
    }>;

    const priceMarks = series.find((item) => item.name === "K 线")?.markPoint?.data;
    const diffMarks = series.find((item) => item.name === "DIFF")?.markPoint?.data;

    expect(priceMarks).toHaveLength(2);
    expect(diffMarks).toHaveLength(2);
    expect(priceMarks?.map((mark) => mark.coord)).toEqual([
      [dailyBarsWithBothPivots.items[0].bar_time, bottomDivergence.pivot_price],
      [dailyBarsWithBothPivots.items[1].bar_time, topDivergence.pivot_price],
    ]);
    expect(diffMarks?.map((mark) => mark.coord)).toEqual([
      [dailyBarsWithBothPivots.items[0].bar_time, bottomDivergence.pivot_diff],
      [dailyBarsWithBothPivots.items[1].bar_time, topDivergence.pivot_diff],
    ]);
    expect(priceMarks?.[0]).toEqual(expect.objectContaining({
      symbolSize: 12,
      symbolRotate: 0,
      itemStyle: { color: "rgba(229, 72, 77, 0.5)" },
    }));
    expect(priceMarks?.[1]).toEqual(expect.objectContaining({
      symbolSize: 15,
      symbolRotate: 180,
      itemStyle: { color: "#16a36f" },
    }));
  });

  test("提示框解释背离状态、锚点和对应交叉", () => {
    const option = buildKlineOption(dailyBarsFixture, undefined, {
      ...macdIndicatorFixture,
      divergences: [
        {
          ...macdIndicatorFixture.divergences[0],
          pivot_price: 1315.04,
          pivot_diff: -0.37,
        },
        macdIndicatorFixture.divergences[1],
      ],
    });
    const formatter = (option.tooltip as {
      formatter: (params: Array<{ dataIndex: number }>) => string;
    }).formatter;
    const content = formatter([{ dataIndex: 0 }]);

    expect(content).toContain("底背离形成中");
    expect(content).toContain("后续创新低或创新高时可能更新或失效");
    expect(content).toContain("锚点一");
    expect(content).toContain("2026-08-01 15:00:00");
    expect(content).toContain("价格 1,315.04　DIFF -0.02");
    expect(content).toContain("锚点二");
    expect(content).toContain("2026-08-04 15:00:00");
    expect(content).toContain("价格 1,330.06　DIFF 0.18");
    expect(content).toContain("节点价格 1,315.04　节点 DIFF -0.37");
    expect(content).not.toContain("当前价格 1,346.06");
    expect(content).not.toContain("当前 DIFF -0.02");
    expect(content).toContain("已出现金叉");
  });

  test("非日K不显示背离标记", () => {
    const option = buildKlineOption(todayBarsFixture, undefined, macdIndicatorFixture);
    const series = option.series as Array<{
      name: string;
      markPoint?: { data: unknown[] };
    }>;

    expect(series.find((item) => item.name === "K 线")?.markPoint).toBeUndefined();
    expect(series.find((item) => item.name === "DIFF")?.markPoint).toBeUndefined();
  });

  test("MACD 副图标题显示当前 K 线周期", () => {
    const option = buildKlineOption(
      { ...dailyBarsFixture, period: "5m" },
      undefined,
      { ...macdIndicatorFixture, period: "5m" },
    );
    const title = option.title as { text: string };

    expect(title.text).toBe("MACD（5分）");
  });

  test("默认显示最近 60 根 K 线，数据不足 60 根时显示全部", () => {
    const longOption = buildKlineOption(makeBars(120));
    const shortOption = buildKlineOption(makeBars(30));

    expect(longOption.dataZoom).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ start: 50, end: 100 }),
      ]),
    );
    expect(shortOption.dataZoom).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ start: 0, end: 100 }),
      ]),
    );
  });

  test("上涨与下跌使用 A 股红涨绿跌并同时保留数值含义", () => {
    const option = buildKlineOption(todayBarsFixture);
    const [candles, volumes] = option.series as Array<{
      itemStyle?: Record<string, string>;
      data: Array<{ value: number; itemStyle: { color: string } }>;
    }>;

    expect(candles.itemStyle).toEqual(
      expect.objectContaining({ color: "#e5484d", color0: "#16a36f" }),
    );
    expect(volumes.data[0]).toEqual(
      expect.objectContaining({ value: 82_100, itemStyle: { color: "#16a36f" } }),
    );
  });

  test("提示框显示上海时间、完整行情和动态柱状态", () => {
    const dynamicSeries = {
      ...todayBarsFixture,
      items: [{ ...todayBarsFixture.items[0], is_complete: false }],
    };
    const option = buildKlineOption(dynamicSeries);
    const formatter = (option.tooltip as { formatter: (params: Array<{ dataIndex: number }>) => string }).formatter;
    const content = formatter([{ dataIndex: 0 }]);

    expect(content).toContain("2026-08-04 10:31:00");
    expect(content).toContain("开 1,334.20");
    expect(content).toContain("高 1,335.08");
    expect(content).toContain("低 1,330.04");
    expect(content).toContain("收 1,330.06");
    expect(content).toContain("成交量 82,100");
    expect(content).toContain("成交额 109,400,000.00");
    expect(content).toContain("动态柱");
  });

  test("两个横轴把非东八区时间统一显示为上海时间标签", () => {
    const utcSeries = {
      ...todayBarsFixture,
      items: [{ ...todayBarsFixture.items[0], bar_time: "2026-08-04T02:31:00Z" }],
    };
    const option = buildKlineOption(utcSeries);
    const axes = option.xAxis as Array<{
      data: string[];
      axisLabel: { formatter: (value: string) => string };
    }>;

    expect(axes[0].data[0]).toBe("2026-08-04T02:31:00Z");
    expect(axes[0].axisLabel.formatter(axes[0].data[0])).toBe("10:31");
    expect(axes[1].axisLabel.formatter(axes[1].data[0])).toBe("10:31");
    expect(axes[0].axisLabel.formatter("非法时间")).toBe("时间未知");

    const dailyOption = buildKlineOption({
      ...dailyBarsFixture,
      items: [{ ...dailyBarsFixture.items[0], bar_time: "2026-08-03T16:30:00Z" }],
    });
    const dailyAxis = (dailyOption.xAxis as typeof axes)[0];
    expect(dailyAxis.axisLabel.formatter(dailyAxis.data[0])).toBe("2026-08-04");
  });

  test("创建图表、响应容器尺寸、支持按钮缩放并在卸载时释放", async () => {
    const chart = {
      setOption: vi.fn(),
      dispatchAction: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
      on: vi.fn(),
      off: vi.fn(),
    };
    let resizeCallback: (() => void) | undefined;
    const observe = vi.fn();
    const disconnect = vi.fn();
    const previousResizeObserver = globalThis.ResizeObserver;
    globalThis.ResizeObserver = class {
      constructor(callback: ResizeObserverCallback) {
        resizeCallback = () => callback([], this as unknown as ResizeObserver);
      }
      observe = observe;
      unobserve = vi.fn();
      disconnect = disconnect;
    } as unknown as typeof ResizeObserver;
    vi.mocked(echarts.init).mockReturnValue(chart as never);

    let dataZoomHandler: ((event: { start: number; end: number }) => void) | undefined;
    chart.on.mockImplementation((event, handler) => {
      if (event === "datazoom") dataZoomHandler = handler;
    });
    chart.dispatchAction.mockImplementation((action) => {
      dataZoomHandler?.({ start: action.start, end: action.end });
    });
    const { rerender, unmount } = render(createElement(KlineChart, { series: makeBars(120) }));
    expect(chart.setOption).toHaveBeenCalledWith(expect.any(Object), true);
    expect(observe).toHaveBeenCalledTimes(1);
    const visibleRange = screen.getByLabelText("K 线当前可见区间");
    expect(visibleRange).toHaveAttribute("data-start", "50");
    expect(visibleRange).toHaveAttribute("data-end", "100");
    expect(visibleRange).toHaveAttribute("data-window", "50");

    act(() => dataZoomHandler?.({ start: 20, end: 80 }));
    fireEvent.click(screen.getByRole("button", { name: "放大 K 线" }));
    expect(chart.dispatchAction).toHaveBeenLastCalledWith(
      expect.objectContaining({ type: "dataZoom", start: 30, end: 70 }),
    );
    expect(visibleRange).toHaveAttribute("data-start", "30");
    expect(visibleRange).toHaveAttribute("data-end", "70");
    expect(visibleRange).toHaveAttribute("data-window", "40");

    rerender(createElement(KlineChart, {
      series: makeBars(121),
    }));
    const refreshedOption = chart.setOption.mock.calls.at(-1)?.[0] as {
      dataZoom: Array<{ start: number; end: number }>;
    };
    expect(refreshedOption.dataZoom[0]).toEqual(expect.objectContaining({ start: 30, end: 70 }));

    fireEvent.click(screen.getByRole("button", { name: "显示全部 K 线" }));
    expect(chart.dispatchAction).toHaveBeenCalledWith(
      expect.objectContaining({ type: "dataZoom", start: 0, end: 100 }),
    );
    expect(visibleRange).toHaveAttribute("data-window", "100");
    resizeCallback?.();
    expect(chart.resize).toHaveBeenCalledTimes(1);

    unmount();
    expect(chart.off).toHaveBeenCalledWith("datazoom", dataZoomHandler);
    expect(disconnect).toHaveBeenCalledTimes(1);
    expect(chart.dispose).toHaveBeenCalledTimes(1);
    globalThis.ResizeObserver = previousResizeObserver;
  });

  test("按钮派发缩放后仅在 ECharts 回发事件时更新可见区间", () => {
    const chart = {
      setOption: vi.fn(),
      dispatchAction: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
      on: vi.fn(),
      off: vi.fn(),
    };
    vi.mocked(echarts.init).mockReturnValue(chart as never);
    render(createElement(KlineChart, { series: makeBars(120) }));
    const visibleRange = screen.getByLabelText("K 线当前可见区间");

    fireEvent.click(screen.getByRole("button", { name: "放大 K 线" }));

    expect(chart.dispatchAction).toHaveBeenCalledWith(
      expect.objectContaining({ type: "dataZoom", start: 60, end: 90 }),
    );
    expect(visibleRange).toHaveAttribute("data-start", "50");
    expect(visibleRange).toHaveAttribute("data-end", "100");
    expect(visibleRange).toHaveAttribute("data-window", "50");
  });

  test("为每根数据柱提供屏幕阅读器可读摘要", () => {
    const chart = {
      setOption: vi.fn(),
      dispatchAction: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
      on: vi.fn(),
      off: vi.fn(),
    };
    vi.mocked(echarts.init).mockReturnValue(chart as never);
    render(createElement(KlineChart, {
      series: {
        ...todayBarsFixture,
        items: [{ ...todayBarsFixture.items[0], is_complete: false }],
      },
    }));

    const table = screen.getByRole("table", { name: "K 线数据明细" });
    expect(table).toHaveTextContent("2026-08-04 10:31:00");
    expect(table).toHaveTextContent("1334.20");
    expect(table).toHaveTextContent("109400000.00");
    expect(table).toHaveTextContent("动态柱");
  });
});
