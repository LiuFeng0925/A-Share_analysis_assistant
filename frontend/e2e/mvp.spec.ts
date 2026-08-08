import { expect, test } from "@playwright/test";

test("从全部股票搜索进入详情并查看今日一分钟 K", async ({ page }, testInfo) => {
  await page.clock.install();
  let todayRequestCount = 0;
  let thirtyMinuteRequestCount = 0;
  let kdjRequestCount = 0;
  let thirtyMinuteKdjRequestCount = 0;
  let thirtyMinuteMacdRequestCount = 0;
  await page.route("**/api/market/summary", async (route) => {
    const response = await route.fetch();
    const summary = await response.json();
    await route.fulfill({
      response,
      json: { ...summary, market_status: "open", stale: true },
    });
  });
  await page.route("**/api/stocks/*/*/bars?*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const url = new URL(route.request().url());
    if (url.searchParams.get("period") === "1m" && url.searchParams.get("range") === "today") {
      todayRequestCount += 1;
      if (todayRequestCount > 1 && body.items.length > 0) {
        const last = body.items.at(-1);
        const nextTime = new Date(new Date(last.bar_time).getTime() + 60_000).toISOString();
        body.items.push({
          ...last,
          bar_time: nextTime,
          acquired_at: nextTime,
          close_price: last.close_price + 0.12,
          high_price: Math.max(last.high_price, last.close_price + 0.12),
          is_complete: false,
          quality_status: "partial",
        });
        body.last_updated_at = nextTime;
      }
    }
    if (url.searchParams.get("period") === "30m") {
      thirtyMinuteRequestCount += 1;
      if (thirtyMinuteRequestCount > 1 && body.items.length > 0) {
        const last = body.items.at(-1);
        const nextTime = new Date(new Date(last.bar_time).getTime() + 30 * 60_000).toISOString();
        body.items.push({
          ...last,
          bar_time: nextTime,
          acquired_at: nextTime,
          close_price: last.close_price + 0.06,
          high_price: Math.max(last.high_price, last.close_price + 0.06),
          is_complete: false,
          quality_status: "partial",
        });
        body.last_updated_at = nextTime;
      }
    }
    await route.fulfill({ response, json: body });
  });
  await page.route("**/api/stocks/*/*/indicators/kdj?*", async (route) => {
    kdjRequestCount += 1;
    const response = await route.fetch();
    const body = await response.json();
    const url = new URL(route.request().url());
    if (url.searchParams.get("period") === "30m") {
      thirtyMinuteKdjRequestCount += 1;
      if (thirtyMinuteKdjRequestCount > 1 && body.items.length > 0) {
        const last = body.items.at(-1);
        const nextTime = new Date(new Date(last.bar_time).getTime() + 30 * 60_000).toISOString();
        const nextPoint = {
          ...last,
          bar_time: nextTime,
          k_value: last.k_value + 1.2,
          d_value: last.d_value + 0.4,
          j_value: last.j_value + 2.8,
          signal_type: "golden_cross",
          signal_zone: "middle",
          current_zone: "neutral",
          is_intraday: true,
          quality: "partial",
        };
        body.items.push(nextPoint);
        body.summary = {
          ...body.summary,
          market_time: nextTime,
          k_value: nextPoint.k_value,
          d_value: nextPoint.d_value,
          j_value: nextPoint.j_value,
          current_zone: nextPoint.current_zone,
          signal_type: nextPoint.signal_type,
          signal_time: nextTime,
          signal_zone: nextPoint.signal_zone,
          recent_signal_days: 0,
          recent_signal_label: "盘中金叉",
          status: "golden_after",
          is_intraday: true,
          quality: "partial",
        };
      }
    }
    await route.fulfill({ response, json: body });
  });
  await page.route("**/api/stocks/*/*/indicators/macd?*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const url = new URL(route.request().url());
    if (url.searchParams.get("period") === "30m") {
      thirtyMinuteMacdRequestCount += 1;
      if (thirtyMinuteMacdRequestCount > 1 && body.items.length > 0) {
        const last = body.items.at(-1);
        const nextTime = new Date(new Date(last.bar_time).getTime() + 30 * 60_000).toISOString();
        body.items.push({
          ...last,
          bar_time: nextTime,
          diff: last.diff + 0.02,
          dea: last.dea + 0.01,
          histogram: last.histogram + 0.02,
          signal_type: "golden_cross",
          is_intraday: true,
          quality: "partial",
        });
        body.summary = {
          ...body.summary,
          market_time: nextTime,
          signal_type: "golden_cross",
          signal_date: nextTime.slice(0, 10),
          recent_signal_days: 0,
          recent_signal_label: "30 分钟新柱金叉",
          status: "golden_after",
          is_intraday: true,
          quality: "partial",
        };
      }
    }
    await route.fulfill({ response, json: body });
  });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "全市场行情" })).toBeVisible();
  const primaryNavigation = page.getByRole("navigation", { name: "主导航" });
  await expect(primaryNavigation.getByRole("link")).toHaveCount(1);
  await expect(primaryNavigation.getByRole("link", { name: "全部股票" })).toBeVisible();
  await expect(page.getByText("数据可能已过期")).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "今日区间" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "更新时间" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "日 K KDJ" })).toBeVisible();
  await expect(page.getByText("日 K KDJ 雷达")).toBeVisible();

  await page.getByLabel("日 K KDJ 信号").selectOption("golden_cross");
  await page.getByLabel("KDJ 交叉区域").selectOption("low");
  await page.getByLabel("KDJ 出现时间").selectOption("today");
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.getByRole("row", { name: /贵州茅台 600519/ })).toContainText("盘中低位金叉");

  await page.getByPlaceholder("搜索股票代码或名称").fill("贵州茅台");
  const maotaiRow = page.getByRole("row", { name: /贵州茅台 600519/ });
  await expect(maotaiRow).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await maotaiRow.click();

  await expect(page.getByRole("heading", { name: "贵州茅台" })).toBeVisible();
  await expect(page.getByText("昨收")).toBeVisible();
  await expect(page.getByText("换手率")).toBeVisible();
  await expect(page.getByText("总市值")).toBeVisible();
  await expect(page.getByText("开市（交易中）")).toBeVisible();

  const switcher = page.getByRole("combobox", { name: "搜索其他股票" });
  await switcher.fill("平安");
  await expect(page.getByRole("option", { name: /平安银行 000001 SZ/ })).toBeVisible();
  await switcher.press("ArrowDown");
  await switcher.press("Enter");
  await expect(page.getByRole("heading", { name: "平安银行" })).toBeVisible();

  await expect(page.getByRole("button", { name: "日K" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByLabel(/000001 1d K 线图，共 60 根/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "KDJ 指标 · 日K" })).toBeVisible();
  await expect(page.getByLabel("KDJ 当前值")).toBeVisible();
  await expect(page.getByText(/KDJ 虚线为 20\/80 区域边界/)).toBeVisible();

  await page.getByRole("button", { name: "周K" }).click();
  await expect(page.getByLabel(/000001 1w K 线图，共 \d+ 根/)).toBeVisible();
  await page.getByRole("button", { name: "月K" }).click();
  await expect(page.getByLabel(/000001 1mo K 线图，共 \d+ 根/)).toBeVisible();
  await page.getByRole("button", { name: "日K" }).click();
  await expect(page.getByLabel(/000001 1d K 线图，共 60 根/)).toBeVisible();

  await page.getByRole("button", { name: "今日" }).click();
  await expect(page.getByText("一分钟一根")).toBeVisible();
  const chart = page.getByLabel("000001 1m K 线图，共 61 根");
  await expect(chart).toBeVisible();
  const visibleRange = page.getByLabel("K 线当前可见区间");
  const initialWindow = Number(await visibleRange.getAttribute("data-window"));
  expect(initialWindow).toBeGreaterThan(90);
  expect(initialWindow).toBeLessThanOrEqual(100);
  await page.getByRole("button", { name: "放大 K 线" }).click();
  await expect.poll(async () => Number(await visibleRange.getAttribute("data-window")))
    .toBeLessThan(initialWindow);
  const zoomedWindow = Number(await visibleRange.getAttribute("data-window"));
  await page.getByRole("button", { name: "缩小 K 线" }).click();
  await expect.poll(async () => Number(await visibleRange.getAttribute("data-window")))
    .toBeGreaterThan(zoomedWindow);

  const windowBeforeWheel = Number(await visibleRange.getAttribute("data-window"));
  await chart.hover();
  await page.mouse.wheel(0, -500);
  await expect.poll(async () => Number(await visibleRange.getAttribute("data-window")))
    .not.toBe(windowBeforeWheel);

  await page.getByRole("button", { name: "显示全部 K 线" }).click();
  await expect(visibleRange).toHaveAttribute("data-window", "100");
  await page.getByRole("button", { name: "放大 K 线" }).click();
  await expect(visibleRange).toHaveAttribute("data-window", "80");
  const chartBox = await chart.boundingBox();
  if (!chartBox) throw new Error("无法获取 K 线图位置");
  const startBeforeDrag = await visibleRange.getAttribute("data-start");
  await page.mouse.move(chartBox.x + chartBox.width * 0.45, chartBox.y + chartBox.height * 0.2);
  await page.mouse.down();
  await page.mouse.move(chartBox.x + chartBox.width * 0.62, chartBox.y + chartBox.height * 0.2, { steps: 8 });
  await page.mouse.up();
  await expect.poll(async () => visibleRange.getAttribute("data-start")).not.toBe(startBeforeDrag);

  const kdjRequestsBeforeRefresh = kdjRequestCount;
  await page.clock.fastForward(60_000);
  await expect(page.getByLabel("000001 1m K 线图，共 62 根")).toBeVisible();
  expect(todayRequestCount).toBeGreaterThanOrEqual(2);
  expect(kdjRequestCount).toBeGreaterThan(kdjRequestsBeforeRefresh);

  await page.getByRole("button", { name: "30分" }).click();
  const thirtyMinuteChart = page.getByLabel(/000001 30m K 线图，共 \d+ 根/);
  await expect(thirtyMinuteChart).toBeVisible();
  await expect(page.getByRole("heading", { name: "KDJ 指标 · 30分" })).toBeVisible();
  await expect(page.locator(".kdj-card").getByLabel("KDJ 当前值")).toBeVisible();
  const initialThirtyMinuteLabel = await thirtyMinuteChart.getAttribute("aria-label");
  const initialThirtyMinuteCount = Number(initialThirtyMinuteLabel?.match(/共 (\d+) 根/)?.[1]);
  const kdjRequestsBeforeThirtyMinuteRefresh = thirtyMinuteKdjRequestCount;
  const macdRequestsBeforeThirtyMinuteRefresh = thirtyMinuteMacdRequestCount;

  await page.clock.fastForward(60_000);

  await expect(page.getByLabel(
    `000001 30m K 线图，共 ${initialThirtyMinuteCount + 1} 根`,
  )).toBeVisible();
  await expect(page.getByText("30 分钟新柱金叉")).toBeVisible();
  await expect(page.locator(".kdj-card")).toContainText("盘中金叉");
  await expect(page.locator(".kdj-card")).toContainText("2026-08-04 11:00:00");
  expect(thirtyMinuteKdjRequestCount).toBeGreaterThan(kdjRequestsBeforeThirtyMinuteRefresh);
  expect(thirtyMinuteMacdRequestCount).toBeGreaterThan(macdRequestsBeforeThirtyMinuteRefresh);

  await page.screenshot({
    path: testInfo.outputPath("a-share-radar-mvp.png"),
    fullPage: true,
  });
});
