/**
 * OpenLayers map. Single instance, exposes helpers to add/remove raster layers.
 */

import "ol/ol.css";
import OlMap from "ol/Map";
import View from "ol/View";
import TileLayer from "ol/layer/Tile";
import OSM from "ol/source/OSM";
import XYZ from "ol/source/XYZ";
import VectorLayer from "ol/layer/Vector";
import VectorSource from "ol/source/Vector";
import Feature from "ol/Feature";
import Point from "ol/geom/Point";
import Polygon from "ol/geom/Polygon";
import { RegularShape, Stroke, Style } from "ol/style";
import { fromLonLat, toLonLat, transformExtent } from "ol/proj";

let map: OlMap | null = null;
const overlayLayers = new Map<string, TileLayer<XYZ>>();
const probeSource = new VectorSource();

export function initMap(): void {
  map = new OlMap({
    target: "map",
    layers: [
      // className lets index.html desaturate the basemap (Swiss palette).
      new TileLayer({ className: "basemap", source: new OSM() }),
      // Probe cursor sits above every raster overlay.
      new VectorLayer({ source: probeSource, zIndex: 1000 }),
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

// ---- Pixel probe cursor ----

export function onMapClick(cb: (lon: number, lat: number) => void): void {
  getMap().on("singleclick", (e) => {
    const [lon, lat] = toLonLat(e.coordinate);
    cb(lon, lat);
  });
}

export function setProbeCursor(on: boolean): void {
  document.getElementById("map")?.classList.toggle("probing", on);
}

/** Outline the locked pixel and mark its centre with a cross (visible at any zoom). */
export function setProbeFootprint(lonlat: [number, number][] | null): void {
  probeSource.clear();
  if (!lonlat) return;
  // Canvas styles can't read CSS variables, so resolve the accent once here.
  const red = getComputedStyle(document.documentElement).getPropertyValue("--red").trim();
  const ring = lonlat.map((c) => fromLonLat(c));
  const cx = ring.reduce((a, c) => a + c[0], 0) / ring.length;
  const cy = ring.reduce((a, c) => a + c[1], 0) / ring.length;
  const stroke = new Stroke({ color: red, width: 2 });
  const footprint = new Feature(new Polygon([[...ring, ring[0]]]));
  footprint.setStyle(new Style({ stroke }));
  const cross = new Feature(new Point([cx, cy]));
  cross.setStyle(new Style({
    image: new RegularShape({ points: 4, radius: 10, radius2: 0, stroke }),
  }));
  probeSource.addFeatures([footprint, cross]);
}
