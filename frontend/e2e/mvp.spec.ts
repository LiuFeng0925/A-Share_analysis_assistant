import { expect, test } from "@playwright/test";

test("从全部股票搜索进入详情并查看今日一分钟 K", async ({ page }, testInfo) => {
  await page.clock.install();
  let todayRequestCount = 0;
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
  await expect(visibleRange).toHaveAttribute("data-window", "30");
  await page.getByRole("button", { name: "放大 K 线" }).click();
  await expect(visibleRange).toHaveAttribute("data-window", "10");
  await page.getByRole("button", { name: "缩小 K 线" }).click();
  await expect(visibleRange).toHaveAttribute("data-window", "30");

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
  await page.mouse.move(chartBox.x + chartBox.width * 0.45, chartBox.y + chartBox.height * 0.4);
  await page.mouse.down();
  await page.mouse.move(chartBox.x + chartBox.width * 0.62, chartBox.y + chartBox.height * 0.4, { steps: 8 });
  await page.mouse.up();
  await expect.poll(async () => visibleRange.getAttribute("data-start")).not.toBe(startBeforeDrag);

  await page.clock.fastForward(60_000);
  await expect(page.getByLabel("000001 1m K 线图，共 62 根")).toBeVisible();
  expect(todayRequestCount).toBeGreaterThanOrEqual(2);

  await page.screenshot({
    path: testInfo.outputPath("a-share-radar-mvp.png"),
    fullPage: true,
  });
});
