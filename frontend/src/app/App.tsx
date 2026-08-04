import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { StockListPage } from "../pages/StockListPage";

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
          <Route path="/" element={<StockListPage />} />
          <Route path="/stocks/:market/:code" element={<StockDetailBasePage />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}
