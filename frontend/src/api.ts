/**
 * Backend API client.
 */

const API_BASE = "http://127.0.0.1:8765";

export interface Raster {
  id: string;
  name: string;
  cog_path: string;
  crs: string;
  bounds: [number, number, number, number];
  bounds_wgs84: [number, number, number, number];
  resolution: [number, number];
  width: number;
  height: number;
  dtype: string;
  nodata: number | null;
  kind: "source" | "result";
  semantic: string;
  created_at: string;
  // satellite band metadata (null for plain GeoTIFF uploads)
  scene_id?: string | null;
  sensor?: string | null;
  band_id?: string | null;
  auto_role?: string | null;
  role?: string | null;
  resolution_m?: number | null;
  stats_min?: number | null;
  stats_max?: number | null;
}

export interface Scene {
  scene_id: string;
  sensor: string;
  acquired_at: string | null;
  raw_name: string;
  created_at: string;
  bands: Raster[];
  failed_bands: { band_id: string; error: string }[];
}

export interface DynamicInputSpec {
  role_prefix: string;
  description: string;
  semantic: string;
  min_count: number;
  max_count: number;
  paired_param: string | null;
}

export interface ParamInfo {
  name: string;
  type: string;
  default: unknown;
  description: string;
  // Sweep metadata (see backend/app/algorithms/registry.py:ParamSpec).
  sweepable?: boolean;
  min?: number | null;
  max?: number | null;
  step_hint?: number | null;
}

export interface AlgorithmInfo {
  name: string;
  display: string;
  category: string;
  description: string;
  inputs: { role: string; description: string; semantic: string }[];
  params: ParamInfo[];
  output_name: string;
  dynamic_inputs: DynamicInputSpec | null;
  is_composite: boolean;
}

// Parameter sweep wire format. See backend/app/jobs/sweep.py.
export type SimplexSpec = {
  kind: "simplex";
  n_divisions: number;
  // Optional per-component bounds (length = n_components). Lattice points
  // outside any [min, max] are dropped before child jobs are enqueued.
  min?: number[];
  max?: number[];
};
export type ParamRangeSpec = SimplexSpec;

export interface PreviewResponse {
  sample_count: number;
  max_samples: number;
}

export async function previewJob(
  algorithm: string,
  params: Record<string, unknown>,
  param_ranges?: Record<string, ParamRangeSpec>,
): Promise<PreviewResponse> {
  const body: Record<string, unknown> = { algorithm, params };
  if (param_ranges && Object.keys(param_ranges).length > 0) {
    body.param_ranges = param_ranges;
  }
  const r = await fetch(`${API_BASE}/jobs/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export interface Job {
  id: string;
  algorithm: string;
  params: Record<string, unknown>;
  inputs: Record<string, string>;
  status: "pending" | "running" | "succeeded" | "failed";
  progress: number;
  message: string | null;
  output_id: string | null;
  created_at: string;
  updated_at: string;
  // Sweep fields (absent / null on plain single jobs).
  kind?: "single" | "sweep" | "sweep_child";
  parent_id?: string | null;
  child_ids?: string[];
  sample_count?: number | null;
  sample_label?: string | null;
  sample_index?: number | null;
  param_ranges?: Record<string, ParamRangeSpec> | null;
}

export async function listRasters(): Promise<Raster[]> {
  const r = await fetch(`${API_BASE}/rasters`);
  return r.json();
}

export async function uploadRaster(file: File): Promise<Raster> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${API_BASE}/rasters/upload`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function listAlgorithms(): Promise<AlgorithmInfo[]> {
  const r = await fetch(`${API_BASE}/algorithms`);
  return r.json();
}

export async function submitJob(
  algorithm: string,
  inputs: Record<string, string>,
  params: Record<string, unknown> = {},
  param_ranges?: Record<string, ParamRangeSpec>,
): Promise<Job> {
  const body: Record<string, unknown> = { algorithm, inputs, params };
  if (param_ranges && Object.keys(param_ranges).length > 0) {
    body.param_ranges = param_ranges;
  }
  const r = await fetch(`${API_BASE}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function getJob(jobId: string): Promise<Job> {
  const r = await fetch(`${API_BASE}/jobs/${jobId}`);
  return r.json();
}

export async function listJobChildren(parentId: string): Promise<Job[]> {
  const r = await fetch(`${API_BASE}/jobs/${parentId}/children`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export function tileUrl(rasterId: string, rescale?: [number, number], colormap = "viridis"): string {
  const params = new URLSearchParams({ colormap });
  if (rescale) params.set("rescale", `${rescale[0]},${rescale[1]}`);
  return `${API_BASE}/tiles/${rasterId}/{z}/{x}/{y}.png?${params.toString()}`;
}

export async function uploadScene(file: File): Promise<Scene> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${API_BASE}/scenes/upload`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function listScenes(): Promise<Scene[]> {
  const r = await fetch(`${API_BASE}/scenes`);
  return r.json();
}

export async function getScene(sceneId: string): Promise<Scene> {
  const r = await fetch(`${API_BASE}/scenes/${sceneId}`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function setBandRole(rasterId: string, role: string): Promise<void> {
  const r = await fetch(`${API_BASE}/scenes/bands/${rasterId}/role`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
  if (!r.ok) throw new Error(await r.text());
}

export interface RasterRef {
  job_id: string;
  algorithm: string;
  status: string;
  role: string;
}

/** Thrown by deleteRaster when the backend answers 409. */
export class RasterInUseError extends Error {
  constructor(
    public code: "in_use" | "referenced",
    message: string,
    public jobs: RasterRef[],
  ) {
    super(message);
  }
}

export async function deleteRaster(rasterId: string, force = false): Promise<void> {
  const r = await fetch(`${API_BASE}/rasters/${rasterId}${force ? "?force=true" : ""}`, {
    method: "DELETE",
  });
  if (r.status === 409) {
    const d = (await r.json()).detail;
    throw new RasterInUseError(d.code, d.message, d.jobs ?? []);
  }
  if (!r.ok) throw new Error(await r.text());
}

/** Stop the backend, then the Vite dev server. Either may already be gone. */
export async function shutdownApp(): Promise<void> {
  await fetch(`${API_BASE}/shutdown`, { method: "POST" }).catch(() => undefined);
  await fetch("/__shutdown", { method: "POST" }).catch(() => undefined);
}
