# 页面依赖树规划

当前没有可追踪的前端源码，以下是已确认的目标依赖关系。

## `/`（全部股票）

入口：`frontend/src/pages/StockListPage.tsx`

计划依赖：

- `frontend/src/app/App.tsx`
  - `frontend/src/components/AppShell.tsx`
  - `frontend/src/pages/StockListPage.tsx`
    - `frontend/src/components/MarketSummary.tsx`
    - `frontend/src/components/StockTable.tsx`
  - `frontend/src/app/styles.css`

## `/stocks/:market/:code`（个股详情）

入口：`frontend/src/pages/StockDetailPage.tsx`

计划依赖：

- `frontend/src/app/App.tsx`
  - `frontend/src/components/AppShell.tsx`
  - `frontend/src/pages/StockDetailPage.tsx`
    - `frontend/src/components/KlineChart.tsx`
  - `frontend/src/app/styles.css`
