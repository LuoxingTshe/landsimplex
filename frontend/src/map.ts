/**
 * OpenLayers map. Single instance, exposes helpers to add/remove raster layers.
 */

import "ol/ol.css";
import OlMap from "ol/Map";
import View from "ol/View";
import TileLayer from "ol/layer/Tile";
import OSM from "ol/source/OSM";
import XYZ from "ol/source/XYZ";
import { transformExtent } from "ol/proj";

let map: OlMap | null = null;
const overlayLayers = new Map<string, TileLayer<XYZ>>();

export function initMap(): void {
  map = new OlMap({
    target: "map",
    layers: [
      new TileLayer({ source: new OSM() }),
    ],
    view: new View({
      center: [0, 0],
      zoom: 2,
      projection: "EPSG:3857",
    }),
  });
}

export function getMap(): OlMap {
  if (!map) throw new Error("Map not initialised");
  return map;
}

export function addRasterLayer(rasterId: string, tileUrl: string, opacity = 0.85): void {
  removeRasterLayer(rasterId);
  const layer = new TileLayer({
    source: new XYZ({
      url: tileUrl,
      crossOrigin: "anonymous",
      // Browser caching keyed on URL; rescale/colormap changes give a new URL
    }),
    opacity,
  });
  layer.set("rasterId", rasterId);
  getMap().addLayer(layer);
  overlayLayers.set(rasterId, layer);
}

export function removeRasterLayer(rasterId: string): void {
  const existing = overlayLayers.get(rasterId);
  if (existing) {
    getMap().removeLayer(existing);
    overlayLayers.delete(rasterId);
  }
}

export function setLayerVisible(rasterId: string, visible: boolean): void {
  overlayLayers.get(rasterId)?.setVisible(visible);
}

export function fitToBoundsWGS84(b: [number, number, number, number]): void {
  const extent = transformExtent(b, "EPSG:4326", "EPSG:3857");
  getMap().getView().fit(extent, { padding: [40, 40, 40, 40], maxZoom: 14 });
}
