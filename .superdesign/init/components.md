# 共享组件现状

- `frontend/src/components/AppShell.tsx`：208px 单菜单应用壳，小于 860px 时转换为顶部栏。
- `frontend/src/components/MarketSummary.tsx`：总数、上涨、下跌、平盘、成交额五张真实概览卡。
- `frontend/src/components/StockTable.tsx`：可排序的单行行情表，支持详情链接、整行鼠标点击与键盘进入。

任务 9 新增 `KlineChart` 时，应复用现有颜色令牌、数据字体、卡片边框和焦点样式，不复制应用壳。
