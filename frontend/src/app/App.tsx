import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { StockListPage } from "../pages/StockListPage";
import { StockDetailPage } from "../pages/StockDetailPage";

export function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<StockListPage />} />
          <Route path="/stocks/:market/:code" element={<StockDetailPage />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  );
}
