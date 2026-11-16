import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createGisReadPoller, GIS_REFRESH_INTERVAL_MS } from '../src/services/gis-read-poller';

const interval = GIS_REFRESH_INTERVAL_MS;
beforeEach(() => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] }));
afterEach(() => vi.useRealTimers());

describe('GIS只读刷新计时器', () => {
  it('按内置间隔开始下一轮，从请求开始而非结束计时', async () => {
    const startedAt: number[] = [];
    const read = vi.fn(async () => {
      startedAt.push(performance.now());
      await new Promise<void>(done => setTimeout(done, 2_000));
    });
    const poller = createGisReadPoller(read);
    poller.start();
    poller.start();
    await vi.advanceTimersByTimeAsync(interval - 1);
    expect(read).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(interval + 1);
    expect(read).toHaveBeenCalledTimes(2);
    expect(startedAt[1]! - startedAt[0]!).toBe(interval);
    poller.stop();
  });

  it('超时长请求不堆叠，完成后立即补一次检查', async () => {
    let finish!: () => void;
    const read = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
    const poller = createGisReadPoller(read);
    poller.start();
    await vi.advanceTimersByTimeAsync(4 * interval);
    expect(read).toHaveBeenCalledOnce();
    finish();
    await vi.advanceTimersByTimeAsync(1);
    expect(read).toHaveBeenCalledTimes(2);
    poller.stop();
    finish();
  });

  it('暂停后重启仍等待上一轮结束，计划按新的开启时间计算', async () => {
    let finish!: () => void;
    const read = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
    const poller = createGisReadPoller(read);
    poller.start();
    await vi.advanceTimersByTimeAsync(interval);
    poller.stop();
    poller.start();
    await vi.advanceTimersByTimeAsync(10_000);
    finish();
    await vi.advanceTimersByTimeAsync(interval - 10_001);
    expect(read).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(1);
    expect(read).toHaveBeenCalledTimes(2);
    poller.stop();
    finish();
  });

  it('离开或暂停清除计时器，晚到旧轮次不能恢复计时', async () => {
    let finish!: () => void;
    const read = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
    const poller = createGisReadPoller(read);
    poller.start();
    await vi.advanceTimersByTimeAsync(interval);
    poller.stop();
    finish();
    await vi.advanceTimersByTimeAsync(4 * interval);
    expect(read).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('读取失败仍按下一周期检查，停止后不再重试', async () => {
    const read = vi.fn(async () => { throw new Error('只读服务暂不可用'); });
    const poller = createGisReadPoller(read);
    poller.start();
    await vi.advanceTimersByTimeAsync(2 * interval);
    expect(read).toHaveBeenCalledTimes(2);
    poller.stop();
    await vi.advanceTimersByTimeAsync(3 * interval);
    expect(read).toHaveBeenCalledTimes(2);
  });
});
