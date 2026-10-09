/**
 * Weight-space simplex view for the pixel probe.
 *
 * A pixel's WLC score is linear in the weights, score(w) = Σ wᵢ·vᵢ, so given
 * the pixel's layer values every lattice point's pass/fail is one dot product.
 * The threshold slider re-evaluates locally without another backend request.
 *
 * Geometry: n=2 → segment, n=3 → triangle, n=4 → tetrahedron (drag to rotate,
 * double-click resets). n≥5 shows the readout only.
 * The element is created once and kept alive across ui.ts re-renders.
 */
import type { ProbeResult } from "./api";
import {
  type P3, type Pt, type WeightVec,
  TETRA, TETRA_EDGES, boundsPolygon, dot, feasiblePolytope,
  hiddenTetraEdges, rotate, toTetra,
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
    else plotEl.innerHTML = `<div class="sv-note">n = ${n}: geometric view arrives in a later phase.</div>`;

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

function triangle(p: ProbeResult, scores: number[], pass: boolean[]): SVGSVGElement {
  const side = W - 2 * M;
  const h = (side * Math.sqrt(3)) / 2;
  const top = 30;
  const V: Pt[] = [[M, top + h], [M + side, top + h], [W / 2, top]];
  const svg = svgEl(W, top + h + 34);
  const toXY = (w: WeightVec): Pt => [
    w[0] * V[0][0] + w[1] * V[1][0] + w[2] * V[2][0],
    w[0] * V[0][1] + w[1] * V[1][1] + w[2] * V[2][1],
  ];

  svg.appendChild(poly(V, "sv-frame"));
  const region = boundsPolygon(p.min, p.max, 3);
  if (region) svg.appendChild(poly(region.map(toXY), "sv-bounds"));

  const s = pointSize(side / p.n_divisions);
  p.lattice.forEach((w, i) => {
    const r = latticePoint(w, scores[i], pass[i]);
    placePoint(r, toXY(w), s);
    svg.appendChild(r);
  });

  vertexLabel(svg, V[2][0], top - 18, "middle", p, 2);
  vertexLabel(svg, V[0][0], top + h + 22, "start", p, 0);
  vertexLabel(svg, V[1][0], top + h + 22, "end", p, 1);
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
  vertexLabel(svg, M, y + 24, "start", p, 0);
  vertexLabel(svg, W - M, y + 24, "end", p, 1);
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

/** Vertex label: bold letter + truncated layer name, e.g. "A slope_reclassify…". */
function vertexLabel(
  svg: SVGSVGElement, x: number, y: number, anchor: "start" | "middle" | "end",
  p: ProbeResult, index: number,
): void {
  const t = document.createElementNS(SVG_NS, "text");
  t.setAttribute("x", String(x));
  t.setAttribute("y", String(y));
  t.setAttribute("text-anchor", anchor);
  const letter = document.createElementNS(SVG_NS, "tspan");
  letter.setAttribute("class", "sv-letter");
  letter.textContent = LETTERS[index];
  const name = document.createElementNS(SVG_NS, "tspan");
  name.setAttribute("class", "sv-name");
  name.setAttribute("dx", "4");
  const full = p.layers[index].name;
  name.textContent = full.length > 22 ? `${full.slice(0, 21)}…` : full;
  t.append(letter, name);
  svg.appendChild(t);
}
