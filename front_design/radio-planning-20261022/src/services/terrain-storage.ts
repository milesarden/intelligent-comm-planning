import { env } from '../config/env';
import type { ResourceStorage } from '../types/resource-edit';

const separate = env.unifiedTerrain && env.plainTerrain;

export function terrainStorageKey(key: string): string {
  return separate ? 'terrain-plain:' + key : key;
}

export function terrainStorage(storage: ResourceStorage): ResourceStorage {
  if (!separate) return storage;
  return {
    getItem: key => storage.getItem(terrainStorageKey(key)),
    setItem: (key, value) => storage.setItem(terrainStorageKey(key), value),
  };
}
