import { fireEvent, render, screen } from "@testing-library/react";
import * as echarts from "echarts";
import { createElement } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { todayBarsFixture } from "../test/fixtures";
import { buildKlineOption, KlineChart } from "./KlineChart";

vi.mock("echarts", () => ({ init: vi.fn() }));

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

  test("创建图表、响应容器尺寸、支持按钮缩放并在卸载时释放", async () => {
    const chart = {
      setOption: vi.fn(),
      dispatchAction: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
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

    const { unmount } = render(createElement(KlineChart, { series: todayBarsFixture }));
    expect(chart.setOption).toHaveBeenCalledWith(expect.any(Object), true);
    expect(observe).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "显示全部 K 线" }));
    expect(chart.dispatchAction).toHaveBeenCalledWith(
      expect.objectContaining({ type: "dataZoom", start: 0, end: 100 }),
    );
    resizeCallback?.();
    expect(chart.resize).toHaveBeenCalledTimes(1);

    unmount();
    expect(disconnect).toHaveBeenCalledTimes(1);
    expect(chart.dispose).toHaveBeenCalledTimes(1);
    globalThis.ResizeObserver = previousResizeObserver;
  });
});
