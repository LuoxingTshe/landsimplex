/**
 * Side panel UI: render raster list, algorithm form, manage jobs.
 *
 * Plain DOM - no framework. The whole panel is under 200 lines, which is the
 * MVP threshold for "don't bother with React".
 */

import {
  AlgorithmInfo,
  Job,
  ParamRangeSpec,
  Raster,
  SimplexSpec,
  Scene,
  deleteRaster,
  RasterInUseError,
  getJob,
  listAlgorithms,
  listJobChildren,
  listRasters,
  listScenes,
  previewJob,
  setBandRole,
  submitJob,
  tileUrl,
  uploadRaster,
  uploadScene,
} from "./api";
import { addRasterLayer, fitToBoundsWGS84, removeRasterLayer, setLayerVisible } from "./map";

const MAX_SWEEP_SAMPLES = 200;

/** C(n + T - 1, T) — number of lattice points on the (n-1)-simplex at resolution T. */
function simplexGridSize(n: number, T: number): number {
  const k = Math.min(T, n - 1);
  let result = 1;
  for (let i = 0; i < k; i++) {
    result = result * (n + T - 1 - i) / (i + 1);
  }
  return Math.round(result);
}

/**
 * Reference table: rows = n (2–6), cols = T (1–10).
 * Highlights the active (n, T) cell; dims values over the sweep cap.
 */
function buildSimplexTable(activeN: number, activeT: number): HTMLElement {
  const nVals = [2, 3, 4, 5, 6];
  const tVals = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

  const wrap = document.createElement("div");
  wrap.style.cssText = "overflow-x:auto;margin:2px 0 4px;";

  const tbl = document.createElement("table");
  tbl.style.cssText = "border-collapse:collapse;font-size:10px;white-space:nowrap;";

  // Header row: T values
  const hrow = document.createElement("tr");
  const corner = document.createElement("td");
  corner.style.cssText = "padding:1px 4px 1px 2px;color:#bbb;";
  corner.textContent = "n\\T";
  hrow.appendChild(corner);
  for (const t of tVals) {
    const th = document.createElement("td");
    const isActiveCol = t === activeT;
    th.style.cssText = `padding:1px 4px;text-align:right;`
      + `color:${isActiveCol ? "#444" : "#bbb"};`
      + `font-weight:${isActiveCol ? "bold" : "normal"};`
      + (isActiveCol ? "background:#eef3ff;" : "");
    th.textContent = String(t);
    hrow.appendChild(th);
  }
  tbl.appendChild(hrow);

  // Data rows: one per n
  for (const n of nVals) {
    const tr = document.createElement("tr");
    const isActiveRow = n === activeN;
    const tdLabel = document.createElement("td");
    tdLabel.style.cssText = `padding:1px 4px 1px 2px;`
      + `color:${isActiveRow ? "#444" : "#bbb"};`
      + `font-weight:${isActiveRow ? "bold" : "normal"};`
      + (isActiveRow ? "background:#eef3ff;" : "");
    tdLabel.textContent = String(n);
    tr.appendChild(tdLabel);

    for (const t of tVals) {
      const count = simplexGridSize(n, t);
      const isCurrent = isActiveRow && t === activeT;
      const overCap = count > MAX_SWEEP_SAMPLES;
      const dimmed = !isActiveRow && t !== activeT;
      const td = document.createElement("td");
      td.style.cssText = `padding:1px 4px;text-align:right;`
        + `font-weight:${isCurrent ? "bold" : "normal"};`
        + `background:${isCurrent ? "#d8e6ff" : (isActiveRow || t === activeT ? "#f4f7ff" : "")};`
        + `color:${overCap ? "#c44" : (dimmed ? "#ddd" : "#333")};`;
      td.textContent = overCap ? `${count}↑` : String(count);
      tr.appendChild(td);
    }
    tbl.appendChild(tr);
  }

  wrap.appendChild(tbl);
  return wrap;
}

const state = {
  rasters: [] as Raster[],
  algorithms: [] as AlgorithmInfo[],
  activeLayers: new Set<string>(),
  jobs: [] as Job[],
  currentTab: "rasters" as "rasters" | "scenes",
  scenes: [] as Scene[],
  currentAlgoTab: "builtin" as "builtin" | "composite",
};

const panel = () => document.getElementById("panel")!;

export async function initPanel(): Promise<void> {
  state.algorithms = await listAlgorithms();
  await Promise.all([refreshRasters(), refreshScenes()]);
  render();
}

async function refreshRasters(): Promise<void> {
  state.rasters = await listRasters();
}

async function refreshScenes(): Promise<void> {
  state.scenes = await listScenes();
}

function render(): void {
  panel().innerHTML = "";
  panel().appendChild(tabBar());
  if (state.currentTab === "rasters") {
    panel().appendChild(uploadSection());
  } else {
    panel().appendChild(sceneBundleSection());
  }
  panel().appendChild(rasterListSection());
  panel().appendChild(algorithmSection());
  panel().appendChild(jobsSection());
}

