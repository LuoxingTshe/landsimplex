/**
 * Weight-space simplex view for the pixel probe.
 *
 * A pixel's WLC score is linear in the weights, score(w) = Σ wᵢ·vᵢ, so given
 * the pixel's layer values every lattice point's pass/fail is one dot product.
 * The threshold slider re-evaluates locally without another backend request.
 *
 * Geometry: n=2 → segment, n=3 → triangle, n=4 → tetrahedron (drag to rotate,
 * double-click resets). n≥5 → two chosen weights plus "the rest" projected onto
 * a triangle, coincident lattice points aggregated into cells, and parallel
 * coordinates below; clicking a cell highlights its weight vectors.
 * The element is created once and kept alive across ui.ts re-renders.
 */
import type { ProbeResult } from "./api";
import {
  type P3, type Pt, type WeightVec,
  TETRA, TETRA_EDGES, boundsPolygon, convexHull, dot, feasiblePolytope,
  groupByProjection, hiddenTetraEdges, mergeProject, rotate, toTetra,
} from "./simplexGeom";

const SVG_NS = "http://www.w3.org/2000/svg";
const W = 304;            // sidebar content width (352 − 2×24 padding)
const M = 24;             // horizontal margin inside the plot
const LETTERS = "ABCDEF";
const DEFAULT_THRESHOLD = 0.7;
const HINT_NO_TARGET = "Select a result with ◎, then click the map.";
const HINT_TARGET = "Click the map to lock a pixel. Esc clears.";
const ORBIT_HOME: Orbit = { yaw: 0.45, pitch: 0.3 };
const DRAG_RAD_PER_PX = 0.01;

interface Orbit { yaw: number; pitch: number }

/** n ≥ 5 projection: weights a and b get their own vertices; `cell` is the selected group key. */
interface Projection { a: number; b: number; cell: string | null }
const PROJECTION_HOME: Projection = { a: 0, b: 1, cell: null };

export interface SimplexView {
  el: HTMLElement;
  setTarget(name: string | null): void;
  setStatus(text: string, isError?: boolean): void;
  show(probe: ProbeResult): void;
  clear(): void;
}

export function createSimplexView(): SimplexView {
  const el = document.createElement("section");
  el.className = "simplex-view";
  el.innerHTML = `
    <h2>Weight space <span class="sv-target"></span></h2>
    <div class="sv-status"></div>
    <div class="sv-body">
      <div class="sv-plot"></div>
      <div class="probe-readout"></div>
      <label class="sv-threshold">Threshold
        <input type="range" min="0" max="1" step="0.01" />
        <span class="val"></span>
      </label>
    </div>`;
  const targetEl = el.querySelector<HTMLElement>(".sv-target")!;
  const statusEl = el.querySelector<HTMLElement>(".sv-status")!;
  const bodyEl = el.querySelector<HTMLElement>(".sv-body")!;
  const plotEl = el.querySelector<HTMLElement>(".sv-plot")!;
  const readoutEl = el.querySelector<HTMLElement>(".probe-readout")!;
  const slider = el.querySelector<HTMLInputElement>(".sv-threshold input")!;
  const sliderVal = el.querySelector<HTMLElement>(".sv-threshold .val")!;

  let probe: ProbeResult | null = null;
  let threshold = DEFAULT_THRESHOLD;
  let jobId: string | null = null;   // threshold resets only when the job changes
  let hasTarget = false;
  const orbit: Orbit = { ...ORBIT_HOME };   // tetrahedron view angle, kept across pixels
  const proj: Projection = { ...PROJECTION_HOME };   // n≥5 axes + selection, kept across pixels

  const setStatus = (text: string, isError = false) => {
    statusEl.textContent = text;
    statusEl.classList.toggle("err", isError);
  };

  slider.oninput = () => {
    threshold = Number(slider.value);
    draw();
  };

  function draw(): void {
    bodyEl.style.display = probe ? "" : "none";
    if (!probe) return;
    slider.value = String(threshold);
    sliderVal.textContent = threshold.toFixed(2);

    const n = probe.layers.length;
    const v = probe.values.map((x) => x ?? NaN);
    const scores = probe.lattice.map((w) => dot(w, v));
    const pass = scores.map((s) => s > threshold);

    plotEl.replaceChildren();
    if (n === 4) plotEl.append(tetrahedron(probe, scores, pass, orbit), orbitHint());
    else if (n === 3) plotEl.appendChild(triangle(probe, scores, pass));
    else if (n === 2) plotEl.appendChild(segment(probe, scores, pass));
    else plotEl.appendChild(projectionView(probe, scores, pass, threshold, proj, draw));

    readoutEl.innerHTML = readout(probe, scores, pass, threshold);
  }

  return {
    el,
    setStatus,
    setTarget(name) {
      hasTarget = name !== null;
      targetEl.textContent = name ?? "";
      targetEl.title = name ?? "";
      probe = null;
      draw();
      setStatus(hasTarget ? HINT_TARGET : HINT_NO_TARGET);
    },
    show(p) {
      if (p.job_id !== jobId) {
        jobId = p.job_id;
        threshold = p.threshold ?? DEFAULT_THRESHOLD;
        Object.assign(proj, PROJECTION_HOME);
      }
      probe = p;
      setStatus(`Pixel row ${p.pixel.row}, col ${p.pixel.col}`);
      draw();
    },
    clear() {
      probe = null;
      draw();
      setStatus(hasTarget ? HINT_TARGET : HINT_NO_TARGET);
    },
  };
}

