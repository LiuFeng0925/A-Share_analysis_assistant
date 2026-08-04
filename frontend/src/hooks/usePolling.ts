import { useEffect } from "react";

export function usePolling(load: () => void | Promise<void>, intervalMs: number) {
  useEffect(() => {
    void load();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void load();
      }
    }, intervalMs);

    return () => window.clearInterval(id);
  }, [load, intervalMs]);
}