function tabBar(): HTMLElement {
  const bar = document.createElement("div");
  bar.style.cssText = "display:flex;gap:4px;margin-bottom:8px;";
  const tabs = [
    { id: "rasters", label: "Raster" },
    { id: "scenes", label: "Scene Bundle" },
  ] as const;
  for (const t of tabs) {
    const btn = document.createElement("button");
    btn.textContent = t.label;
    btn.style.cssText = `flex:1;padding:4px 0;cursor:pointer;${state.currentTab === t.id ? "font-weight:bold;border-bottom:2px solid #4a90d9;" : ""}`;
    btn.onclick = () => { state.currentTab = t.id; render(); };
    bar.appendChild(btn);
  }
  return bar;
}

function uploadSection(): HTMLElement {
  const el = document.createElement("section");
  el.innerHTML = `
    <h2>Upload raster</h2>
    <div class="upload">
      <input type="file" id="upload-input" accept=".tif,.tiff" />
      <button id="upload-btn">Upload</button>
      <div id="upload-status" style="font-size: 12px; color: #888; margin-top: 4px;"></div>
    </div>
  `;
  const input = el.querySelector<HTMLInputElement>("#upload-input")!;
  const btn = el.querySelector<HTMLButtonElement>("#upload-btn")!;
  const status = el.querySelector<HTMLDivElement>("#upload-status")!;

  btn.onclick = async () => {
    const file = input.files?.[0];
    if (!file) { status.textContent = "Pick a file first"; return; }
    btn.disabled = true;
    status.textContent = `Uploading & cogifying ${file.name}...`;
    try {
      const raster = await uploadRaster(file);
      status.textContent = `Imported ${raster.name}`;
      await refreshRasters();
      render();
      // auto-show the freshly uploaded raster
      toggleLayer(raster.id);
      fitToBoundsWGS84(raster.bounds_wgs84);
    } catch (err) {
      status.textContent = `Failed: ${err}`;
    } finally {
      btn.disabled = false;
    }
  };
  return el;
}

function rasterListSection(): HTMLElement {
  const el = document.createElement("section");
  el.innerHTML = `<h2>Rasters (${state.rasters.length})</h2>`;
  for (const r of state.rasters) {
    const item = document.createElement("div");
    item.className = "raster-item";
    const active = state.activeLayers.has(r.id);
    item.innerHTML = `
      <div class="layer-toggle">
        <input type="checkbox" ${active ? "checked" : ""} data-rid="${r.id}" />
        <strong>${r.name}</strong>
        <span style="margin-left: auto; font-size: 10px; padding: 1px 6px; border-radius: 8px; background: ${r.kind === "result" ? "#fce4a4" : "#cce3fc"};">${r.kind}</span>
        <button class="delete-btn" title="Delete layer" style="margin-left:6px;padding:1px 6px;font-size:11px;color:#c00;background:transparent;border:1px solid #c00;border-radius:4px;cursor:pointer;line-height:1.4;">✕</button>
      </div>
      <div class="meta">${r.crs} · ${r.width}×${r.height} · ${r.dtype}</div>
    `;
    const cb = item.querySelector<HTMLInputElement>("input[type=checkbox]")!;
    cb.onchange = () => {
      if (cb.checked) {
        addLayer(r);
      } else {
        removeRasterLayer(r.id);
        state.activeLayers.delete(r.id);
      }
    };
    const delBtn = item.querySelector<HTMLButtonElement>(".delete-btn")!;
    delBtn.onclick = async (ev) => {
      ev.stopPropagation();
      if (!confirm(`Delete "${r.name}"? This cannot be undone.`)) return;
      try {
        try {
          await deleteRaster(r.id);
        } catch (err) {
          if (!(err instanceof RasterInUseError)) throw err;
          const list = err.jobs.map((j) => `  • ${j.algorithm} (${j.status}, ${j.role})`).join("\n");
          if (err.code === "in_use") {
            alert(`Cannot delete "${r.name}": used by a running/pending job.\n${list}`);
            return;
          }
          if (!confirm(`"${r.name}" is an input of ${err.jobs.length} existing job(s):\n${list}\n\nDelete anyway?`)) return;
          await deleteRaster(r.id, true);
        }
        if (state.activeLayers.has(r.id)) {
          removeRasterLayer(r.id);
          state.activeLayers.delete(r.id);
        }
        await refreshRasters();
        render();
      } catch (err) {
        alert(`Delete failed: ${err}`);
      }
    };
    item.onclick = (ev) => {
      if ((ev.target as HTMLElement).tagName !== "INPUT" && !(ev.target as HTMLElement).classList.contains("delete-btn")) {
        fitToBoundsWGS84(r.bounds_wgs84);
      }
    };
    el.appendChild(item);
  }
  return el;
}

const BAND_ROLES = [
  "blue", "green", "red", "nir", "nir_narrow",
  "swir1", "swir2", "pan",
  "rededge1", "rededge2", "rededge3",
  "coastal", "water_vapor", "thermal", "thermal2", "other",
];

