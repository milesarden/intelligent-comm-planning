import { afterEach, describe, expect, it, vi } from 'vitest';
import { plainTerrainApiBase, terrainSceneUrl } from '../src/config/terrain-scenes';
import { coverageFilePath } from '../src/services/coverage-http';

const origin = 'http://127.0.0.1:18480';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe('同源地形场景入口', () => {
  it('保留嵌入模块和通用页签，切换两端时使用对应的应用根地址', () => {
    const original = origin + '/#/embed/spectrum?tab=spectrum-config';
    const plain = origin + '/scenes/plain/#/embed/spectrum?tab=spectrum-config';
    expect(terrainSceneUrl(original, 'plain')).toBe(plain);
    expect(terrainSceneUrl(plain, 'hills')).toBe(original);
  });

  it('保留完整版规划入口，清除属于原场景的任务、方案和作业编号', () => {
    const original = origin + '/?v=old#/planning?taskId=one&planId=two&jobId=three';
    expect(terrainSceneUrl(original, 'plain')).toBe(origin + '/scenes/plain/#/planning');
    expect(terrainSceneUrl(origin + '/scenes/plain/#/embed/gis-workbench?deviceId=DV-0001', 'hills'))
      .toBe(origin + '/#/embed/gis-workbench');
  });

  it('保留当前站点，丢弃未知页签和不受支持的跳转片段', () => {
    expect(terrainSceneUrl(origin + '/#/embed/knowledge?tab=other', 'plain'))
      .toBe(origin + '/scenes/plain/#/embed/knowledge');
    expect(terrainSceneUrl(origin + '/#//other.invalid/path', 'plain'))
      .toBe(origin + '/scenes/plain/#/situation');
  });
});

describe('场景范围内的资源访问', () => {
  it('平原覆盖接受本服务返回的根相对文件句柄，实际请求保留认证前缀', () => {
    const file = '/files/coverage/raster.png';
    expect(coverageFilePath('/api/v1' + file, plainTerrainApiBase, origin)).toBe(file);
    expect(coverageFilePath(plainTerrainApiBase + file, plainTerrainApiBase, origin)).toBe(file);
    expect(() => coverageFilePath(origin + '/api/v1' + file, plainTerrainApiBase, origin)).toThrow();
    expect(() => coverageFilePath('http://other.invalid/api/v1' + file, plainTerrainApiBase, origin)).toThrow();
    expect(() => coverageFilePath('/api/v1/files/%2e%2e/raster.png', plainTerrainApiBase, origin)).toThrow();
  });

  it('一体包平原按场景隔离待核实与本地业务键，旧丘陵记录仍使用原键', async () => {
    const oldKey = 'device-configuration-pending-a1:local';
    const storage = new Map([[oldKey, 'hills']]);
    const browser = {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => { storage.set(key, value); },
    };
    const old = await import('../src/services/terrain-storage');
    expect(old.terrainStorageKey(oldKey)).toBe(oldKey);
    vi.stubEnv('VITE_UNIFIED_TERRAIN', '1');
    vi.stubEnv('VITE_TERRAIN_SCENE', 'plain');
    vi.resetModules();
    const plain = await import('../src/services/terrain-storage');
    expect(plain.terrainStorageKey(oldKey)).toBe('terrain-plain:' + oldKey);
    expect(plain.terrainStorage(browser).getItem(oldKey)).toBeNull();
    plain.terrainStorage(browser).setItem(oldKey, 'plain');
    expect(browser.getItem(oldKey)).toBe('hills');
    expect(plain.terrainStorage(browser).getItem(oldKey)).toBe('plain');
    expect((await import('../src/config/env')).env.apiBaseUrl).toBe(plainTerrainApiBase);
  });
});
