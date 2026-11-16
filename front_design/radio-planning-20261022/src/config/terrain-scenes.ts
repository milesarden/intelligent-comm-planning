import { embeddedModuleIds, isEmbeddedPath, modulePath } from './embedded-pages';
import { isModuleTab, moduleTabValues, type TabbedModule } from './module-tabs';

export type TerrainScene = 'hills' | 'plain';

export const terrainScenePaths = { hills: '/', plain: '/scenes/plain/' } as const;
export const plainTerrainApiBase = terrainScenePaths.plain + 'api/v1';

export function isPlainTerrainPath(path: string): boolean {
  return path === terrainScenePaths.plain.slice(0, -1) || path.startsWith(terrainScenePaths.plain);
}

export function terrainSceneUrl(current: string, scene: TerrainScene): string {
  const source = new URL(current);
  const target = new URL(terrainScenePaths[scene], source.origin);
  const [rawPath = '', query = ''] = source.hash.slice(1).split(/[?#]/);
  const embedded = isEmbeddedPath(rawPath);
  const module = modulePath(rawPath).slice(1).replace(/\/$/, '');
  const targetModule = embeddedModuleIds.find(id => id === module) ?? 'situation';
  const tab = new URLSearchParams(query).get('tab');
  // 只带可通用的模块页签，站点、方案和作业等标识属于原地形的数据。
  const keepTab = Object.hasOwn(moduleTabValues, targetModule) &&
    isModuleTab(targetModule as TabbedModule, tab);
  target.hash = (embedded ? '/embed/' : '/') + targetModule + (keepTab ? '?tab=' + tab : '');
  return target.href;
}