// ---------------------------------------------------------------------------
// Readout
// ---------------------------------------------------------------------------

function readout(p: ProbeResult, scores: number[], pass: boolean[], t: number): string {
  const rows = p.layers.map((l, i) => `
    <tr><td class="k">${LETTERS[i]}</td><td class="name" title="${l.name}">${l.name}</td>
    <td class="num">${p.values[i] == null ? "—" : p.values[i]!.toFixed(3)}</td></tr>`).join("");
  const table = `<table class="probe-tbl">${rows}</table>`;

  if (!p.valid) {
    const missing = p.values.map((x, i) => (x == null ? LETTERS[i] : "")).join("");
    return `<div class="probe-pct">—</div>
      <div class="probe-sub">No data at this pixel (layer ${missing}).</div>${table}`;
  }

  const k = pass.filter(Boolean).length;
  const N = pass.length;
  const rate = k / N;
  let mapLine = "";
  if (p.mode === "threshold_probability" && p.output_value != null) {
    const same = p.threshold != null && Math.abs(t - p.threshold) < 1e-9;
    mapLine = same
      ? `Map value ${p.output_value.toFixed(3)}${Math.abs(p.output_value - rate) < 1e-4 ? " ✓" : ""}`
      : `≠ map value ${p.output_value.toFixed(3)} (job threshold ${p.threshold!.toFixed(2)})`;
  } else if (p.mode === "overlay_sweep" && p.output_value != null) {
    mapLine = `This sample's score ${p.output_value.toFixed(3)}`;
  }
  const lo = Math.min(...scores);
  const hi = Math.max(...scores);
  return `
    <div class="probe-pct">${(rate * 100).toFixed(1)}<small>%</small></div>
    <div class="probe-sub">${k} / ${N} weight vectors pass · score &gt; ${t.toFixed(2)}</div>
    ${mapLine ? `<div class="probe-sub">${mapLine}</div>` : ""}
    ${table}
    <div class="probe-sub">Score range ${lo.toFixed(3)} – ${hi.toFixed(3)} · T = ${p.n_divisions}</div>`;
}

// ---------------------------------------------------------------------------
// Geometry
// ---------------------------------------------------------------------------

/** Equilateral triangle filling the plot width; toXY maps barycentric (w₀, w₁, w₂) to it. */
function triangleFrame() {
  const side = W - 2 * M;
  const h = (side * Math.sqrt(3)) / 2;
  const top = 30;
  const V: Pt[] = [[M, top + h], [M + side, top + h], [W / 2, top]];
  const svg = svgEl(W, top + h + 34);
  const toXY = (w: number[]): Pt => [
    w[0] * V[0][0] + w[1] * V[1][0] + w[2] * V[2][0],
    w[0] * V[0][1] + w[1] * V[1][1] + w[2] * V[2][1],
  ];
  svg.appendChild(poly(V, "sv-frame"));
  return { svg, side, h, top, V, toXY };
}

