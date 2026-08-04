import { render, screen } from "@testing-library/react";
import { App } from "./App";

test("根路由显示全市场行情基础页面", () => {
  window.history.pushState({}, "", "/");

  render(<App />);

  expect(screen.getByRole("heading", { name: "全市场行情" })).toBeInTheDocument();
});

test("股票详情路由显示个股详情基础页面", () => {
  window.history.pushState({}, "", "/stocks/sh/600000");

  render(<App />);

  expect(screen.getByRole("heading", { name: "个股详情" })).toBeInTheDocument();
});
