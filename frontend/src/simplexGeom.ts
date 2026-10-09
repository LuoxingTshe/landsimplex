/**
 * Pure geometry for the weight-space simplex view (no DOM; simplexView.ts draws).
 *
 * Weight vectors are barycentric coordinates on the (n−1)-simplex. This module
 * holds the bounds region (2-D clip for n ≤ 3, vertex/edge enumeration for any
 * n), a convex hull, the n=4 tetrahedron projection and the n≥5 merged
 * triangle projection.
 */

export type Pt = [number, number];
export type P3 = [number, number, number];
export type WeightVec = number[];

const EPS = 1e-9;

export function dot(w: number[], v: number[]): number {
  let s = 0;
  for (let i = 0; i < w.length; i++) s += w[i] * v[i];
  return s;
}

const unit = (n: number, i: number): WeightVec =>
  Array.from({ length: n }, (_, j) => (j === i ? 1 : 0));

// ---------------------------------------------------------------------------
// Bounds region
// ---------------------------------------------------------------------------

/**
 * Feasible region of the min/max bounds, as polygon vertices in weight space
 * (Sutherland–Hodgman clip of the simplex by wᵢ ≥ minᵢ and wᵢ ≤ maxᵢ; the
 * constraints are linear, so clipping in barycentric coordinates is exact).
 * n = 2 or 3 only; use feasiblePolytope for higher n.
 */
export function boundsPolygon(
  min: number[] | null, max: number[] | null, n: number,
): WeightVec[] | null {
  if (!min && !max) return null;
  let poly: WeightVec[] = Array.from({ length: n }, (_, i) => unit(n, i));
  for (let i = 0; i < n; i++) {
    const lo = min?.[i] ?? 0;
    const hi = max?.[i] ?? 1;
    if (lo > 0) poly = clip(poly, (w) => w[i] - lo, n === 2);
    if (hi < 1) poly = clip(poly, (w) => hi - w[i], n === 2);
  }
  return poly.length ? poly : null;
}

function clip(poly: WeightVec[], f: (w: WeightVec) => number, open: boolean): WeightVec[] {
  const out: WeightVec[] = [];
  const edges = open ? poly.length - 1 : poly.length;
  if (open && poly.length === 1) return f(poly[0]) >= 0 ? poly : [];
  for (let i = 0; i < edges; i++) {
    const P = poly[i];
    const Q = poly[(i + 1) % poly.length];
    const fp = f(P);
    const fq = f(Q);
    if (fp >= 0) out.push(P);
    if (fp * fq < 0) {
      const t = fp / (fp - fq);
      out.push(P.map((x, k) => x + (Q[k] - x) * t));
    }
    if (open && i === edges - 1 && fq >= 0) out.push(Q);
  }
  return out;
}

export interface Polytope {
  vertices: WeightVec[];
  edges: [number, number][];
}

/**
 * Vertices and edges of {Σw = 1, minᵢ ≤ wᵢ ≤ maxᵢ} for any n.
 *
 * Every constraint pins one coordinate, so a vertex is n−1 coordinates at a
 * bound with the remaining one taking up the slack (n·2ⁿ⁻¹ candidates, ≤ 192
 * for n = 6). Two vertices share an edge when their common active constraints
 * pin n−2 coordinates and no third vertex satisfies all of them.
 */
export function feasiblePolytope(
  min: number[] | null, max: number[] | null, n: number,
): Polytope | null {
  const lo = Array.from({ length: n }, (_, i) => min?.[i] ?? 0);
  const hi = Array.from({ length: n }, (_, i) => max?.[i] ?? 1);
  const vertices: WeightVec[] = [];
  const seen = new Set<string>();
  for (let k = 0; k < n; k++) {
    const others = [...Array(n).keys()].filter((i) => i !== k);
    for (let mask = 0; mask < 1 << others.length; mask++) {
      const w = new Array<number>(n).fill(0);
      let s = 0;
      others.forEach((i, b) => {
        w[i] = (mask >> b) & 1 ? hi[i] : lo[i];
        s += w[i];
      });
      w[k] = 1 - s;
      if (w[k] < lo[k] - EPS || w[k] > hi[k] + EPS) continue;
      const key = w.map((x) => x.toFixed(6)).join(",");
      if (seen.has(key)) continue;
      seen.add(key);
      vertices.push(w);
    }
  }
  if (!vertices.length) return null;

  // Active constraints as a bitmask: bit 2i = (wᵢ at minᵢ), bit 2i+1 = (wᵢ at maxᵢ).
  const active = vertices.map((w) => {
    let m = 0;
    w.forEach((x, i) => {
      if (Math.abs(x - lo[i]) < 1e-7) m |= 1 << (2 * i);
      if (Math.abs(x - hi[i]) < 1e-7) m |= 1 << (2 * i + 1);
    });
    return m;
  });
  const rank = (m: number) => {
    let c = 0;
    for (let i = 0; i < n; i++) if ((m >> (2 * i)) & 3) c++;
    return Math.min(c, n - 1);   // all n pinned is only n−1 independent given Σw = 1
  };
  const edges: [number, number][] = [];
  for (let a = 0; a < vertices.length; a++) {
    for (let b = a + 1; b < vertices.length; b++) {
      const common = active[a] & active[b];
      if (rank(common) !== n - 2) continue;
      const shared = active.some((m, c) => c !== a && c !== b && (m & common) === common);
      if (!shared) edges.push([a, b]);
    }
  }
  return { vertices, edges };
}

// ---------------------------------------------------------------------------
// 2-D helpers
// ---------------------------------------------------------------------------