function sceneBundleSection(): HTMLElement {
  const el = document.createElement("section");
  el.innerHTML = `<h2>Upload scene bundle</h2>`;

  const uploadDiv = document.createElement("div");
  uploadDiv.className = "upload";
  uploadDiv.innerHTML = `
    <input type="file" id="scene-input" accept=".zip,.tar.gz" />
    <button id="scene-btn">Upload</button>
    <div id="scene-status" style="font-size:12px;color:#888;margin-top:4px;"></div>
  `;
  el.appendChild(uploadDiv);

  const input = uploadDiv.querySelector<HTMLInputElement>("#scene-input")!;
  const btn = uploadDiv.querySelector<HTMLButtonElement>("#scene-btn")!;
  const status = uploadDiv.querySelector<HTMLDivElement>("#scene-status")!;

  btn.onclick = async () => {
    const file = input.files?.[0];
    if (!file) { status.textContent = "Pick a .zip or .tar.gz file first"; return; }
    btn.disabled = true;
    status.textContent = `Uploading ${file.name}…`;
    try {
      const scene = await uploadScene(file);
      status.textContent = `Imported ${scene.bands.length} band(s) from ${scene.scene_id}`;
      if (scene.failed_bands.length > 0) {
        status.textContent += ` (${scene.failed_bands.length} failed)`;
      }
      await Promise.all([refreshRasters(), refreshScenes()]);
      // Auto-show the red band if present.
      const redBand = scene.bands.find((b) => b.role === "red");
      if (redBand) { addLayer(redBand); fitToBoundsWGS84(redBand.bounds_wgs84); }
      render();
    } catch (err) {
      status.textContent = `Failed: ${err}`;
    } finally {
      btn.disabled = false;
    }
  };

  // Show existing scenes.
  if (state.scenes.length > 0) {
    const heading = document.createElement("h3");
    heading.textContent = "Imported scenes";
    heading.style.margin = "12px 0 4px";
    el.appendChild(heading);

    for (const scene of state.scenes) {
      el.appendChild(sceneCard(scene));
    }
  }

  return el;
}

function sceneCard(scene: Scene): HTMLElement {
  const card = document.createElement("div");
  card.style.cssText = "border:1px solid #ddd;border-radius:4px;padding:6px;margin-bottom:8px;";
  card.innerHTML = `
    <div style="font-weight:bold;font-size:12px;">${scene.scene_id}</div>
    <div style="font-size:11px;color:#666;">${scene.sensor} · ${scene.acquired_at ?? "unknown date"}</div>
  `;

  const table = document.createElement("table");
  table.style.cssText = "width:100%;font-size:11px;border-collapse:collapse;margin-top:4px;";
  table.innerHTML = `<thead><tr>
    <th style="text-align:left;padding:2px 4px;">Band</th>
    <th style="text-align:left;padding:2px 4px;">Auto role</th>
    <th style="text-align:left;padding:2px 4px;">Role override</th>
  </tr></thead>`;
  const tbody = document.createElement("tbody");

  for (const band of scene.bands) {
    const tr = document.createElement("tr");
    const sel = document.createElement("select");
    sel.style.fontSize = "11px";
    sel.innerHTML = BAND_ROLES.map(
      (r) => `<option value="${r}"${band.role === r ? " selected" : ""}>${r}</option>`
    ).join("");
    sel.onchange = async () => {
      try {
        await setBandRole(band.id, sel.value);
      } catch (err) {
        alert(`Role update failed: ${err}`);
        sel.value = band.role ?? "";
      }
    };

    const tdBand = document.createElement("td");
    tdBand.style.padding = "2px 4px";
    tdBand.textContent = band.band_id ?? "—";

    const tdAuto = document.createElement("td");
    tdAuto.style.padding = "2px 4px";
    tdAuto.textContent = band.auto_role ?? "—";

    const tdRole = document.createElement("td");
    tdRole.style.padding = "2px 4px";
    tdRole.appendChild(sel);

    tr.appendChild(tdBand);
    tr.appendChild(tdAuto);
    tr.appendChild(tdRole);
    tbody.appendChild(tr);
  }

  table.appendChild(tbody);
  card.appendChild(table);
  return card;
}

function addLayer(r: Raster): void {
  let rescale: [number, number];
  if (r.kind === "result" && r.stats_min != null && r.stats_max != null) {
    // Use actual data range computed when the result was written.
    rescale = [r.stats_min, r.stats_max];
  } else if (r.kind === "result") {
    rescale = [0, 90]; // fallback for results without stored stats
  } else {
    rescale = [0, 4000]; // generic DEM
  }
  // threshold_probability → black-red gradient for intuitive probability display
  const cmap = r.name.startsWith("threshold_probability") ? "prob-coldhot" : "viridis";
  addRasterLayer(r.id, tileUrl(r.id, rescale, cmap));
  state.activeLayers.add(r.id);
}

function toggleLayer(rasterId: string): void {
  const r = state.rasters.find((x) => x.id === rasterId);
  if (!r) return;
  if (state.activeLayers.has(rasterId)) {
    removeRasterLayer(rasterId);
    state.activeLayers.delete(rasterId);
  } else {
    addLayer(r);
  }
  render();
}

/**
 * Render a plain numeric input. Carries identifier attributes so the submit
 * collector can find it (e.g. `data-param=peak_deg` or
 * `data-paired-param=weights data-paired-index=0`).
 */
function renderSweepableInput(
  parent: HTMLElement,
  identifierAttrs: Record<string, string>,
  opts: {
    defaultValue: string;
    step?: string;
    compact?: boolean;
    onChange: () => void;
  },
): void {
  const wrap = document.createElement("span");
  wrap.style.cssText = "display:inline-flex;gap:4px;align-items:center;flex:1;";
  for (const [k, v] of Object.entries(identifierAttrs)) wrap.setAttribute(k, v);

  const single = document.createElement("input");
  single.type = "number";
  single.value = opts.defaultValue;
  if (opts.step) single.step = opts.step;
  single.style.cssText = opts.compact ? "width:60px;" : "flex:1;";
  single.className = "sweep-single";
  single.oninput = opts.onChange;
  wrap.appendChild(single);

  parent.appendChild(wrap);
}

