# LandSimplex — 开发指南

## 环境与启动

- **Conda env:** `landplan`（Python 3.11, GDAL 3.8, rasterio 1.3, FastAPI）
- **后端：** `just backend`（等同于 `cd backend && conda run -n landplan uvicorn app.main:app --reload --host 127.0.0.1 --port 8765`）
  - **必须从项目根目录运行**，justfile 在根目录。
- **前端：** `just frontend`
- **访问：** http://localhost:5173
- **示例数据：** `data/samples/`（DEM.tif、TestDoc.tif，已 gitignore）；运行时数据仍在 `backend/data/`。
- **Conda env 名保持 `landplan`**（项目已更名为 LandSimplex，env 沿用旧名以免重建环境）。
- **首次安装（macOS arm64）：** `brew install just && brew install --cask miniforge`，然后 `just setup`（建 conda 环境 + `npm install`）。测试依赖需另装：`conda run -n landplan pip install pytest`（pytest 不在 `environment.yml` 中）。
- **nimplex：** `backend/vendor/nimplex.so` 已是 arm64 预编译版，开箱可用，无需装 Nim。

## 目录结构

```
backend/app/
  api/
    rasters.py   — GET /rasters, GET /rasters/{id}, POST /rasters/upload, DELETE /rasters/{id}
    tiles.py     — XYZ 瓦片（rio-tiler 读 COG）
    jobs.py      — 算法 + 任务 CRUD
    scenes.py    — 卫星数据包摄取 API
  pipeline/
    cogify.py    — GeoTIFF → COG；大文件（≥200 MiB）走三步路径
    alignment.py — AlignmentSpec 数据类（冻结网格：CRS/分辨率/原点/宽高）
    normalize.py — N 个 COG → reproject → AlignedStack；大文件用 memmap
    stack.py     — AlignedStack: spec + dict[role→ndarray] + mask + _tmpdir
    writer.py    — AlignedStack → COG；大输出同样走三步路径
    ingest.py    — 压缩包解析 + 波段发现（Sentinel-2 zip, Landsat tar.gz）
  algorithms/
    registry.py              — @register_algorithm 装饰器，BaseAlgorithm ABC
    composite.py             — CompositeAlgorithm 基类（不自注册）
    builtin/slope.py         — Horn 3×3；大文件走分块路径
    builtin/aspect.py        — gdal.DEMProcessing 坡向（罗盘0-360°）；分块+1像素晕圈
    builtin/weighted_overlay.py — 加权求和；大文件走分块路径
    builtin/landscape_sun_score.py — 复合：坡向+坡度→重分类→加权叠加，输出0-1
  jobs/runner.py   — ProcessPoolExecutor（max_workers=2）
  storage/
    local.py     — SQLite 目录（rasters, jobs, scenes 表）
    migrations.py — 增量 schema 迁移
  config.py      — 路径：COG_DIR, RESULT_DIR, UPLOAD_TMP, DB_PATH, BUNDLE_STAGING_DIR

frontend/src/
  main.ts   — 入口：initMap() + initPanel()
  api.ts    — 类型化 fetch 封装
  map.ts    — OpenLayers 地图
  ui.ts     — 侧边栏（原生 DOM，无框架）
```

## 关键管道流程

1. **上传：** `rasters.py` 分块写入 → `cogify()` → SQLite 注册
2. **cogify：** 小文件单步；大文件三步（见下文大文件部分）
3. **算法任务：** `runner._run_job` → `normalize(source_paths)` → `algo.run(stack)` → `write_stack_array(...)`
4. **normalize：** 将每个 COG 重投影到公共 AlignmentSpec；大波段用 memmap
5. **algo.run：** slope/aspect/weighted_overlay 自动检测 memmap 并切换分块路径
6. **write_stack_array：** 窗口写入 → COG（大输出走三步路径）
7. **删除：** `DELETE /rasters/{id}` 移除 SQLite 记录 + 删除磁盘上的 COG 文件
8. **LOCAL_CS 修复：** 源 CRS 为 LOCAL_CS 时，`_repair_local_cs()` 通过 pyproj 按名称解析，写临时副本后再 cogify；无法解析时返回 400

## 大文件支持（2 GiB 内存预算）

**阈值：** 200 MiB 未压缩（`_LARGE_ARRAY_BYTES = 200 * 1024 * 1024`），所有阶段统一，分块大小 512×512。

### cogify.py — 三步 COG 创建

`rio_copy(driver="COG", overviews="AUTO")` 会在 `/vsimem/`（虚拟内存）中计算概览，造成 OOM。

**修复：** 大文件走三步：
1. `rio_copy → GTiff`（分块，落磁盘）
2. `ds.build_overviews(OVERVIEW_LEVELS, Resampling.average)` 原地构建概览
3. `rio_copy → COG`，`copy_src_overviews=True`（复用已建概览，不走 vsimem）

