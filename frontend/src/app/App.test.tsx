import { render, screen } from "@testing-library/react";
import { App } from "./App";

test("根路由显示全市场行情基础页面", () => {
  window.history.pushState({}, "", "/");

  render(<App />);

  expect(screen.getByRole("heading", { name: "全市场行情" })).toBeInTheDocument();
});

test("股票详情路由显示周期选择与股票标识", () => {
  window.history.pushState({}, "", "/stocks/SH/600000");

  render(<App />);

  expect(screen.getByText("STOCK / SH.600000")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "日K" })).toHaveAttribute("aria-pressed", "true");
});