/**
 * Read per-component min/max inputs for a simplex axis (paramName).
 * Returns null when all values are at their defaults (min=0 / max=1),
 * meaning "no bounds" — caller should skip the bounds payload and use the
 * local binomial counter. Returns the arrays otherwise so the caller can
 * forward them to the backend preview endpoint.
 */
function readBoundsForParam(
  form: HTMLElement,
  paramName: string,
): { min: number[]; max: number[] } | null {
  const wraps = Array.from(
    form.querySelectorAll<HTMLElement>(`[data-bounds-param="${paramName}"]`),
  ).sort(
    (a, b) =>
      parseInt(a.dataset.boundsIndex!, 10) - parseInt(b.dataset.boundsIndex!, 10),
  );
  if (wraps.length === 0) return null;
  const min: number[] = [];
  const max: number[] = [];
  let anyNonDefault = false;
  for (const w of wraps) {
    const minInput = w.querySelector<HTMLInputElement>(".weight-min")!;
    const maxInput = w.querySelector<HTMLInputElement>(".weight-max")!;
    const lo = parseFloat(minInput.value);
    const hi = parseFloat(maxInput.value);
    const loVal = Number.isFinite(lo) ? lo : 0;
    const hiVal = Number.isFinite(hi) ? hi : 1;
    if (loVal !== 0 || hiVal !== 1) anyNonDefault = true;
    min.push(loVal);
    max.push(hiVal);
  }
  return anyNonDefault ? { min, max } : null;
}

/** Collect per-paired-param base values into a Record for preview payloads. */
function readPairedValues(form: HTMLElement): Record<string, number[]> {
  const out: Record<string, number[]> = {};
  const counts: Record<string, number> = {};
  form.querySelectorAll<HTMLElement>("[data-paired-param]").forEach((w) => {
    const name = w.getAttribute("data-paired-param")!;
    const idx = parseInt(w.getAttribute("data-paired-index")!, 10);
    counts[name] = Math.max(counts[name] ?? 0, idx + 1);
    const single = w.querySelector<HTMLInputElement>(".sweep-single")!;
    const v = parseFloat(single.value);
    (out[name] ||= [])[idx] = Number.isFinite(v) ? v : 0;
  });
  for (const [name, arr] of Object.entries(out)) {
    for (let i = 0; i < counts[name]; i++) if (arr[i] === undefined) arr[i] = 0;
  }
  return out;
}

// Monotonic token: each recomputeSweepCount call bumps it; stale async
// /jobs/preview responses (returning after a newer call started) are dropped.
let previewToken = 0;

/**
 * Walk active simplex wrappers under `form`, multiply their sample counts,
 * and update the counter + Run button. When any axis has non-default bounds
 * the binomial formula is no longer exact, so we debounce-call POST
 * /jobs/preview for the real filtered count.
 */
