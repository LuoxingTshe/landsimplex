# LandSimplex — 开发指南

**当前版本：** `v1.0.2-beta.1`（2026-10-09，n=4 四面体探针视图；前端只保留 threshold_probability）。版本号同步位置：`backend/app/main.py`（FastAPI version）、`backend/pyproject.toml`（PEP 440 写法 `1.0.2b1`）、`frontend/package.json` + `package-lock.json`；发版说明写在 `CHANGELOG.md`，打 tag `vX.Y.Z-beta.N`。

## 环境与启动

- **Conda env:** `landplan`（Python 3.11, GDAL 3.8, rasterio 1.3, FastAPI）
- **后端：** `just backend`（等同于 `cd backend && conda run -n landplan uvicorn app.main:app --reload --host 127.0.0.1 --port 8765`）
  - **必须从项目根目录运行**，justfile 在根目录。
- **前端：** `just frontend`
- **访问：** http://localhost:5173
- **示例数据：** `data/samples/`（DEM.tif、TestDoc.tif，已 gitignore）；运行时数据仍在 `backend/data/`。合成 WLC 测试数据：`conda run -n landplan python scripts/make_wlc_sample.py` → `data/samples/wlc_test/`（A_east / B_north / C_center / D_waves，100×100；n=4 四面体用 A–D）。
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
    probe.py     — GET /probe/{raster_id}?lon&lat：像素层值 + 权重空间格点（单纯形探针）
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
  map.ts    — OpenLayers 地图；探针光标图层（onMapClick / setProbeFootprint）
  ui.ts     — 侧边栏（原生 DOM，无框架）
  simplexView.ts — 单纯形探针视图（SVG：n=2 线段、n=3 三角形、n=4 可拖拽旋转四面体；阈值滑块本地重算）
  simplexGeom.ts — 探针纯几何（无 DOM）：bounds 可行域（n≤3 多边形裁剪 / 任意 n 顶点+棱枚举）、凸包、四面体投影与隐藏棱