/** Indices of the convex hull, counter-clockwise (monotone chain; collinear points dropped). */
export function convexHull(pts: Pt[]): number[] {
  const idx = pts.map((_, i) => i).sort((a, b) => pts[a][0] - pts[b][0] || pts[a][1] - pts[b][1]);
  if (idx.length < 3) return idx;
  const cross = (o: number, a: number, b: number) =>
    (pts[a][0] - pts[o][0]) * (pts[b][1] - pts[o][1]) - (pts[a][1] - pts[o][1]) * (pts[b][0] - pts[o][0]);
  const half = (order: number[]) => {
    const h: number[] = [];
    for (const i of order) {
      while (h.length >= 2 && cross(h[h.length - 2], h[h.length - 1], i) <= EPS) h.pop();
      h.push(i);
    }
    h.pop();
    return h;
  };
  return [...half(idx), ...half([...idx].reverse())];
}

function barycentric(p: Pt, a: Pt, b: Pt, c: Pt): P3 | null {
  const d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]);
  if (Math.abs(d) < EPS) return null;
  const l1 = ((b[1] - c[1]) * (p[0] - c[0]) + (c[0] - b[0]) * (p[1] - c[1])) / d;
  const l2 = ((c[1] - a[1]) * (p[0] - c[0]) + (a[0] - c[0]) * (p[1] - c[1])) / d;
  return [l1, l2, 1 - l1 - l2];
}

/** Parameters (s, u) where segments p0→p1 and q0→q1 cross, or null if parallel. */
function crossing(p0: Pt, p1: Pt, q0: Pt, q1: Pt): Pt | null {
  const rx = p1[0] - p0[0], ry = p1[1] - p0[1];
  const sx = q1[0] - q0[0], sy = q1[1] - q0[1];
  const d = rx * sy - ry * sx;
  if (Math.abs(d) < EPS) return null;
  const qx = q0[0] - p0[0], qy = q0[1] - p0[1];
  return [(qx * sy - qy * sx) / d, (qx * ry - qy * rx) / d];
}

// ---------------------------------------------------------------------------
// n = 4: regular tetrahedron, orthographic projection
// ---------------------------------------------------------------------------

const S = Math.sqrt(2 / 3);
const R = Math.sqrt(8) / 3;

/** Circumradius 1, centred at the origin, y up, z toward the viewer: A/B front, C back, D apex. */
export const TETRA: P3[] = [
  [-S, -1 / 3, R / 2],
  [S, -1 / 3, R / 2],
  [0, -1 / 3, -R],
  [0, 1, 0],
];

export const TETRA_EDGES: [number, number][] = [[0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3]];

export function toTetra(w: WeightVec): P3 {
  const q: P3 = [0, 0, 0];
  for (let i = 0; i < 4; i++) for (let k = 0; k < 3; k++) q[k] += w[i] * TETRA[i][k];
  return q;
}

/** Yaw about the vertical axis, then pitch about the horizontal (positive tips the top toward the viewer). */
export function rotate([x, y, z]: P3, yaw: number, pitch: number): P3 {
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  const x1 = x * cy + z * sy;
  const z1 = -x * sy + z * cy;
  return [x1, y * cp - z1 * sp, y * sp + z1 * cp];
}

/**
 * Tetrahedron edges hidden behind the solid, given its projected vertices and
 * their depths (larger = nearer). Silhouette is a triangle → the inner vertex's
 * three edges are hidden if it lies behind the front face; silhouette is a
 * quadrilateral → the farther of the two crossing diagonals is hidden.
 */
export function hiddenTetraEdges(p: Pt[], depth: number[]): [number, number][] {
  const hull = convexHull(p);
  if (hull.length === 3) {
    const k = [0, 1, 2, 3].find((i) => !hull.includes(i))!;
    const [a, b, c] = hull;
    const bc = barycentric(p[k], p[a], p[b], p[c]);
    if (!bc) return [];
    const faceDepth = bc[0] * depth[a] + bc[1] * depth[b] + bc[2] * depth[c];
    return depth[k] < faceDepth ? hull.map((i) => [k, i] as [number, number]) : [];
  }
  if (hull.length === 4) {
    const d1: [number, number] = [hull[0], hull[2]];
    const d2: [number, number] = [hull[1], hull[3]];
    const x = crossing(p[d1[0]], p[d1[1]], p[d2[0]], p[d2[1]]);
    if (!x) return [];
    const z1 = depth[d1[0]] + (depth[d1[1]] - depth[d1[0]]) * x[0];
    const z2 = depth[d2[0]] + (depth[d2[1]] - depth[d2[0]]) * x[1];
    return [z1 < z2 ? d1 : d2];
  }
  return [];
}

// ---------------------------------------------------------------------------
// n ≥ 5: merged projection onto a triangle
// ---------------------------------------------------------------------------

/** Barycentric position on the projection triangle: (wₐ, w_b, everything else). */
export function mergeProject(w: WeightVec, a: number, b: number): P3 {
  return [w[a], w[b], 1 - w[a] - w[b]];
}

/**
 * Lattice indices grouped by their projected position. Lattice weights are
 * multiples of 1/T, so (round(wₐ·T), round(w_b·T)) is an exact key.
 */
export function groupByProjection(
  lattice: WeightVec[], a: number, b: number, T: number,
): Map<string, number[]> {
  const groups = new Map<string, number[]>();
  lattice.forEach((w, i) => {
    const key = `${Math.round(w[a] * T)},${Math.round(w[b] * T)}`;
    const members = groups.get(key);
    if (members) members.push(i);
    else groups.set(key, [i]);
  });
  return groups;
}
