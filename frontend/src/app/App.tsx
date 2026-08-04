import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "../components/AppShell";

function StockListBasePage() {
  return (
    <section className="page-placeholder">
      <span className="page-kicker">市场总览</span>
      <h1>全市场行情</h1>
    </section>
  );
}

function StockDetailBasePage() {
  return (
    <section className="page-placeholder">
      <span className="page-kicker">股票详情</span>
      <h1>个股详情</h1>
    </section>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<StockListBasePage />} />
          <Route path="/stocks/:market/:code" element={<StockDetailBasePage />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}