全程在 `rasterio.Env(GDAL_CACHEMAX=512, GDAL_TIFF_OVR_BLOCKSIZE=512)` 中运行。`OVERVIEW_LEVELS` 和 `_GDAL_CACHE_MIB` 从 `cogify.py` 导出供 `writer.py` 使用。

### normalize.py — 分块重投影 + memmap

- 小文件：`reproject()` → 普通 ndarray
- 大文件：`reproject()` → `rasterio.Band`（分块 GeoTIFF）→ 逐窗口读入 `numpy.memmap`
- `stack._tmpdir`（TemporaryDirectory）持有所有 memmap 文件；通过 `with_array` / `with_mask` 传播

### slope.py — 分块 Horn 法

`isinstance(stack["dem"], np.memmap) or n_bytes > _LARGE_BYTES` 时走分块路径：
- 读 514×514 块（含1像素晕圈），运行 Horn 3×3，写 512×512 结果到 output memmap
- 边界超出时 `np.pad(..., mode="edge")`
- 峰值 RAM ≈ 5 MB/块

### weighted_overlay.py — 分块加权求和

`any(isinstance(..., np.memmap)...) or n_bytes > _LARGE_BYTES` 时走分块路径：
- 逐 512×512 块累加加权和，写入 output memmap
- 峰值 RAM ≈ (N+1) × 1 MB（N=6 时 ≤ 7 MB）

### 内存峰值汇总

| 阶段 | 峰值 RAM |
|---|---|
| cogify 大文件 | ≤ 512 MiB（GDAL cache）|
| normalize 每波段 | ≈ 1 MB/块 |
| slope 分块 | ≈ 5 MB |
| weighted_overlay 分块（N=6）| ≈ 7 MB |
| 写大输出 | ≈ 2 MB + 512 MiB GDAL cache |

## AlignedStack

- `spec: AlignmentSpec` — 共享网格
- `arrays: dict[str, np.ndarray]` — role → float32 数组（可能是 numpy.memmap）
- `mask: np.ndarray` — bool，True = 有效像素
- `_tmpdir: object` — TemporaryDirectory，保持 memmap 文件存活；通过 with_array/with_mask 传播；算法将输出 memmap 写入同一 tmpdir

## 参数扫描 / 单纯形采样（jobs/sweep.py）

**入口：** `POST /jobs` 携带可选 `param_ranges` 字段。展开后样本数 > 1 时走扫描分支，产出 1 个 parent (`kind='sweep'`) + N 个 child (`kind='sweep_child'`)。child 走原有 `runner._run_job` 路径。

**唯一支持的 kind — 单纯形均匀格点：**

```json
{ "weights": { "kind": "simplex", "n_divisions": 4, "min": [0, 0.1, 0], "max": [0.5, 0.6, 1] } }
```

- **`kind:"simplex"`**：`list[float]` 参数在 (n-1)-单纯形上均匀格点采样（nimplex），未过滤样本数 = C(n+T-1, T)，权重自动 sum=1；`n_divisions`≥1，n 由 `base_params` 列表长度推断
- **可选 `min` / `max` 数组**（长度=n）：超出任一分量 `[min[i], max[i]]` 的格点被丢弃。校验：每个值 ∈ [0,1]，`min[i] ≤ max[i]`，`sum(min) ≤ 1 ≤ sum(max)`；过滤后为空 → 400
- 多个 `simplex` 轴时取 Cartesian 乘积，轴顺序按 `algo.info.params` 声明顺序

**nimplex 依赖：** 编译好的 `.so` 在 `backend/vendor/nimplex.so`（lazy-import）。构建方法：安装 Nim，克隆 https://github.com/amkrajewski/nimplex，`nimble install arraymancer nimpy -y` 后 `nim c --d:release --threads:on --app:lib --passC:"-I<python_include>" --out:nimplex.so nimplex.nim`。

**关键不变量：**
- `MAX_SWEEP_SAMPLES = 200`（`backend/app/jobs/sweep.py`），超过返回 400
- child 不自动加载到地图，必须用户在 sweep 面板勾选 checkbox
- normalize 缓存**故意未做**；MAX_SWEEP_SAMPLES + max_workers=2 已能保证最坏情况可控

**端点：**
- `POST /jobs` — 单值或扫描都走这里
- `POST /jobs/preview` — 解析 param_ranges 并返回精确 sample_count（不入队）。前端在 bounds 非默认时调用以显示实时计数；payload 与 /jobs 相同但不需要 `inputs`
- `GET /jobs/{id}/children` — 扫描子任务列表（按 sample_index 排序）

