# 可提取组件现状

现有 `AppShell`、`MarketSummary` 与 `StockTable` 已在 React 源码中形成清晰边界，本轮不再提取成 Superdesign 独立组件。

详情页可新增：

- `KlineChart`：只接受类型化 K 线序列并负责 ECharts 生命周期。
- `PeriodTabs`：若周期按钮逻辑超过页面内的简单映射，再单独提取。