function recomputeSweepCount(
  algo: AlgorithmInfo,
  form: HTMLElement,
  counter: HTMLElement,
  runBtn: HTMLButtonElement,
): void {
  const myToken = ++previewToken;
  let nLocal = 1;
  let invalidReason: string | null = null;
  const ranges: Record<string, ParamRangeSpec> = {};
  let usesBounds = false;

  const simplexWrappers = form.querySelectorAll<HTMLElement>('[data-simplex-active="on"]');
  simplexWrappers.forEach((w) => {
    const paramName = w.getAttribute("data-simplex-param")!;
    const tInput = w.querySelector<HTMLInputElement>(".simplex-T");
    const T = tInput ? parseInt(tInput.value, 10) : NaN;
    const dim = form.querySelectorAll(`[data-paired-param="${paramName}"]`).length;
    if (!Number.isInteger(T) || T < 1) {
      invalidReason = "单纯形 T 必须是正整数";
      return;
    }
    if (dim < 2) {
      invalidReason = "单纯形采样需至少 2 层";
      return;
    }
    nLocal *= simplexGridSize(dim, T);
    const spec: SimplexSpec = { kind: "simplex", n_divisions: T };
    const bounds = readBoundsForParam(form, paramName);
    if (bounds) {
      // Reject obviously-invalid bounds locally so we don't spam the backend.
      for (let i = 0; i < bounds.min.length; i++) {
        if (bounds.min[i] > bounds.max[i]) {
          invalidReason = `w${i}: min > max`;
          return;
        }
        if (bounds.min[i] < 0 || bounds.max[i] > 1) {
          invalidReason = `w${i}: 边界须在 [0, 1]`;
          return;
        }
      }
      const sumMin = bounds.min.reduce((a, b) => a + b, 0);
      const sumMax = bounds.max.reduce((a, b) => a + b, 0);
      if (sumMin > 1 + 1e-9) { invalidReason = "sum(min) > 1，不可行"; return; }
      if (sumMax < 1 - 1e-9) { invalidReason = "sum(max) < 1，不可行"; return; }
      spec.min = bounds.min;
      spec.max = bounds.max;
      usesBounds = true;
    }
    ranges[paramName] = spec;
  });

  const hasSweep = simplexWrappers.length > 0;
  if (invalidReason) {
    counter.textContent = invalidReason;
    counter.style.color = "#c00";
    runBtn.disabled = true;
    return;
  }
  if (!hasSweep) {
    counter.textContent = "";
    runBtn.disabled = false;
    return;
  }

  if (!usesBounds) {
    // Closed-form — instant. (Single-sample collapse is still legal but yields a single child.)
    if (nLocal > MAX_SWEEP_SAMPLES) {
      counter.textContent = `将运行 ${nLocal} 次 (超过上限 ${MAX_SWEEP_SAMPLES})`;
      counter.style.color = "#c00";
      runBtn.disabled = true;
    } else if (nLocal === 1) {
      counter.textContent = "";
      runBtn.disabled = false;
    } else {
      counter.textContent = `将运行 ${nLocal} 次`;
      counter.style.color = "#3a3";
      runBtn.disabled = false;
    }
    return;
  }

  // Bounded path — defer to backend for the exact post-filter count.
  counter.textContent = "计算中...";
  counter.style.color = "#888";
  runBtn.disabled = true;
  const params = readPairedValues(form);
  previewJob(algo.name, params, ranges).then(
    (resp) => {
      if (myToken !== previewToken) return;
      if (resp.sample_count > resp.max_samples) {
        counter.textContent = `将运行 ${resp.sample_count} 次 (超过上限 ${resp.max_samples})`;
        counter.style.color = "#c00";
        runBtn.disabled = true;
      } else if (resp.sample_count === 1) {
        counter.textContent = "";
        runBtn.disabled = false;
      } else {
        counter.textContent = `将运行 ${resp.sample_count} 次`;
        counter.style.color = "#3a3";
        runBtn.disabled = false;
      }
    },
    (err) => {
      if (myToken !== previewToken) return;
      counter.textContent = String(err.message ?? err);
      counter.style.color = "#c00";
      runBtn.disabled = true;
    },
  );
}