**测试：** `conda run -n landplan pytest tests/` — 82 个测试（test_raster_delete、test_sweep、test_jobs_api、test_reclassify_algorithms、test_threshold_probability），覆盖 sweep 纯函数（含 bounds）、API（含 /jobs/preview）、重分类算法、WLC 阈值概率与删除引用检查。

**单一幸存者：** 当 bounds 把样本数压缩到正好 1 时，`POST /jobs` 用 `expand_ranges` 解析出的那个组合的 params 提交单任务，而**不是** `req.params` 中的 base 值。`n == 1` 分支在 `expand_ranges` 调用之后，保证有/无 bounds 都正确（空 `param_ranges={}` 也走这条路径，degenerate 时 `expand_ranges` 返回 `[(base, "")]`）。

## 开发路线图（按优先级）

1. ✅ **删除时引用检查**（已完成，规则见下节）：避免删除栅格后留下孤儿 job 记录。
2. **导入时处理地理坐标系（EPSG:4326）**：目前 slope 在度制坐标系上数值错误且无提示；至少导入时警告，或算法前自动重投影到投影 CRS。
3. **瓦片缓存**：`rio-tiler` 每次请求重读 COG；加 LRU/磁盘缓存，利于多个扫描结果叠加对比。
4. **任务持久化 + WebSocket/SSE**：重启丢任务、前端每秒轮询；先持久化，再换推送。
5. **normalize 结果缓存**：`AlignmentSpec.signature` 可作缓存键；目前"故意未做"，属产品取舍，动手前先确认。
6. **新功能方向**：矢量数据（GeoPackage，红线/保护区约束）；更多景观算法（视域、坡位、水文）；扫描结果汇总统计（均值/方差图）；`registry` 增加 `user_plugins/` 扫描。

## 删除栅格的引用规则

`DELETE /rasters/{id}` 先调用 `store.find_raster_references(id)`（扫描 jobs.inputs）：
- 被 **pending/running** 任务作为输入 → **409，任何情况下都不能删**（`code: "in_use"`）。
- 被 **已结束**（succeeded/failed）任务作为输入 → **409**（`code: "referenced"`，返回 `jobs` 列表），带 `?force=true` 才删除；历史 job 的 inputs 保留。
- 仅作为 job 的 `output_id`（即删除结果图）→ 直接允许，并把这些 job 的 `output_id` 置 NULL，不产生悬空引用。
- 前端 `deleteRaster()` 遇到 `referenced` 会弹窗列出引用任务，用户确认后带 `force=true` 重试。

## 已知注意事项（规则）

### ui.ts — Run 按钮必须在 recompute 之前创建

**规则：** 在 `algorithmSection()` 中，`runBtn` 必须在定义 `recompute` 闭包**之前**完成赋值，最后再 `form.appendChild(runBtn)`。

**原因：** `recompute()` 闭包捕获的是变量绑定。`renderDyn()` 在构建动态输入时会调用 `recompute()`，若此时 `runBtn` 仍是 `undefined`，`recomputeSweepCount` 内的 `runBtn.disabled = ...` 抛出 TypeError，整个 `onchange` 回调中断，Run 按钮永远不会被创建（页面上看不到任何报错）。

```typescript
// ✓ 正确顺序
const runBtn = document.createElement("button");
runBtn.onclick = () => submit(algo, form);
const recompute = () => recomputeSweepCount(form, counter, runBtn);
// ... 构建表单（可安全调用 recompute）...
form.appendChild(runBtn);
```

### FastAPI 204 端点

**规则：** 永远不要在路由装饰器上用 `status_code=204`。必须显式返回 `Response(status_code=204)`。

**原因：** FastAPI 在**模块导入时**（非请求时）抛出 `AssertionError: Status code 204 must not have a response body`，导致整个后端启动崩溃，前端侧边栏全白。

```python
from fastapi import Response

@router.delete("/{raster_id}")
def delete_raster(raster_id: str) -> Response:
    ...
    return Response(status_code=204)
```

适用范围：`backend/app/api/` 中所有 DELETE（及其他无内容响应）端点。

## SQLite 存储

位置：`backend/data/metadata.sqlite`（`DB_PATH` in `config.py`）

rasters 表关键字段：`id, name, cog_path, crs, bounds, bounds_wgs84, resolution, width, height, dtype, nodata, kind (source|result), semantic, scene_id, sensor, band_id, auto_role, role, resolution_m`

jobs 表扫描相关字段：`kind` (single|sweep|sweep_child, 默认 'single')、`parent_id`、`param_ranges` (json, 仅 parent)、`sample_count` (仅 parent)、`sample_label` + `sample_index` (仅 child)。

函数：`insert_raster, get_raster, list_rasters, delete_raster, insert_job, insert_sweep_job, update_job, get_job, list_children, list_scenes, insert_scene, update_raster_role`
