# Step B15：掌机 720p 闪退定罪修复、自动真机掉帧剖析与 Apple5 性能优化报告

## 1. 掌机 720p 闪退取证与根因定罪

通过 `/opt/homebrew/bin/idevicecrashreport -u <YOUR_IPAD_UDID> -k /tmp/botw-ios-step-b15/crashes` 拉取设备最新崩溃日志，使用 B14 构建对应的 `TOTK.app.dSYM`（`UUID: 857B53AC-3DA0-3BB3-9E07-27EF6E6728EB`）完成 `atos` 完整符号化：

1. **掌机 720p 闪退实锤（`TOTK-2026-11-21-152930.ips`）**
   - 进程：`TOTK` PID `4202`（`2026-11-21 15:27:29.7510 +0800` 启动，`15:29:28.9410 +0800` 崩溃），异常 `EXC_BAD_ACCESS / SIGABRT (abort() called)`。
   - `atos` 符号化崩溃线程（Thread 6）：
     `arena_alloc_u.cold.1 -> arena_alloc_u -> upd_shadow_resolve (nvn_mtl.m:3190) -> encode_args -> draw_inner -> draw (nvn_mtl.m:19124) -> mtl_exec (nvn_mtl.m:22038) -> submit_exec (hle_nvn.c:2931) -> h_nvnQueueSubmitCommands (hle_nvn.c:2891) -> runtime_hle_switch -> dm_thread_enter`。
   - **进程虚拟机快照（`vmSummary`）**：`Writable regions: Total=3.8G`，`shared memory: 1.7G` 碎片化分布于 **`1015` 个独立区域**。
   - **Jetsam 对照**：`JetsamEvent-2026-11-21-152154.ips` 与 `155650.ips` 中 `TOTK` 均以 `frontmost` 处于 `reason: None`，系统仅清理后台 `searchd` / `fileindexerd` / `UVCAssistant`，**并非 Jetsam 查杀 `TOTK`**。
2. **精确根因**：
   - B14 将 iOS `arena_trim()` 设为 `keep_max = 8`、`idle_submits = 30`，低于大世界约 `19–28` 块的工作集需求，导致在 9,900 帧内反复分配与释放 `10,830` 块 1 MiB `MTLResourceStorageModeShared` 缓冲，加剧 iOS 共享内存区碎片化。
   - 当 `[g_dev newBufferWithLength:]` 返回 `nil` 时，B14 `arena_new()` 的恢复循环仍使用冻结在 `pend_begin()` 时刻的粗粒度截断条件 `it->fence <= g_pend_reuse_done`，导致等待 GPU 完成 500 ms 后最新完成栅栏的 `NvArena` 块依然被全部跳过、既不复用也不清理，且未触发 `defer_drain()` / `@autoreleasepool` 或块大小降级便直接 `MDIE`。

---

## 2. Apple5 源码修复与性能优化明细

全部修改集中在 `upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`：

| 模块 | 精确行号 | 改动内容与作用 |
| --- | --- | --- |
| **精确 Pending Pin 检查、主动排空与块尺寸兜底**（根治 720p Arena 崩溃） | `L9967-L10078` | 1) 新增 `arena_is_pend_pinned(const NvArena *it)`：仅当 `g_pend_kind[i] == 0` 且 `g_pend_src[i]` 真正落入 `[it->cpu, it->cpu + it->used)` 时才阻止复用/释放，解除 `g_pend_reuse_done` 冻结假阳性锁死；<br>2) `arena_purge_idle_locked(done, exact_pin)` 增加 `@autoreleasepool` 立即回收底层 Metal `MTLBuffer`；<br>3) `arena_new()` 分配失败后先精确清理并临时释放 `g_mu` 调用 `defer_drain()`，再在 `g_cv` 上分 `10 × 50 ms` 等待 GPU 完成并复用/排空，最后支持从 `1 MiB` 逐级减半降级至 `64 KiB`（`minimum` 对齐）兜底。 |
| **优化 A：消除 Arena 分配抖动** | `L10088-L10134` | 将 `#if TARGET_OS_IPHONE` 下的 `arena_trim()` 参数放宽为 **`idle_submits = 150`、`keep_max = 32`**（原 `30` / `8`），并维持每 300 次提交打印 `[arena] stats` 与 `[discrete-cache]` 遥测。 |
| **优化 B：Apple5 离散绑定与管线/深度状态缓存** | `L2237-L2320`, `L2376-L2386`, `L6921`, `L7331`, `L13957-L14050`, `L15432-L15682`, `L19618-L19627`, `L20828`, `L21365`, `L21425` | 新增每编码器轻量平铺状态缓存 `g_disc_r` / `g_disc_c`（render / compute encoder 创建与结束时清零，`es_reset` / `rp_end_from` / `pg_compute` / `dispatch` 精确同步失效）：<br>1) 在 `enc_r_pso` / `enc_r_dss` / `enc_c_pso` 跳过相同管线与深度模板状态设置；<br>2) 新增 `disc_cache_bind_buffer`、`disc_cache_set_vertex_buffer`（与 stage 0 共享 `vbuf[0..30]` 缓存）、`disc_cache_bind_texture`、`disc_cache_bind_sampler`，命中且指针+偏移全同直接跳过，仅偏移变化改用轻量 `set*BufferOffset:atIndex:`；保持 `discrete_bind_*` / `classic_set_vertex_buffer` 原函数纯净以兼容离线提取单测。 |