function algorithmSection(): HTMLElement {
  const el = document.createElement("section");
  el.innerHTML = `<h2>Run algorithm</h2>`;

  // Tab bar: builtin vs composite. Filters the algorithm dropdown below.
  const algoTabs = document.createElement("div");
  algoTabs.style.cssText = "display:flex;gap:4px;margin-bottom:6px;";
  const tabs = [
    { id: "builtin", label: "基础算法" },
    { id: "composite", label: "复合分析" },
  ] as const;
  for (const t of tabs) {
    const btn = document.createElement("button");
    btn.textContent = t.label;
    btn.style.cssText = `flex:1;padding:4px 0;cursor:pointer;${state.currentAlgoTab === t.id ? "font-weight:bold;border-bottom:2px solid #4a90d9;" : ""}`;
    btn.onclick = () => { state.currentAlgoTab = t.id; render(); };
    algoTabs.appendChild(btn);
  }
  el.appendChild(algoTabs);

  const visibleAlgos = state.algorithms.filter(
    (a) => (state.currentAlgoTab === "composite") === !!a.is_composite,
  );
  const select = document.createElement("select");
  select.innerHTML = `<option value="">— Choose —</option>` +
    visibleAlgos.map((a) => `<option value="${a.name}">${a.display}</option>`).join("");
  el.appendChild(select);

  const form = document.createElement("div");
  form.className = "algo-form";
  form.style.display = "none";
  el.appendChild(form);

  select.onchange = () => {
    const algo = state.algorithms.find((a) => a.name === select.value);
    form.innerHTML = "";
    form.style.display = algo ? "block" : "none";
    if (!algo) return;

    const sourceRasters = state.rasters; // include results too; user might want to chain

    // Sample-count banner — updated by recomputeSweepCount on every input.
    const sweepCounter = document.createElement("div");
    sweepCounter.style.cssText = "font-size:11px;color:#3a3;margin:4px 0 6px;min-height:14px;";
    form.appendChild(sweepCounter);

    // Created here so recompute() can disable/enable it before it is appended.
    const runBtn = document.createElement("button");
    runBtn.textContent = "Run";
    runBtn.style.marginTop = "8px";
    runBtn.onclick = () => submit(algo, form);
    const recompute = () => recomputeSweepCount(algo, form, sweepCounter, runBtn);

    // For algorithms with declared inputs (e.g. slope): one picker per role
    for (const input of algo.inputs) {
      const lbl = document.createElement("label");
      lbl.textContent = `${input.role} — ${input.description}`;
      const sel = document.createElement("select");
      sel.dataset.role = input.role;
      sel.innerHTML = sourceRasters.map((r) => `<option value="${r.id}">${r.name}</option>`).join("");
      form.appendChild(lbl);
      form.appendChild(sel);
    }

    // Dynamic inputs: schema-driven N-pickers for algorithms like weighted_overlay.
    // If the dynamic spec declares a paired_param, render one value per generated
    // input (e.g. weights[i] alongside layer_i) and skip that param below.
    const dyn = algo.dynamic_inputs;
    // Look up the ParamSpec for the paired param so the inner inputs inherit
    // its sweepable / step_hint metadata.
    const pairedParamSpec = dyn?.paired_param
      ? algo.params.find((p) => p.name === dyn.paired_param)
      : undefined;
    if (dyn) {
      const lbl = document.createElement("label");
      lbl.textContent = `Number of ${dyn.role_prefix.replace(/_$/, "")}s`;
      const numInput = document.createElement("input");
      numInput.type = "number";
      numInput.min = String(dyn.min_count);
      numInput.max = String(dyn.max_count);
      numInput.value = String(dyn.min_count);
      form.appendChild(lbl);
      form.appendChild(numInput);

      // Simplex mode toggle — only for list[float] paired params (e.g. weights).
      // simplexRow / renderSimplexTable are declared here (outer scope) so that
      // renderDyn (defined below) can safely call renderSimplexTable.
      let simplexRow: HTMLElement | null = null;
      let renderSimplexTable: () => void = () => {};  // no-op until simplex configured

      if (dyn.paired_param && pairedParamSpec?.type === "list[float]") {
        simplexRow = document.createElement("div");
        simplexRow.style.cssText = "display:flex;align-items:center;gap:6px;margin:5px 0 2px;flex-wrap:wrap;";
        simplexRow.dataset.simplexParam = dyn.paired_param;
        simplexRow.dataset.simplexActive = "off";

        const simplexToggleCb = document.createElement("input");
        simplexToggleCb.type = "checkbox";
        simplexToggleCb.style.cssText = "margin:0;cursor:pointer;";

        const simplexLabel = document.createElement("label");
        simplexLabel.style.cssText = "display:inline-flex;align-items:center;gap:4px;cursor:pointer;color:#555;font-size:11px;";
        simplexLabel.appendChild(simplexToggleCb);
        simplexLabel.appendChild(document.createTextNode("单纯形采样"));

        const simplexTLabel = document.createElement("span");
        simplexTLabel.style.cssText = "color:#555;font-size:11px;";
        simplexTLabel.textContent = "T =";

        const simplexT = document.createElement("input");
        simplexT.type = "number";
        simplexT.min = "1";
        simplexT.step = "1";
        simplexT.value = "4";
        simplexT.className = "simplex-T";
        simplexT.style.cssText = "width:48px;font-size:11px;";
        simplexT.disabled = true;

        // Reference table shown below the simplex control when active.
        const simplexTableDiv = document.createElement("div");
        simplexTableDiv.style.display = "none";

        // Assign the actual implementation to the outer-scope variable.
        renderSimplexTable = () => {
          simplexTableDiv.innerHTML = "";
          if (simplexRow!.dataset.simplexActive !== "on") {
            simplexTableDiv.style.display = "none";
            return;
          }
          simplexTableDiv.style.display = "";
          const n = parseInt(numInput.value, 10);
          const T = parseInt(simplexT.value, 10);
          simplexTableDiv.appendChild(buildSimplexTable(isNaN(n) ? 2 : n, isNaN(T) ? 0 : T));
        };

        simplexToggleCb.onchange = () => {
          const on = simplexToggleCb.checked;
          simplexRow!.dataset.simplexActive = on ? "on" : "off";
          simplexT.disabled = !on;
          // Reveal / hide the per-component min/max inputs paired with this axis.
          form
            .querySelectorAll<HTMLElement>(`[data-bounds-param="${dyn.paired_param}"]`)
            .forEach((b) => { b.style.display = on ? "inline-flex" : "none"; });
          renderSimplexTable();
          recompute();
        };
        simplexT.oninput = () => { renderSimplexTable(); recompute(); };

        simplexRow.appendChild(simplexLabel);
        simplexRow.appendChild(simplexTLabel);
        simplexRow.appendChild(simplexT);
        form.appendChild(simplexRow);
        form.appendChild(simplexTableDiv);
      }

      const dynContainer = document.createElement("div");
      form.appendChild(dynContainer);
      const renderDyn = () => {
        dynContainer.innerHTML = "";
        const n = parseInt(numInput.value, 10);
        for (let i = 0; i < n; i++) {
          const l = document.createElement("label");
          l.textContent = dyn.paired_param
            ? `${dyn.role_prefix}${i} (${dyn.paired_param})`
            : `${dyn.role_prefix}${i} — ${dyn.description}`;
          const row = document.createElement("div");
          row.style.display = "flex";
          row.style.gap = "4px";
          row.style.alignItems = "center";
          const sel = document.createElement("select");
          sel.dataset.role = `${dyn.role_prefix}${i}`;
          sel.style.flex = "1";
          sel.style.minWidth = "0";
          sel.innerHTML = sourceRasters.map((r) => `<option value="${r.id}">${r.name}</option>`).join("");
          row.appendChild(sel);
          if (dyn.paired_param) {
            renderSweepableInput(row, {
              "data-paired-param": dyn.paired_param,
              "data-paired-index": `${i}`,
            }, {
              defaultValue: "1",
              step: pairedParamSpec?.step_hint ? String(pairedParamSpec.step_hint) : "0.1",
              compact: true,
              onChange: recompute,
            });
            // Per-component bound inputs. Hidden until the simplex toggle is on.
            // Only relevant when the paired param is a list[float] sampled on a simplex.
            if (pairedParamSpec?.type === "list[float]") {
              const boundsSpan = document.createElement("span");
              boundsSpan.dataset.boundsParam = dyn.paired_param;
              boundsSpan.dataset.boundsIndex = String(i);
              boundsSpan.style.cssText =
                "display:none;gap:2px;align-items:center;font-size:10px;color:#666;";
              const mkBound = (cls: string, def: string, title: string) => {
                const inp = document.createElement("input");
                inp.type = "number";
                inp.min = "0";
                inp.max = "1";
                inp.step = "0.05";
                inp.value = def;
                inp.title = title;
                inp.className = cls;
                inp.style.cssText = "width:42px;font-size:10px;padding:1px 2px;";
                inp.oninput = recompute;
                return inp;
              };
              const minLbl = document.createElement("span");
              minLbl.textContent = "≥";
              const maxLbl = document.createElement("span");
              maxLbl.textContent = "≤";
              boundsSpan.appendChild(minLbl);
              boundsSpan.appendChild(mkBound("weight-min", "0", `w${i} 下限`));
              boundsSpan.appendChild(maxLbl);
              boundsSpan.appendChild(mkBound("weight-max", "1", `w${i} 上限`));
              // Reveal immediately if simplex was already toggled on (e.g. user
              // changed the layer count while sweep mode was active).
              if (simplexRow?.dataset.simplexActive === "on") {
                boundsSpan.style.display = "inline-flex";
              }
              row.appendChild(boundsSpan);
            }
          }
          dynContainer.appendChild(l);
          dynContainer.appendChild(row);
        }
        if (simplexRow?.dataset.simplexActive === "on") renderSimplexTable();
        recompute();
      };
      numInput.oninput = renderDyn;
      renderDyn();
    }

    // Plain params. Skip the dynamic-paired one — it's collected per-input above.
    for (const p of algo.params) {
      if (dyn && p.name === dyn.paired_param) continue;
      const lbl = document.createElement("label");
      lbl.textContent = `${p.name} (${p.type}) — ${p.description}`;
      form.appendChild(lbl);
      // list[float] params without a dynamic-input pairing aren't sweepable
      // through a single cell — sweep mode is only offered for scalars.
      if (p.type === "list[float]") {
        const inp = document.createElement("input");
        inp.dataset.param = p.name;
        inp.value = String(p.default ?? "");
        form.appendChild(inp);
        continue;
      }
      renderSweepableInput(form, { "data-param": p.name }, {
        defaultValue: String(p.default ?? ""),
        step: p.step_hint != null ? String(p.step_hint) : (p.type === "int" ? "1" : undefined),
        onChange: recompute,
      });
    }

    form.appendChild(runBtn);
    recompute();
  };

  return el;
}