frontend/index.html — 全部样式（Swiss 风格，CSS 变量在 :root）
```

## 前端只暴露 threshold_probability

- `ui.ts` 的 `FRONTEND_ALGORITHMS` 白名单：前端算法下拉框只显示 `threshold_probability`（WLC阈值概率密度），并自动选中；"基础算法/复合分析"标签栏已删除。
- **后端算法引擎全部保留**（slope、aspect、weighted_overlay、各 reclassify 等仍注册、仍可用 API 调用），只是前端不再暴露。
- **原因（2026-10-09）**：用户想要概率图，却在 Weighted overlay 上开了单纯形采样，一次生成了 120 张中间结果图。用户明确只要 probability map，不要过程文件。不要再把其他算法加回前端，除非用户要求。

## 前端样式规范（Swiss Style）

- **样式集中在 `frontend/index.html` 的 `<style>`**，用 `:root` 变量（`--ink/--paper/--red/--hair/--mute/--u`）。`ui.ts` 里**不要**再写内联颜色/圆角，用 CSS 类（`.tag .badge .counter .job .tabs .primary .row .bounds` 等）。
- 风格约束：黑白 + 单一强调色（`--red`）、Helvetica 系字体、8px 栅格、无圆角无阴影、细线分隔。
- 状态用类名表达：`.badge.{succeeded|failed|running|pending}`、`.job.{status}`、`.counter.{err|pending}`。
- 底图图层带 `className: "basemap"`，由 CSS 灰度化；结果栅格的 colormap 不受影响。
- `.bounds` 默认 `display:none`，由 JS 内联切换 `inline-flex`/`none`（单纯形开关）。

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

**测试：** `cd backend && conda run -n landplan pytest tests/` — 92 个测试（test_raster_delete、test_sweep、test_jobs_api、test_reclassify_algorithms、test_threshold_probability、test_probe），覆盖 sweep 纯函数（含 bounds）、API（含 /jobs/preview）、重分类算法、WLC 阈值概率与删除引用检查。

**单一幸存者：** 当 bounds 把样本数压缩到正好 1 时，`POST /jobs` 用 `expand_ranges` 解析出的那个组合的 params 提交单任务，而**不是** `req.params` 中的 base 值。`n == 1` 分支在 `expand_ranges` 调用之后，保证有/无 bounds 都正确（空 `param_ranges={}` 也走这条路径，degenerate 时 `expand_ranges` 返回 `[(base, "")]`）。

## 开发路线图（按优先级）

1. ✅ **删除时引用检查**（已完成，规则见下节）：避免删除栅格后留下孤儿 job 记录。
2. ✅ **像素探针 + 权重空间单纯形视图 n=2/3**（v1.0.0-beta.1，规则见"单纯形探针"）。
3. ✅ **修复 Quit 漏洞**（已完成）：lifespan 关闭阶段调用 `runner.shutdown()`，见"Quit 按钮"一节。
4. **探针后续阶段**：✅ 阶段 3 = n=4 四面体（拖拽旋转、按深度排序）；阶段 4 = n=5/6（选 3 个权重做投影三角形，"其余"并入第三顶点，重叠格点聚合显示 + 平行坐标 + 三角形格↔折线联动高亮）。几何放 `simplexGeom.ts`（`feasiblePolytope`、`convexHull` 可直接复用），绘制放 `simplexView.ts`；后端接口已支持任意 n。
5. **`cog_path` 改存相对路径**：目前存绝对路径，项目目录一移动旧记录全部失效（beta 前曾手动重写过前缀）。
6. **导入时处理地理坐标系（EPSG:4326）**：目前 slope 在度制坐标系上数值错误且无提示；至少导入时警告，或算法前自动重投影到投影 CRS。
7. **瓦片缓存**：`rio-tiler` 每次请求重读 COG；加 LRU/磁盘缓存，利于多个扫描结果叠加对比。
8. **任务持久化 + WebSocket/SSE**：重启丢任务、前端每秒轮询；先持久化，再换推送。
9. **normalize 结果缓存**：`AlignmentSpec.signature` 可作缓存键；目前"故意未做"，属产品取舍，动手前先确认。
10. **新功能方向**：矢量数据（GeoPackage，红线/保护区约束）；更多景观算法（视域、坡位、水文）；扫描结果汇总统计（均值/方差图）；`registry` 增加 `user_plugins/` 扫描。

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

### 单纯形探针（/probe）

- 像素得分对权重线性：`score(w) = Σ wᵢ·vᵢ`。后端只返回该像素的 n 个层值 + 格点，通过/不通过由前端点积计算（阈值滑块即时重算）。
- 支持对象：`threshold_probability` 结果（格点来自 `params._internal_sweep.weights`，阈值 = `target_score`）和 `weighted_overlay` 扫描子结果（格点来自父任务 `param_ranges.weights`，阈值由前端定）。其他结果 → 400 `code:"not_probeable"`。
- **层值必须在结果栅格网格上采样**（`reproject` 到 1×1 目标像素，重采样同 `runner._build_resampling_map`）。normalize 会把网格对齐到分辨率整数倍，结果网格可能与输入网格错开半个像素，直接读源 COG 的像素会和地图值对不上。
- 不变量：threshold_probability 下 `mean(lattice·values > threshold) == output_value`（`test_probe.py` 覆盖对齐/错位网格）。
- 前端：栅格列表里可探针的结果带 `◎`（`isProbeable()` 按名字前缀判断，后端是最终权威）→ `state.probeTarget`；点地图 → `probePixel` → `simplexView.show()` + `setProbeFootprint()`。`Esc` 清除当前像素。
- **`render()` 只重建 `body`**：masthead 和 `simplexView.el` 在 `initPanel()` 里只挂载一次。轮询任务时每秒 render，如果重建视图会丢 SVG、打断滑块拖动。新增侧边栏区块时往 `body` 里加，不要再 `panel().innerHTML = ...`。
- 探针请求用 `probeToken` 丢弃过期响应（快速连点、切换目标时）。
- **不画阈值切面**（Σwᵢvᵢ = t 的线/面）：试做过，用户认为太丑，已删除（2026-10-09）。通过/不通过只用格点颜色表达。
- **n=4 四面体**：元素只建一次，拖拽时只重新投影 + 按深度从远到近重新 append（`threshold_probability` 的格点不受 200 上限约束，可上千个）。视角 `orbit` 存在视图闭包里，换像素保留，双击复位。隐藏棱：轮廓为三角形时内侧顶点若在前面之后则其 3 条棱虚线；轮廓为四边形时较远的对角线虚线。

### Quit 按钮（关闭前后端）

- 侧边栏 masthead 右上角 `.quit` 按钮 → `shutdownApp()`：先 `POST /shutdown`（后端），再 `POST /__shutdown`（Vite 插件，见 `frontend/vite.config.ts`），最后渲染 `.halted` 页面。
- 后端 `_terminate_server()`（`main.py`）先 `runner.shutdown()` 杀掉进程池（否则运行中的任务会阻塞退出），再 SIGTERM：`--reload` 下发给 reloader 父进程（`multiprocessing.parent_process()` 非 None），否则发给自己。
- 两个端点都校验 `Origin`（跨站简单 POST 不走 CORS 预检），非前端来源返回 403。
- **lifespan 关闭阶段也调用 `runner.shutdown()`**（`main.py` `_lifespan`）。`runner._executor` 是 worker 进程内的模块变量；`--reload` 重启 worker 时若不在此清理，旧进程池子进程会变成孤儿（父进程 = 1），继续占着 `conda run` 的 stdout 管道，`just backend` 挂住不退出（2026-10-08 实测，2026-10-09 修复）。验证：跑任务 → 改后端文件触发 reload → `pgrep -fl spawn_main` 只剩新 worker → Quit 后全部消失。

## SQLite 存储

位置：`backend/data/metadata.sqlite`（`DB_PATH` in `config.py`）

rasters 表关键字段：`id, name, cog_path, crs, bounds, bounds_wgs84, resolution, width, height, dtype, nodata, kind (source|result), semantic, scene_id, sensor, band_id, auto_role, role, resolution_m`

jobs 表扫描相关字段：`kind` (single|sweep|sweep_child, 默认 'single')、`parent_id`、`param_ranges` (json, 仅 parent)、`sample_count` (仅 parent)、`sample_label` + `sample_index` (仅 child)。

函数：`insert_raster, get_raster, list_rasters, delete_raster, insert_job, insert_sweep_job, update_job, get_job, list_children, list_scenes, insert_scene, update_raster_role`