function triangle(p: ProbeResult, scores: number[], pass: boolean[]): SVGSVGElement {
  const { svg, side, h, top, V, toXY } = triangleFrame();
  const region = boundsPolygon(p.min, p.max, 3);
  if (region) svg.appendChild(poly(region.map(toXY), "sv-bounds"));

  const s = pointSize(side / p.n_divisions);
  p.lattice.forEach((w, i) => {
    const r = latticePoint(w, scores[i], pass[i]);
    placePoint(r, toXY(w), s);
    svg.appendChild(r);
  });

  vertexLabel(svg, V[2][0], top - 18, "middle", LETTERS[2], p.layers[2].name);
  vertexLabel(svg, V[0][0], top + h + 22, "start", LETTERS[0], p.layers[0].name);
  vertexLabel(svg, V[1][0], top + h + 22, "end", LETTERS[1], p.layers[1].name);
  return svg;
}

function segment(p: ProbeResult, scores: number[], pass: boolean[]): SVGSVGElement {
  const y = 22;
  const svg = svgEl(W, y + 40);
  const toXY = (w: WeightVec): Pt => [M + w[1] * (W - 2 * M), y];
  svg.appendChild(line([M, y], [W - M, y], "sv-frame"));
  const region = boundsPolygon(p.min, p.max, 2);
  if (region) {
    const [a, b] = region.map(toXY);
    svg.appendChild(line([a[0], y - 8], [b[0], y - 8], "sv-bounds"));
  }
  const s = pointSize((W - 2 * M) / p.n_divisions);
  p.lattice.forEach((w, i) => {
    const r = latticePoint(w, scores[i], pass[i]);
    placePoint(r, toXY(w), s);
    svg.appendChild(r);
  });
  vertexLabel(svg, M, y + 24, "start", LETTERS[0], p.layers[0].name);
  vertexLabel(svg, W - M, y + 24, "end", LETTERS[1], p.layers[1].name);
  return svg;
}

/**
 * n = 4: orthographic tetrahedron. Elements are built once; dragging only
 * re-projects them and re-appends the lattice points far-to-near, so large
 * lattices (threshold_probability is not capped at 200) stay smooth.
 */
function tetrahedron(
  p: ProbeResult, scores: number[], pass: boolean[], orbit: Orbit,
): SVGSVGElement {
  const scale = (W - 2 * M) / 2;     // circumradius 1 → fits the plot width
  const cx = W / 2;
  const cy = 22 + scale;
  const svg = svgEl(W, cy + scale + 22);
  svg.classList.add("sv-orbit");
  const screen = (q: P3): Pt => [cx + scale * q[0], cy - scale * q[1]];
  const project = (w: WeightVec): P3 => rotate(toTetra(w), orbit.yaw, orbit.pitch);

  const edgeEls = TETRA_EDGES.map(() => svg.appendChild(line([0, 0], [0, 0], "sv-frame")));
  const region = p.min || p.max ? feasiblePolytope(p.min, p.max, 4) : null;
  const regionEls = (region?.edges ?? []).map(() => svg.appendChild(line([0, 0], [0, 0], "sv-bounds")));
  const pointEls = p.lattice.map((w, i) => latticePoint(w, scores[i], pass[i]));
  const s = pointSize((Math.sqrt(8 / 3) * scale) / p.n_divisions);   // edge length / T
  const labelEls = TETRA.map((_, i) => {
    const t = document.createElementNS(SVG_NS, "text");
    t.setAttribute("class", "sv-letter");
    t.setAttribute("text-anchor", "middle");
    t.setAttribute("dominant-baseline", "central");
    t.textContent = LETTERS[i];
    return t;
  });

  const update = () => {
    const V = TETRA.map((q) => rotate(q, orbit.yaw, orbit.pitch));
    const VP = V.map(screen);
    const hidden = new Set(hiddenTetraEdges(VP, V.map((q) => q[2])).map(([a, b]) => `${Math.min(a, b)}-${Math.max(a, b)}`));
    TETRA_EDGES.forEach(([a, b], k) => {
      setLine(edgeEls[k], VP[a], VP[b]);
      edgeEls[k].setAttribute("class", hidden.has(`${a}-${b}`) ? "sv-frame back" : "sv-frame");
    });
    region?.edges.forEach(([a, b], k) => {
      setLine(regionEls[k], screen(project(region.vertices[a])), screen(project(region.vertices[b])));
    });
    const q = p.lattice.map(project);
    const order = q.map((_, i) => i).sort((a, b) => q[a][2] - q[b][2]);
    for (const i of order) {
      const near = (q[i][2] + 1) / 2;   // 0 = far, 1 = near
      placePoint(pointEls[i], screen(q[i]), s * (0.7 + 0.3 * near));
      pointEls[i].setAttribute("opacity", (0.4 + 0.6 * near).toFixed(2));
      svg.appendChild(pointEls[i]);
    }
    VP.forEach(([x, y], i) => {
      const dx = x - cx, dy = y - cy;
      const d = Math.hypot(dx, dy) || 1;
      labelEls[i].setAttribute("x", (x + (dx / d) * 12).toFixed(1));
      labelEls[i].setAttribute("y", (y + (dy / d) * 12).toFixed(1));
      svg.appendChild(labelEls[i]);
    });
  };
  update();

  let drag: Pt | null = null;
  let frame = 0;
  svg.addEventListener("pointerdown", (e) => {
    drag = [e.clientX, e.clientY];
    svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    orbit.yaw += (e.clientX - drag[0]) * DRAG_RAD_PER_PX;
    orbit.pitch = Math.max(-1.5, Math.min(1.5, orbit.pitch + (e.clientY - drag[1]) * DRAG_RAD_PER_PX));
    drag = [e.clientX, e.clientY];
    if (!frame) frame = requestAnimationFrame(() => { frame = 0; update(); });
  });
  const end = () => { drag = null; };
  svg.addEventListener("pointerup", end);
  svg.addEventListener("pointercancel", end);
  svg.addEventListener("dblclick", () => {
    Object.assign(orbit, ORBIT_HOME);
    update();
  });
  return svg;
}