async function submit(algo: AlgorithmInfo, form: HTMLElement): Promise<void> {
  const inputs: Record<string, string> = {};
  const params: Record<string, unknown> = {};
  const param_ranges: Record<string, ParamRangeSpec> = {};

  form.querySelectorAll<HTMLSelectElement>("select[data-role]").forEach((sel) => {
    inputs[sel.dataset.role!] = sel.value;
  });

  // ---- Paired params (e.g. weights[i] alongside layer_i) -------------
  const pairedFixed: Record<string, number[]> = {};
  const pairedCounts: Record<string, number> = {};
  form.querySelectorAll<HTMLElement>("[data-paired-param]").forEach((w) => {
    const name = w.getAttribute("data-paired-param")!;
    const idx = parseInt(w.getAttribute("data-paired-index")!, 10);
    pairedCounts[name] = Math.max(pairedCounts[name] ?? 0, idx + 1);
    const single = w.querySelector(".sweep-single") as HTMLInputElement;
    const fixedVal = parseFloat(single.value);
    (pairedFixed[name] ||= [])[idx] = isFinite(fixedVal) ? fixedVal : 0;
  });
  for (const [name, arr] of Object.entries(pairedFixed)) {
    for (let i = 0; i < pairedCounts[name]; i++) if (arr[i] === undefined) arr[i] = 0;
    params[name] = arr;
  }

  // Simplex mode overrides list-range for the paired param.
  form.querySelectorAll<HTMLElement>('[data-simplex-active="on"]').forEach((w) => {
    const name = w.getAttribute("data-simplex-param")!;
    const T = parseInt((w.querySelector<HTMLInputElement>(".simplex-T")!).value, 10);
    const spec: SimplexSpec = { kind: "simplex", n_divisions: T };
    const bounds = readBoundsForParam(form, name);
    if (bounds) {
      spec.min = bounds.min;
      spec.max = bounds.max;
    }
    param_ranges[name] = spec;
  });

  // ---- Scalar params -------------------------------------------------
  // Two shapes carry data-param:
  //   - <span data-param=name> wrapper (renderSweepableInput): may be in range mode.
  //   - <input data-param=name>      (plain non-sweepable, e.g. list[float] textbox).
  form.querySelectorAll<HTMLElement>("[data-param]").forEach((w) => {
    const name = w.getAttribute("data-param")!;
    if (w.tagName === "INPUT") {
      params[name] = (w as HTMLInputElement).value;
      return;
    }
    const single = w.querySelector(".sweep-single") as HTMLInputElement;
    params[name] = single.value;  // backend coerces strings → numbers per ParamSpec.type
  });

  try {
    const job = await submitJob(algo.name, inputs, params, param_ranges);
    state.jobs.unshift(job);
    render();
    pollJob(job.id, { autoLoad: job.kind !== "sweep" });
    if (job.kind === "sweep" && job.child_ids) {
      // Track each child so the sweep panel can show live status.
      hydrateChildJobs(job.id);
    }
  } catch (err) {
    alert(`Submit failed: ${err}`);
  }
}

