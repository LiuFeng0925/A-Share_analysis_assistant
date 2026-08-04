import { useEffect } from "react";

interface PollingOptions {
  enabled?: boolean;
  immediate?: boolean;
}

export function usePolling(
  load: (signal: AbortSignal) => void | Promise<void>,
  intervalMs: number,
  options: PollingOptions = {},
) {
  const { enabled = true, immediate = true } = options;
  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let inFlight = false;
    let refreshWhenSettled = false;
    let activeController: AbortController | null = null;

    const run = async (queueIfBusy = false) => {
      if (disposed || document.visibilityState !== "visible") return;
      if (inFlight) {
        if (queueIfBusy) refreshWhenSettled = true;
        return;
      }
      inFlight = true;
      const controller = new AbortController();
      activeController = controller;
      try {
        await load(controller.signal);
      } catch {
        // 页面负责把非取消错误转换为可见状态；Hook 只负责调度与清理。
      } finally {
        if (activeController === controller) activeController = null;
        inFlight = false;
        if (refreshWhenSettled && !disposed && document.visibilityState === "visible") {
          refreshWhenSettled = false;
          void run();
        }
      }
    };

    if (immediate) void run();
    const id = window.setInterval(() => {
      void run();
    }, intervalMs);

    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        refreshWhenSettled = false;
        activeController?.abort();
      } else {
        void run(true);
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      disposed = true;
      refreshWhenSettled = false;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      activeController?.abort();
    };
  }, [enabled, immediate, load, intervalMs]);
}
