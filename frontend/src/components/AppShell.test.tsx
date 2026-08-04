import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppShell } from "./AppShell";

test("只显示全部股票一个菜单项", () => {
  render(
    <MemoryRouter>
      <AppShell>
        <div>内容</div>
      </AppShell>
    </MemoryRouter>,
  );

  expect(screen.getAllByRole("link")).toHaveLength(1);
  expect(screen.getByRole("link", { name: "全部股票" })).toBeInTheDocument();
  expect(screen.queryByText("条件筛选")).not.toBeInTheDocument();
  expect(screen.queryByText("我的关注")).not.toBeInTheDocument();
});
