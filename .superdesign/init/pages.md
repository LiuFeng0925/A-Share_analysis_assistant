# 页面依赖树

## `/`（全部股票）

入口：`frontend/src/pages/StockListPage.tsx`

当前依赖：

- `frontend/src/app/App.tsx`
  - `frontend/src/components/AppShell.tsx`
  - `frontend/src/pages/StockListPage.tsx`
    - `frontend/src/components/MarketSummary.tsx`
    - `frontend/src/components/StockTable.tsx`
  - `frontend/src/app/styles.css`

## `/stocks/:market/:code`（个股详情）

入口：`frontend/src/pages/StockDetailPage.tsx`

待实现依赖：

- `frontend/src/app/App.tsx`
  - `frontend/src/components/AppShell.tsx`
  - `frontend/src/pages/StockDetailPage.tsx`
    - `frontend/src/components/KlineChart.tsx`
  - `frontend/src/app/styles.css`
