import { expect, test } from "@playwright/test";

test("从全部股票搜索进入详情并查看今日一分钟 K", async ({ page }, testInfo) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "全市场行情" })).toBeVisible();
  const primaryNavigation = page.getByRole("navigation", { name: "主导航" });
  await expect(primaryNavigation.getByRole("link")).toHaveCount(1);
  await expect(primaryNavigation.getByRole("link", { name: "全部股票" })).toBeVisible();

  await page.getByPlaceholder("搜索股票代码或名称").fill("贵州茅台");
  const maotaiRow = page.getByRole("row", { name: /贵州茅台 600519/ });
  await expect(maotaiRow).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await maotaiRow.click();

  await expect(page.getByRole("heading", { name: "贵州茅台" })).toBeVisible();
  await expect(page.getByRole("button", { name: "日K" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByLabel(/600519 1d K 线图，共 \d+ 根/)).toBeVisible();

  await page.getByRole("button", { name: "今日" }).click();
  await expect(page.getByText("一分钟一根")).toBeVisible();
  await expect(page.getByLabel("600519 1m K 线图，共 61 根")).toBeVisible();
  const visibleRange = page.getByLabel("K 线当前可见区间");
  await expect(visibleRange).toHaveAttribute("data-window", "30");
  await page.getByRole("button", { name: "放大 K 线" }).click();
  await expect(visibleRange).toHaveAttribute("data-window", "10");
  await page.getByRole("button", { name: "缩小 K 线" }).click();
  await expect(visibleRange).toHaveAttribute("data-window", "30");

  await page.screenshot({
    path: testInfo.outputPath("a-share-radar-mvp.png"),
    fullPage: true,
  });
});