---

## 3. 单测、编译、签名与数据一致性校验

- **本地单测（14/14 OK）**：
  - `TOTK_DISCRETE_MSL_DIR=/tmp/totk-abi-e2e/ios-metal-iphoneos-20261120 python3 -m unittest upstream.tests.test_discrete_native_draw upstream.tests.test_discrete_buffer_binding upstream.tests.test_discrete_texture_sampler_binding upstream.tests.test_native_main_encoders upstream.tests.test_warm_policy -v`
  - 结果：`Ran 14 tests in 7.294s - OK`
- **构建与校验**：
  - `xcodebuild` `Release-iphoneos`：`** BUILD SUCCEEDED **`（`nvn_mtl.m` 零新增警告）。
  - `nvn_mtl_udot=0`（`otool -tV ... nvn_mtl.o | grep -c '\budot\b'` = `0`）。
  - 签名与部署：`STAGE_SIGN_OK`（`CFBundleIdentifier=org.macnativegames.botw.a12z`，安装 `databaseSequenceNumber: 2584`）。
  - **数据与管线存档保留**：`romfs.sha256`（`23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774`）与 `shader-seed.txt`（`189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9`）校验 **`DATA_PRESERVED_OK`**，`pipelines/` 保持 **`3263`** 条 `.metalarchive` 管线存档完好。

---

## 4. 真机剖析与双模式对比（B14 vs B15：掌机 720p & 底座 1080p）

两次真机运行均保持 `[present-probe]` 默认关闭，自动化 `[autoload]` 读档进 3D 大世界并稳定推进至第 **`3,300`** 帧，测试后拉取 `/tmp/botw-ios-step-b15/crashes-post` 确认 **零新 `TOTK-*.ips` 崩溃、零 Jetsam 查杀 `TOTK`**，零 GPU / MTL4 / `gpuAddress` 报错，且全程 **`温控 0`**。

### 4.1 掉帧主因定量剖析（来自 `/tmp/botw-ios-step-b15/run1-720p.log` 与 `run2-1080p.log`）
1. **读档瞬间峰值长帧（第 339–665 帧，最大 `1014.6–1055.1 ms`）**：
   - Top 5 `[长帧]` 中，**`95%+` 耗时集中在流式大纹理首次转码与上传**（单帧 `52.5–112.3 MB`、`199.0–810.7 ms`），而管线/着色器编译仅 `0.0–1.1 ms`（B13 `3,263` 条预热管线使全周期运行时仅 `1–4` 次未命中、`0` 次即时转译）。
2. **进入大世界稳态后（第 1200–3300 帧）**：
   - 贴图淘汰触发 **`0` 次**（`evict_triggers=0`）、温控降频 **`0`**（`温控 0`）。
   - **优化效果直接兑现**：`[discrete-cache]` 在两档模式下均实现 **`82.5%–82.6%` 冗余绑定跳过率**（跳过约 `1.26–1.29 亿` 次 Metal 调用，另有约 `1,309–1,370 万` 次转为纯偏移更新）；`Arena` 释放次数到 `sub=6900` 从 B14 的 `4,575` 块锐减至 **`130` 块（720p）/ `279` 块（1080p）**，720p 自 `sub=1500` 起至 `sub=6600` 保持 `released=130` **零抖动**。
   - **720p vs 1080p 硬件余量差异**：720p 下 `GPU p50` 稳定在 **`14.25–14.43 ms`**（`p95 16.05–16.55 ms`），比 1080p（`GPU p50 17.13–17.22 ms`, `p95 18.27–18.51 ms`）低约 **`2.8 ms`（~17%）**，`footprint` 也低约 `277 MiB`。

