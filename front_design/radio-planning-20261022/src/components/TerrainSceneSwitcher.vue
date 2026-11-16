<template>
  <label class="terrain-scene-switcher">
    <span>地形场景</span>
    <select :value="currentScene" :disabled="switching || auth.blocked || !!auth.busy || auth.transition" @change="selectScene">
      <option value="hills">丘陵</option>
      <option value="plain">平原</option>
    </select>
  </label>
  <p v-if="error" class="terrain-switch-error" role="alert">{{ error }}</p>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { env } from '../config/env';
import { terrainSceneUrl, type TerrainScene } from '../config/terrain-scenes';
import { useAuthStore } from '../stores/auth';

const currentScene: TerrainScene = env.plainTerrain ? 'plain' : 'hills';
const auth = useAuthStore();
const switching = ref(false);
const error = ref('');

async function selectScene(event: Event) {
  const select = event.target as HTMLSelectElement;
  const scene = select.value;
  select.value = currentScene;
  if ((scene !== 'hills' && scene !== 'plain') || scene === currentScene || switching.value) return;
  switching.value = true;
  error.value = '';
  try {
    if (await auth.prepareSceneSwitch()) window.location.assign(terrainSceneUrl(window.location.href, scene));
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '暂时无法切换地形场景';
  } finally {
    switching.value = false;
  }
}
</script>

<style scoped>
.terrain-scene-switcher { display: inline-flex; align-items: center; gap: 8px; font-size: var(--font-control); }
.terrain-scene-switcher select {
  min-width: 7em;
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: var(--surface);
  color: inherit;
  font: inherit;
}
.terrain-switch-error { margin: 0; font-size: var(--font-control); }
</style>