/**
 * n ≥ 5: weights a and b keep their own vertices, the top vertex is the sum of
 * the rest. Lattice points that land on the same spot form one cell whose red
 * fill height is the share that passes. Parallel coordinates below show every
 * weight vector; clicking a cell highlights its members there.
 */
function projectionView(
  p: ProbeResult, scores: number[], pass: boolean[], t: number,
  proj: Projection, redraw: () => void,
): HTMLElement {
  const n = p.layers.length;
  const T = p.n_divisions;
  const { a, b } = proj;
  const rest = [...Array(n).keys()].filter((i) => i !== a && i !== b);
  const restLabel = rest.map((i) => LETTERS[i]).join("+");
  const wrap = document.createElement("div");

  // Axis picker: two selects; picking the other one's letter swaps them.
  const picker = document.createElement("div");
  picker.className = "sv-axes";
  const pick = (label: string, value: number, set: (i: number) => void) => {
    const sel = document.createElement("select");
    sel.innerHTML = p.layers.map((l, i) =>
      `<option value="${i}" title="${l.name}"${i === value ? " selected" : ""}>${LETTERS[i]}</option>`).join("");
    sel.onchange = () => { set(Number(sel.value)); proj.cell = null; redraw(); };
    const lab = document.createElement("label");
    lab.append(label, sel);
    return lab;
  };
  const restEl = document.createElement("span");
  restEl.textContent = `Top = ${restLabel}`;
  picker.append(
    pick("Left", a, (i) => { if (i === proj.b) proj.b = proj.a; proj.a = i; }),
    pick("Right", b, (i) => { if (i === proj.a) proj.a = proj.b; proj.b = i; }),
    restEl,
  );

  // Projected triangle with aggregated cells.
  const { svg: tri, side, h, top, V, toXY } = triangleFrame();
  if (p.min || p.max) {
    const region = feasiblePolytope(p.min, p.max, n);
    if (region) {
      const pts = region.vertices.map((w) => toXY(mergeProject(w, a, b)));
      const hull = convexHull(pts).map((i) => pts[i]);
      if (hull.length >= 3) tri.appendChild(poly(hull, "sv-bounds"));
      else if (hull.length === 2) tri.appendChild(line(hull[0], hull[1], "sv-bounds"));
    }
  }
  const groups = groupByProjection(p.lattice, a, b, T);
  const s = Math.max(4, Math.min(14, (side / T) * 0.6));
  const cellEls = new Map<string, SVGRectElement>();
  for (const [key, members] of groups) {
    const w = p.lattice[members[0]];
    const [x, y] = toXY(mergeProject(w, a, b));
    const k = members.filter((i) => pass[i]).length;
    const cell = document.createElementNS(SVG_NS, "rect");
    placePoint(cell, [x, y], s);
    cell.setAttribute("class", "sv-cell");
    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = `${cellLabel(w, a, b, restLabel)} — ${k} / ${members.length} pass`;
    cell.appendChild(title);
    cell.addEventListener("click", (e) => {
      e.stopPropagation();
      proj.cell = proj.cell === key ? null : key;
      applySelection();
    });
    tri.appendChild(cell);
    cellEls.set(key, cell);
    if (k > 0) {
      const fh = (s * k) / members.length;
      const fill = document.createElementNS(SVG_NS, "rect");
      fill.setAttribute("x", (x - s / 2).toFixed(2));
      fill.setAttribute("y", (y + s / 2 - fh).toFixed(2));
      fill.setAttribute("width", s.toFixed(2));
      fill.setAttribute("height", fh.toFixed(2));
      fill.setAttribute("class", "sv-cell-fill");
      tri.appendChild(fill);
    }
  }
  tri.addEventListener("click", () => {
    if (proj.cell === null) return;
    proj.cell = null;
    applySelection();
  });
  vertexLabel(tri, V[2][0], top - 18, "middle", restLabel, "");
  vertexLabel(tri, V[0][0], top + h + 22, "start", LETTERS[a], p.layers[a].name);
  vertexLabel(tri, V[1][0], top + h + 22, "end", LETTERS[b], p.layers[b].name);

  const caption = document.createElement("div");
  caption.className = "probe-sub";

  const pc = parallelCoords(p, scores, pass, t);

  function applySelection(): void {
    const members = proj.cell ? groups.get(proj.cell) ?? [] : [];
    if (proj.cell && !members.length) proj.cell = null;
    for (const [key, el] of cellEls) el.classList.toggle("sel", key === proj.cell);
    pc.svg.classList.toggle("has-sel", proj.cell !== null);
    const on = new Set(members);
    pc.lines.forEach((el, i) => el.classList.toggle("on", on.has(i)));
    for (const i of members) pc.svg.insertBefore(pc.lines[i], pc.overlay);   // raise above the rest
    if (proj.cell) {
      const k = members.filter((i) => pass[i]).length;
      caption.textContent = `${cellLabel(p.lattice[members[0]], a, b, restLabel)} — ${k} / ${members.length} pass`;
    } else {
      caption.textContent = "Click a cell to trace its weight vectors below.";
    }
  }
  applySelection();

  wrap.append(picker, tri, caption, pc.svg);
  return wrap;
}

