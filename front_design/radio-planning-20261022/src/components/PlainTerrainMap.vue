<template>
  <div class="plain-map">
    <div ref="target" class="map-host" role="region" tabindex="0" aria-label="华北平原地形地图，可平移和缩放" />
    <div class="map-info">
      <strong>{{ terrain.name }}</strong>
      <span>{{ terrain.widthKm }} km × {{ terrain.heightKm }} km · DEM</span>
    </div>
    <button type="button" class="fit-button btn small" @click="fitExtent">全域查看</button>
    <p v-if="failed" class="load-error" role="alert">平原底图加载失败，请重新打开场景。</p>
    <div class="elevation-legend">
      <span>高程</span>
      <span>{{ terrain.elevationMetres[0] }} m</span>
      <b aria-hidden="true" />
      <span>{{ terrain.elevationMetres[1] }} m</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import Map from 'ol/Map';
import View from 'ol/View';
import { ScaleLine, defaults } from 'ol/control';
import ImageLayer from 'ol/layer/Image';
import { transformExtent } from 'ol/proj';
import ImageStatic from 'ol/source/ImageStatic';
import terrain from '../assets/terrain/plain.json';
import plainImage from '../assets/terrain/plain.png';

const target = ref<HTMLElement>();
const failed = ref(false);
const extent = transformExtent(terrain.extent, terrain.projection, 'EPSG:3857');
let map: Map | undefined;
let observer: ResizeObserver | undefined;
let source: ImageStatic | undefined;

function fitExtent() {
  map?.getView().fit(extent, { padding: [35, 20, 45, 20] });
}

onMounted(() => {
  if (!target.value) return;
  source = new ImageStatic({ url: plainImage, imageExtent: terrain.extent, projection: terrain.projection });
  source.on('imageloaderror', () => { failed.value = true; });
  source.on('imageloadend', () => { failed.value = false; });
  map = new Map({
    target: target.value,
    layers: [new ImageLayer({ source })],
    view: new View({ maxZoom: 13, showFullExtent: true, extent }),
    controls: defaults({ attribution: false }).extend([new ScaleLine()]),
  });
  fitExtent();
  observer = new ResizeObserver(() => map?.updateSize());
  observer.observe(target.value);
});

onBeforeUnmount(() => {
  observer?.disconnect();
  map?.dispose();
  source?.dispose();
});
</script>

<style scoped>
.plain-map, .map-host { position: absolute; inset: 0; }
.plain-map { background: var(--page-bg, #142334); }
.map-host:focus-visible { outline: 2px solid var(--cyan); outline-offset: -3px; }
.map-info, .elevation-legend, .load-error, .fit-button {
  position: absolute;
  padding: 7px 11px;
  border: 1px solid #456478;
  border-radius: 5px;
  background: #0c1a2bf0;
  color: #dce7f5;
  font-size: var(--font-control);
}
.map-info { top: 16px; left: 65px; display: grid; gap: 3px; }
.fit-button { top: 100px; right: 16px; }
.load-error { top: 76px; left: 65px; margin: 0; }
.elevation-legend { right: 16px; bottom: 20px; display: flex; align-items: center; gap: 6px; }
.elevation-legend b {
  width: clamp(36px, 9vw, 120px);
  height: 7px;
  background: linear-gradient(to right, #225244, #678759, #d4c097);
}
</style>
