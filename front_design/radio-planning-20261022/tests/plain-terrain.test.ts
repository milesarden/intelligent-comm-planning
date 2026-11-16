import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { getDistance } from 'ol/sphere';
import { expect, it } from 'vitest';
import plain from '../src/assets/terrain/plain.json';
import hills from '../public/maps/sr4-terrain.json';

it('平原使用独立 DEM 和完整的 120 km × 120 km 区域', () => {
  const [west, south, east, north] = plain.extent;
  const [longitude, latitude] = plain.center;
  expect(plain.projection).toBe('EPSG:4326');
  expect(plain.sourceSha256).not.toBe(hills.source_sha256);
  expect(east).toBeGreaterThan(hills.extent[2]);
  expect(getDistance([west, latitude], [east, latitude])).toBeCloseTo(120_000);
  expect(getDistance([longitude, south], [longitude, north])).toBeCloseTo(120_000);
  const image = readFileSync(new URL('../src/assets/terrain/plain.png', import.meta.url));
  expect(createHash('sha256').update(image).digest('hex')).toBe(plain.imageSha256);
});