function cellLabel(w: WeightVec, a: number, b: number, restLabel: string): string {
  return `${LETTERS[a]} ${w[a].toFixed(2)} · ${LETTERS[b]} ${w[b].toFixed(2)} · ${restLabel} ${(1 - w[a] - w[b]).toFixed(2)}`;
}

/**
 * One vertical axis per weight (0 at the bottom, 1 at the top) plus a score
 * axis, one polyline per lattice weight vector. Failing lines are drawn first,
 * passing ones on top. `overlay` holds labels and the threshold tick, so
 * raised lines are inserted before it.
 */
function parallelCoords(p: ProbeResult, scores: number[], pass: boolean[], t: number) {
  const n = p.layers.length;
  const withScore = p.valid;
  const axes = withScore ? n + 1 : n;
  const top = 8;
  const H = 140;
  const svg = svgEl(W, top + H + 24);
  const ax = (i: number) => M + (i * (W - 2 * M)) / (axes - 1);
  const wy = (x: number) => top + H * (1 - x);
  const finite = scores.filter((x) => Number.isFinite(x));
  const lo = Math.min(0, ...finite);
  const hi = Math.max(1, ...finite);
  const sy = (x: number) => wy((x - lo) / (hi - lo));

  for (let i = 0; i < axes; i++) svg.appendChild(line([ax(i), top], [ax(i), top + H], "sv-axis"));

  const order = p.lattice.map((_, i) => i).sort((x, y) => Number(pass[x]) - Number(pass[y]));
  const lines: SVGPolylineElement[] = new Array(p.lattice.length);
  for (const i of order) {
    const w = p.lattice[i];
    const pts: Pt[] = w.map((x, j) => [ax(j), wy(x)]);
    if (withScore) pts.push([ax(n), sy(scores[i])]);
    const el = document.createElementNS(SVG_NS, "polyline");
    el.setAttribute("points", pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" "));
    el.setAttribute("class", `sv-pc ${pass[i] ? "pass" : "fail"}`);
    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = `${w.map((x, j) => `${LETTERS[j]} ${x.toFixed(2)}`).join(" · ")} → ${Number.isNaN(scores[i]) ? "—" : scores[i].toFixed(3)}`;
    el.appendChild(title);
    svg.appendChild(el);
    lines[i] = el;
  }

  const overlay = document.createElementNS(SVG_NS, "g");
  const text = (x: number, y: number, s: string, cls: string, anchor = "middle") => {
    const e = document.createElementNS(SVG_NS, "text");
    e.setAttribute("x", x.toFixed(1));
    e.setAttribute("y", y.toFixed(1));
    e.setAttribute("text-anchor", anchor);
    e.setAttribute("class", cls);
    e.textContent = s;
    overlay.appendChild(e);
  };
  for (let j = 0; j < n; j++) text(ax(j), top + H + 18, LETTERS[j], "sv-letter");
  text(ax(0) - 6, wy(1) + 4, "1", "sv-name", "end");
  text(ax(0) - 6, wy(0) + 4, "0", "sv-name", "end");
  if (withScore) {
    text(ax(n), top + H + 18, "Score", "sv-name");
    overlay.appendChild(line([ax(n) - 5, sy(t)], [ax(n) + 5, sy(t)], "sv-thr"));
  }
  svg.appendChild(overlay);
  return { svg, lines, overlay };
}