### 4.2 B14 vs B15 同场景（至第 3,300 帧）量化对比

| 对比指标 | B14 底座 1080p（至第 3,300 帧基线） | B15 Run 1：掌机 **720p**（至第 3,300 帧） | B15 Run 2：底座 **1080p**（至第 3,300 帧） |
| --- | --- | --- | --- |
| 分辨率配置 | `docked`, `1920x1080` (`drawable 1080`) | `handheld`, `1280x720` (`drawable 720`) | `docked`, `1920x1080` (`drawable 1080`) |
| 崩溃 / 闪退 / 温控 | 此前 720p 于 ~2m 处 `.ips`；1080p 5132 帧后正常 | **零崩溃、零 Jetsam、`温控 0`** | **零崩溃、零 Jetsam、`温控 0`** |
| Arena 抖动 (`sub=6600/6900`) | `kept=19 (19 MB)`, `released=4,575` 块 | `kept=28 (28 MB)`, **`released=130` 块**（`sub>=1500` 零新增释放） | `kept=28 (28 MB)`, **`released=279` 块**（**降 93.9%**） |
| 离散绑定去重统计 | 无（全量调用） | `sets=13.67M / off=13.70M / skips=128.60M` (**`82.5%` 跳过**) | `sets=13.41M / off=13.09M / skips=125.65M` (**`82.6%` 跳过**) |
| 稳态帧时间（第 2700–3300 帧） | 第 3300 帧：`p50 33.37 / p95 39.39 / max 51.91 ms`，`p99 58.36 ms`（第 3000 帧 `p95 50.17`） | 第 3300 帧：**`p50 33.46 / p95 35.00 / max 46.48 ms`**（第 3000 帧 `max 36.06 ms`） | 第 3300 帧：**`p50 33.45 / p95 34.87 / max 42.41 ms`**，**`p99 33.35 ms`** |
| 稳态 NVN 提交 / GPU 耗时（第 3300 帧） | 提交 `p50 16.45 / max 25.64 ms`；GPU `p50 15.51 / p95 17.02 ms` | 提交 `p50 16.89 / p95 18.25 ms`；**GPU `p50 14.30 / p95 16.37 ms`** | 提交 `p50 17.07 / max 19.36 ms`（峰值降 6.3 ms）；GPU `p50 17.13 / p95 18.27 ms` |
| 掉帧数（2101–3300 帧共 4 个窗口） | `25 + 25 + 30 + 22 = 102` 帧 | `5 + 2 + 7 + 5 = 19` 帧（**-81.4%**，第 2700 帧仅 `2`） | **`4 + 0 + 1 + 0 = 5` 帧（-95.1%，第 2700/3300 帧 0 掉帧）** |
| Footprint（第 3300 帧 / 峰值） | `2622 MiB` / `2645 MiB` | **`2566 MiB`** / **`2584 MiB`** | `2843 MiB` / `2930 MiB` |
| 运行期管线未命中 / 转译 | `0` / `0` | `4` / `0` | `1` / `0` |
| 音频欠载 (`underruns`) | `0` | **`0`**（`5,999,616` 回调帧） | **`0`**（`5,999,616` 回调帧） |

---

## 5. 下一步优化建议

1. **大世界高强度跑图与相机旋转压测（Step B16）**：当前已证明原址长稳下两档均达到 30 FPS 锁帧（1080p 第 2400–3300 帧 900 帧仅掉 1 帧，720p GPU 余量多出 ~2.8 ms 且 footprint 仅 2566 MiB）；下一步可在手玩跑图/战斗/神庙切换场景验证流式纹理高负载上传峰值下 `arena` 恢复兜底与 `pkg_evict_sweep` 表现。
2. **读档瞬间 1 秒长帧异步化（可选）**：若要进一步压缩进世界初期第 339–665 帧的 `467–1055 ms` 纹理首传长帧，可考虑拆分单帧大批量 `pkg_upload_` 预算或在非首帧可见纹理上做分帧延迟转码。
