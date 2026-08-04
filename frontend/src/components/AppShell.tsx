import type { PropsWithChildren } from "react";
import { NavLink } from "react-router-dom";

export function AppShell({ children }: PropsWithChildren) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand" aria-label="A 股雷达">
          <span className="brand-mark" aria-hidden="true">
            A
          </span>
          <span>A 股雷达</span>
        </div>

        <nav className="primary-nav" aria-label="主导航">
          <span className="nav-label">市场</span>
          <NavLink className="nav-item" to="/">
            <svg aria-hidden="true" viewBox="0 0 20 20">
              <rect x="2.5" y="2.5" width="6" height="6" rx="1" />
              <rect x="11.5" y="2.5" width="6" height="6" rx="1" />
              <rect x="2.5" y="11.5" width="6" height="6" rx="1" />
              <rect x="11.5" y="11.5" width="6" height="6" rx="1" />
            </svg>
            <span>全部股票</span>
          </NavLink>
        </nav>

        <p className="source-status">
          <span className="live-dot" aria-hidden="true" />
          行情服务状态由后端提供
        </p>
      </aside>

      <main className="workspace">{children}</main>
    </div>
  );
}