function orbitHint(): HTMLElement {
  const d = document.createElement("div");
  d.className = "probe-sub";
  d.textContent = "Drag to rotate · double-click resets";
  return d;
}

function pointSize(spacing: number): number {
  return Math.max(3, Math.min(10, spacing * 0.55));
}

function latticePoint(w: WeightVec, score: number, pass: boolean): SVGRectElement {
  const r = document.createElementNS(SVG_NS, "rect");
  r.setAttribute("class", `sv-pt ${pass ? "pass" : "fail"}`);
  const title = document.createElementNS(SVG_NS, "title");
  const ws = w.map((x, j) => `${LETTERS[j]} ${x.toFixed(2)}`).join(" · ");
  title.textContent = `${ws} → ${Number.isNaN(score) ? "—" : score.toFixed(3)}`;
  r.appendChild(title);
  return r;
}

function placePoint(r: SVGRectElement, [x, y]: Pt, s: number): void {
  r.setAttribute("x", (x - s / 2).toFixed(2));
  r.setAttribute("y", (y - s / 2).toFixed(2));
  r.setAttribute("width", s.toFixed(2));
  r.setAttribute("height", s.toFixed(2));
}

// ---------------------------------------------------------------------------
// SVG helpers
// ---------------------------------------------------------------------------

function svgEl(w: number, h: number): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h.toFixed(1)}`);
  return svg;
}

function poly(pts: Pt[], cls: string): SVGPolygonElement {
  const e = document.createElementNS(SVG_NS, "polygon");
  setPoints(e, pts);
  e.setAttribute("class", cls);
  return e;
}

function setPoints(e: SVGPolygonElement, pts: Pt[]): void {
  e.setAttribute("points", pts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" "));
}

function line(a: Pt, b: Pt, cls: string): SVGLineElement {
  const e = document.createElementNS(SVG_NS, "line");
  setLine(e, a, b);
  e.setAttribute("class", cls);
  return e;
}

function setLine(e: SVGLineElement, a: Pt, b: Pt): void {
  e.setAttribute("x1", a[0].toFixed(2));
  e.setAttribute("y1", a[1].toFixed(2));
  e.setAttribute("x2", b[0].toFixed(2));
  e.setAttribute("y2", b[1].toFixed(2));
}

/** Vertex label: bold letter(s) + truncated layer name, e.g. "A slope_reclassify…". */
function vertexLabel(
  svg: SVGSVGElement, x: number, y: number, anchor: "start" | "middle" | "end",
  letters: string, full: string,
): void {
  const t = document.createElementNS(SVG_NS, "text");
  t.setAttribute("x", String(x));
  t.setAttribute("y", String(y));
  t.setAttribute("text-anchor", anchor);
  const letter = document.createElementNS(SVG_NS, "tspan");
  letter.setAttribute("class", "sv-letter");
  letter.textContent = letters;
  t.appendChild(letter);
  if (full) {
    const name = document.createElementNS(SVG_NS, "tspan");
    name.setAttribute("class", "sv-name");
    name.setAttribute("dx", "4");
    name.textContent = full.length > 22 ? `${full.slice(0, 21)}…` : full;
    t.appendChild(name);
  }
  svg.appendChild(t);
}