async function hydrateChildJobs(parentId: string): Promise<void> {
  try {
    const kids = await listJobChildren(parentId);
    for (const k of kids) {
      if (!state.jobs.find((x) => x.id === k.id)) state.jobs.push(k);
    }
    render();
    // Poll each non-terminal child individually.
    for (const k of kids) {
      if (k.status === "pending" || k.status === "running") {
        pollJob(k.id, { autoLoad: false });
      }
    }
  } catch (err) {
    console.error("Failed to load sweep children:", err);
  }
}

function jobsSection(): HTMLElement {
  const el = document.createElement("section");
  el.innerHTML = `<h2>Jobs</h2>`;
  // Sweep children are rendered nested under their parent — skip at top level.
  const topLevel = state.jobs.filter((j) => !j.parent_id);
  for (const j of topLevel) {
    if (j.kind === "sweep") {
      el.appendChild(sweepJobCard(j));
    } else {
      el.appendChild(singleJobCard(j));
    }
  }
  return el;
}

function singleJobCard(j: Job): HTMLElement {
  const item = document.createElement("div");
  item.className = `job ${j.status}`;
  const pct = Math.round(j.progress * 100);
  item.innerHTML = `
    <div><strong>${j.algorithm}</strong> · ${j.status} · ${pct}%</div>
    <div style="color:#888;">${(j.message ?? "").split("\n")[0]}</div>
  `;
  return item;
}

function sweepJobCard(parent: Job): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = `job ${parent.status}`;
  wrap.style.cssText = "border-left:3px solid #4a90d9;padding-left:6px;";

  const children = state.jobs
    .filter((c) => c.parent_id === parent.id)
    .sort((a, b) => (a.sample_index ?? 0) - (b.sample_index ?? 0));
  const succeeded = children.filter((c) => c.status === "succeeded").length;
  const failed = children.filter((c) => c.status === "failed").length;
  const total = parent.sample_count ?? children.length;

  const header = document.createElement("div");
  header.innerHTML = `
    <strong>${parent.algorithm} 区间扫描</strong>
    · ${succeeded}/${total} 完成${failed ? ` · <span style="color:#c00;">${failed} 失败</span>` : ""}
  `;
  wrap.appendChild(header);

  for (const c of children) {
    const row = document.createElement("div");
    row.style.cssText = "display:flex;gap:6px;align-items:center;font-size:11px;margin-top:4px;padding-left:6px;";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.disabled = c.status !== "succeeded" || !c.output_id;
    cb.checked = !!(c.output_id && state.activeLayers.has(c.output_id));
    cb.onchange = () => toggleChildLayer(c, cb.checked);
    row.appendChild(cb);

    const label = document.createElement("span");
    label.style.cssText = "flex:1;font-family:ui-monospace,monospace;color:#444;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
    label.textContent = c.sample_label || `#${c.sample_index ?? "?"}`;
    label.title = c.sample_label ?? "";
    row.appendChild(label);

    const badge = document.createElement("span");
    badge.textContent = c.status;
    badge.style.cssText = `font-size:10px;padding:1px 6px;border-radius:8px;${statusBadgeStyle(c.status)}`;
    row.appendChild(badge);

    wrap.appendChild(row);
  }
  return wrap;
}

function statusBadgeStyle(status: string): string {
  switch (status) {
    case "succeeded": return "background:#cfe9d5;color:#264;";
    case "failed":    return "background:#fbd2d2;color:#822;";
    case "running":   return "background:#fce4a4;color:#643;";
    default:          return "background:#e4e4e4;color:#555;";
  }
}

function toggleChildLayer(child: Job, on: boolean): void {
  if (!child.output_id) return;
  if (on) {
    const raster = state.rasters.find((r) => r.id === child.output_id);
    if (raster) addLayer(raster);
  } else {
    removeRasterLayer(child.output_id);
    state.activeLayers.delete(child.output_id);
  }
  render();
}

async function pollJob(jobId: string, opts: { autoLoad?: boolean } = {}): Promise<void> {
  const autoLoad = opts.autoLoad ?? true;
  while (true) {
    await new Promise((r) => setTimeout(r, 1000));
    let j: Job;
    try {
      j = await getJob(jobId);
    } catch {
      return;
    }
    const idx = state.jobs.findIndex((x) => x.id === j.id);
    if (idx >= 0) state.jobs[idx] = j;
    else state.jobs.push(j);

    // Refresh rasters when a result becomes available so child checkboxes can
    // enable themselves even if autoLoad is off.
    if (j.status === "succeeded" && j.output_id) {
      await refreshRasters();
    }
    render();

    if (j.status === "succeeded") {
      if (autoLoad) {
        const result = state.rasters.find((r) => r.id === j.output_id);
        if (result) {
          addLayer(result);
          fitToBoundsWGS84(result.bounds_wgs84);
        }
        render();
      }
      return;
    }
    if (j.status === "failed") return;
  }
}
