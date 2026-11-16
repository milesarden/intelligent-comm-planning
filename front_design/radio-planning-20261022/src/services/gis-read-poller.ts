// 以开始时间计周期，为30秒更新目标留出网络读取和页面绘制时间。
export const GIS_REFRESH_INTERVAL_MS = 24_000;

export function createGisReadPoller(read: () => Promise<void>, intervalMs = GIS_REFRESH_INTERVAL_MS) {
  let running = false, reading = false;
  let due = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  function schedule() {
    if (!running || reading || timer !== undefined) return;
    timer = setTimeout(async () => {
      timer = undefined;
      if (!running) return;
      reading = true;
      due = performance.now() + intervalMs;
      try { await read(); } catch { /* 业务读取负责展示错误，计时器继续只读检查。 */ }
      finally {
        reading = false;
        // 慢请求结束后最多补做一次检查；暂停期间未结算的请求不重启计时器。
        schedule();
      }
    }, Math.max(0, due - performance.now()));
  }
  return {
    start() {
      if (running) return;
      running = true;
      due = performance.now() + intervalMs;
      schedule();
    },
    stop() {
      running = false;
      clearTimeout(timer);
      timer = undefined;
    },
  };
}
