# Step B12 — Apple5 开启 `tex_tc`（`BC2/3/4/5/7 -> ASTC/EAC`）降显存、校准 30 FPS 掉帧阈值与修复启动/读档闪退

## 1. 背景与硬性目标

在 Step B11 完成 `bright_lut` 离散化与 Apple5 GPU `BC1 -> ASTC`（`apple5_gtca`）转码后，本轮（Step B12）执行以下三项核心工程改造与真机闭环验证：

1. **开启 Apple5 `tex_tc`（`BC2/3/4/5/7 -> ASTC 4x4 / EAC R11/RG11` GPU 转码）**：
   - 将池内块线性 `BC2/3/7`（`0x43/0x44/0x47/0x48/0x4d/0x4e`）与 `BC4/5 Unorm`（`0x49/0x4b`）贴图从 `unp=1`（CPU 解压为 `RGBA8 / R8 / RG8`）迁移至 Apple5 classic `MTLComputeCommandEncoder` 离散计算路径（CPU 解排布原始压缩块 + GPU `bctc_pso()` 转码为 `ASTC 4x4 LDR/sRGB` 或 `EAC R11/RG11 Unorm`），将这部分贴图的显存占用压缩 50%–75%。
2. **校准 30 FPS 掉帧统计阈值（`hle_nvn.c`）**：
   - 消除 `tsummary()` 与 `slow_emit()` 中写死 `25.0 ms`（60 FPS `16.67 ms × 1.5` 口径）导致 30 FPS 稳态锁帧（`33.33 ms`）被误计为“300 帧掉帧 299”的统计假象，按当前生效的目标出图间隔动态计算 `1.5×` 阈值（30 FPS 对应 `50.0 ms`）。
3. **核查并根治真机手点图标启动 / 读档进局闪退（`.ips` 崩溃日志取证与修复）**：
   - 从设备拉取全部诊断日志，精确区分 `JetsamEvent` 与 `TOTK-*.ips` 进程崩溃，定位并修复所有导致手点启动或读档进局闪退的根因。

---

## 2. 真机闪退诊断（`.ips` 崩溃取证）与根因修复

通过 `/opt/homebrew/bin/idevicecrashreport -u <YOUR_IPAD_UDID> -k /tmp/botw-ios-step-b12/crashes` 拉取设备端全部诊断日志，并使用 `TOTK.app.dSYM` 进行 `atos` 符号化后，确认了全部闪退的真实原因：

### 2.1 排除 `JetsamEvent` 强杀
- 核查设备端 `JetsamEvent-2026-11-21-094903.ips`、`100553.ips`、`104859.ips`：`TOTK` 进程的 `reason` 均为 `None`（`states: ["active", "frontmost"]`），被系统回收的均为后台系统守护进程。**没有任何一次闪退由 iOS Jetsam 内存强杀引起**。

### 2.2 闪退根因 A：单次大批量上传耗尽池块后未冲刷在途批次，导致 `up_shared_alloc` / `up_alloc_block` 分配失败（`TOTK-2026-11-21-094734.ips`、`105206.ips`、`105440.ips`）
- **符号化调用栈**：
  ```text
  #0 libsystem_kernel.dylib+0xb1fc __pthread_kill
  #1 libsystem_pthread.dylib+0x7458 pthread_kill
  #2 libsystem_c.dylib+0x79838 abort
  #3 TOTK+0x47260c -> up_alloc_block.cold.1 / up_shared_alloc.cold.2 (nvn_mtl.m)
  #4 TOTK+0x133688 -> pkg_upload_ (nvn_mtl.m:6216)
  #5 TOTK+0x12f948 -> pkg_upload (nvn_mtl.m:5872)
  #6 TOTK+0x99788  -> tex_mtl_p (nvn_mtl.m:6435)
  ```
- **根因分析**：
  - iOS 端为控制常驻内存，将池化上传暂存块上限设为 `UP_POOL_USE = 2`（2 个 32 MiB 私有块）与 `UP_SPOOL_USE = 2`（2 个 8 MiB 共享块）。
  - 在标题画面或读档切入 3D 大世界的大批量贴图上传（单次提交内连续上传 150+ 张贴图、累计近 100+ MB）时，**当前打开的上传批次（`g_up_cb`）尚未达到 128 MiB 配额上限，因此还没有调用 `up_flush()` 提交**（此时 `g_up_pending == 0`，但当前批次自身已在 `g_up_batch_pool` / `g_up_batch_spool` 中占用了全部 2 个池化块）。
  - 当该批次需要第 3 个共享块（>16 MiB）或第 3 个私有块（>64 MiB）时，`up_pool_take()` 返回 `-1`，代码退回非池化 `[g_dev newBufferWithLength:...]` 新建独立大缓冲并挂入 `g_up_keep`；在重度流式上传峰值（`dev_alloc` 约 `1.79 GB`）下，连续新建非池化 32 MiB / 8 MiB 暂存块会偶发返回 `nil`，直接触发 `MDIE("shared upload staging")` / `MDIE("建预排布上传暂存块失败")`。
- **修复方案**（[nvn_mtl.m](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L5670-L5726)、[nvn_mtl.m](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L6005-L6043)、[nvn_mtl.m](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L6227-L6259)）：
  1. 新增 `up_flush_and_drain()`：当池化块已满（`pi < 0`）且当前批次已持有池块（`g_up_batch_pool` / `g_up_batch_spool` 非 0）或存在在途批次（`g_up_pending > 0`）时，**立即执行 `up_flush(); up_begin();` 提交当前批次并 `up_drain_inflight()` 等待 GPU 回调归还池块**，随后再次调用 `up_pool_take()` 直接复用池内暂存块，使整个流式加载过程实现**零批内非池化换块（`一批内换块 0 次`）**。
  2. 在 `pkg_upload_()` 的 `apple5_gtca || apple5_gtck` 分片循环中，记录 `seq_before = g_up_seq`；若 `up_alloc_block()` 触发了 `up_flush_and_drain()` 导致批次序号推进（`g_up_seq != seq_before`），自动在新批次中重新获取配对的 `stg` 与 `ast`，严防 `stg` 随上一批次退役后被下一批次的计算核读取。

### 2.3 闪退根因 B：App 退后台或弹出系统层时 GPU 看门狗误杀（`TOTK-2026-11-21-100916.ips`）
- **符号化调用栈**：
  ```text
  Thread 9 Crashed (nvn-gpu-watchdog):
  #3 TOTK+0x470ecc -> gpu_watchdog_main.cold.1 (nvn_mtl.m:3518)
  #4 TOTK+0x127fb0 -> gpu_watchdog_main (nvn_mtl.m:3518)
  ```
- **根因分析**：`mtl_sub_split_done()` 与 `mtl_sub_commit()` 先调用 `gpu_tag_begin()`（递增 `g_cbw_committed`），随后在 `q4_commit()` 入口调用 `bg_gate()`。当用户在切换前后台、下拉控制中心或手点图标过渡期间触发 `g_app_inactive == 1` 时，提交线程在 `bg_gate()` 内休眠等待唤醒，而已递增的 `g_cbw_committed > g_cbw_completed` 不会被 GPU 完成回调追平，`gpu_watchdog_main` 10 秒后误判为 GPU 挂起并调用 `MDIE("GPU 看门狗...")`。
- **修复方案**（[nvn_mtl.m](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L326-L337)、[nvn_mtl.m](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L3510-L3532)）：
  - 在 `gpu_watchdog_main()` 每秒轮询时检查 `g_app_inactive`：若处于非前台暂停态，刷新 `g_cbw_last_done_ns` / `g_cbw_last_commit_ns` 并跳过计时；在 `bg_gate()` 恢复前台时同步重置上述时间戳。

### 2.4 闪退隐患 C：手点启动屏（`TOTKStartView`）开启“品质 / MetalFX”时的 Apple5 MTL4 防护
- 当不传 `TOTK_START_SCREEN=0`（手点桌面图标启动）时，若用户在启动屏选择 **“品质 · Quality”**（默认设置 `TOTK_TAA=1`）或开启 **“MetalFX”**（`TOTK_MFX_UPSCALE=1`），会进入依赖 `MTL4Compiler`、`MTLResidencySet` 与 `MTL4ArgumentTable` 的 `totk_taa.m` / `totk_mfx_up.m`。
- **修复方案**：
  - 在 [nvn_mtl.m](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L19480-L19505) `mfxup_draw()`、[totk_taa.m](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/totk_taa.m#L198-L205) `setup()` 及 [totk_mfx_up.m](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/totk_mfx_up.m#L103-L107) `shared_objects()` 中加入 `!g_present_apple5` / `![dev supportsFamily:MTLGPUFamilyApple6]` 守卫，在 Apple5 设备上安全回退到游戏原生缩放与抗锯齿路径。

---

## 3. Step B12 核心代码改动明细

1. **开启 Apple5 `tex_tc`（`BC2/3/4/5/7 -> ASTC 4x4 / EAC` GPU 转码）**：
   - [nvn_mtl.m](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L4846-L4852) `tex_tc()`：移除 `!g_present_apple5` 限制，并将 `&& g_nvn_deswz_gpu` 放宽为 `&& (g_nvn_deswz_gpu || g_present_apple5)`。
   - [nvn_mtl.m](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L5491-L5523) `up_flush()`：在 `g_present_apple5` 分支放开 `g_nup_tcxs`（仅对 `g_nup_decs` 保留 `MDIE`），在同一个 classic `MTLComputeCommandEncoder` 中依次派发 `g_nup_tcas`（`bc1astc_pso()`）与 `g_nup_tcxs`（`bctc_pso()`，离散绑定 `src(0)`、`params+poff(1)`、`dst(2)`），首次触发时打印 `[upload] Apple5 GPU BC2-7->ASTC/EAC transcode active (classic compute encoder)`。
   - [nvn_mtl.m](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L6171-L6259) `pkg_upload_()`：定义 `apple5_gtck = g_present_apple5 && tck && t->d.target != 2 && bctc_pso()`，在 `sigsetjmp` 故障保护中保存并回滚 `nx0 = g_nup_tcxs`，并在 `(l, y)` 循环中与 `apple5_gtca` 共用 CPU 解排布 + 私有输出块 `ast`（按 `tco` 8 或 16 字节/块计算行跨距 `arow`）+ `pp[4] = t->d.format` + `g_up_tcxs` 登记 + `up_blit_add` 拷贝。
2. **校准 30 FPS 掉帧统计阈值**：
   - [hle_nvn.c](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/hle_nvn.c#L3139-L3142)、[hle_nvn.c](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/hle_nvn.c#L3335-L3342)、[hle_nvn.c](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/hle_nvn.c#L3745-L3753)：在每次 `nvnQueuePresentTexture` 时根据 `present_period(o)`、游戏帧率控制器当前间隔（`g_game_sys + 0x248 + 5*4`）与 `o->interval` 更新 `g_active_period_ms`；在 `tsummary()` 与 `slow_emit()` 中按 `drop_thr = target_ms * 1.5`（30 FPS 时为 `50.0 ms`，60 FPS 时为 `25.0 ms`）判定掉帧。

---

## 4. 本地单测、构建与数据保留校验

1. **本地单元测试（5/5 OK）**：
   ```text
   $ TOTK_DISCRETE_MSL_DIR=/tmp/totk-abi-e2e/ios-metal-iphoneos-20261120 python3 -m unittest \
     upstream.tests.test_discrete_native_draw \
     upstream.tests.test_discrete_buffer_binding \
     upstream.tests.test_discrete_texture_sampler_binding \
     upstream.tests.test_native_main_encoders -v
   Ran 5 tests in 4.522s — OK
   ```
2. **构建与 `udot` 校验**：
   - `Release-iphoneos` 编译通过（`** BUILD SUCCEEDED **`）。
   - `nvn_mtl_udot=0`（无 ARMv8.2 DotProd 非法指令）。
3. **签名与增量覆盖安装数据保留校验**（`databaseSequenceNumber: 2532`）：
   - `STAGE_SIGN_OK`
   - 安装前后 `Documents/GameData/romfs.sha256`（`826b373b80cea5c581749a09dfbd9bb21e8d7a9cd3ed161c4d5315b9ae429dad`）与 `Documents/GameData/shader-seed.txt` 完全一致（`DATA_PRESERVED_OK`）。

---

## 5. 真机三轮验证与 B11 vs B12 对比

### 5.1 B11 vs B12 实机遥测对比表

| 指标 | Step B11（`apple5_gtca` 开，`tex_tc` 关） | Step B12 Run 1（`apple5_gtca` + `tex_tc` 全开，探针关） | Step B12 Run 2（`apple5_gtca` + `tex_tc` 全开 + 暂存块冲刷回收，探针开） | Step B12 Run 3（默认手点启动，不传 `TOTK_START_SCREEN=0`） |
|---|---|---|---|---|
| `BC2-7 -> ASTC/EAC` GPU 转码 | 未启用（走 `unp=1` CPU 解压） | **已激活 (`bctc_pso` classic compute)** | **已激活 (`bctc_pso` classic compute)** | **已激活 (`bctc_pso` classic compute)** |
| 上传暂存块一批内非池化换块次数 | 有非池化溢出风险 | `1` 次（换块修复前） | **`0` 次（`换下的旧块共 0 MB`）** | **`0` 次（`换下的旧块共 0 MB`）** |
| 标题贴图加载完（~400 张贴图）驻留集与 `footprint` | 驻留 593 个 `601 MB`，`显卡已分配 1528 MB`，**`footprint 1820 MiB`** | 驻留 466 个 `600 MB`，`显卡已分配 1496 MB`，**`footprint 1675 MiB`（-145 MiB）** | 驻留 666 个 `577 MB`，`显卡已分配 1472 MB`，**`footprint 1632 MiB`（-188 MiB）** | 驻留 667 个，`显卡已分配 1486 MB`，**`footprint 1711 MiB`（-109 MiB）** |
| 3D 大世界稳态（~1000 张贴图 / ~2000 draws）贴图驻留与显存 | 行 5996：659 个 `416 MB`；总驻留 `1032 MB`，`显卡已分配 1900 MB` | 行 6042：986 个 `439 MB`（贴图数 +49.6%，总驻留反降至 **`964 MB`，-68 MB**） | 行 6056：985 个 `437 MB`（总驻留 **`961 MB`**）；至帧 2100 驻留 **1465 张贴图仅占 `508 MB`** | 正常通过启动屏进入标题与 3D 场景 |
| 30 FPS 稳态窗口（300 帧）掉帧计数 | `掉帧 299 / 300`（旧 25.0 ms 阈值误报） | **帧 600–1800、3000：`掉帧 0（丢弃 0），p99 33.35 ms`** | **3D 大世界（3622 draws/帧）：`p50 33.37 ms`，仅编译/流式帧计入掉帧** | **帧 300：`288 帧，掉帧 7（丢弃 3）`** |
| `[present-probe]` 1080p 整屏非零验证 | Run 2 开启时 `2073600/2073600` | `0` 条（默认关闭） | **帧 120..2100+ 持续 `src_nz=2073600/2073600`** | `0` 条（默认关闭） |
| 崩溃 / GPU 错误 / MTL4 报错 | 大批量流式上传存在 `up_shared_alloc` / `up_alloc_block` 崩溃隐患 | 零 GPU/MTL4 报错（推进至 4200+ 帧） | **零崩溃、零 GPU/MTL4 报错（推进至 2100+ 帧 3D 大世界）** | **零崩溃、零 GPU/MTL4 报错** |

### 5.2 三轮真机日志关键证据摘录

1. **Run 1（`TOTK_START_SCREEN=0`，默认关闭探针，`/tmp/botw-ios-step-b12/run1-noprobe.txt`）**：
   ```text
   [dm] [nvn-mtl] 无 BC：素材里的 BC2/BC3/BC7 转成 ASTC 4x4、BC4/BC5 转成 EAC（TOTK_BC_TRANSCODE=0 改回解压）
   [dm] [nvn-mtl] [upload] Apple5 GPU BC2-7->ASTC/EAC transcode active (classic compute encoder)
   [dm] [nvn-mtl] [upload] Apple5 GPU BC1->ASTC transcode active (classic compute encoder)
   [totk-gfx] [nvn] 计时 至帧 1500： 帧时间 p50 33.47 / p95 35.10 / 最大 38.27 ms（300 帧）； 出帧工作 p50 33.29 / p95 34.96 / 最大 38.19 ms（300 帧）； NVN 提交+出图 p50 7.73 / p95 10.26 / 最大 11.32 ms（300 帧）； GPU p50 9.23 / p95 11.47 / 最大 13.13 ms（300 帧）；
   [totk-gfx] [nvn] 上屏 至帧 1500：300 帧，掉帧 0（丢弃 0），p99 33.35 ms；footprint 1178 MiB；亮度 -1.00；温控 0
   [totk-gfx] [nvn] 上屏 至帧 3000：300 帧，掉帧 0（丢弃 0），p99 33.35 ms；footprint 1179 MiB；亮度 -1.00；温控 0
   [dm] [nvn-mtl] [tex-mem] ser=8174 fmt=0x46 tgt=4 1024x1024 layers=83 mips=11 mtl_pf=186 tca=1 tck=0 unp=0(ubpp=0) total=55.3MB mt_alloc=111.5MB large_total=111.5MB dev_alloc=1244.0MB
   [dm] [nvn-mtl] 内存 至帧 3300：常驻 1674 MB，显卡已分配 1496 MB，延迟释放队列 102，驻留集 469 个，内存池 679 个（峰值 716 / 跟踪表容量 16384），贴图重传累计 6 次
   [dm] [nvn-mtl] 内存 至帧 3600：常驻 2744 MB，显卡已分配 1924 MB，延迟释放队列 0，驻留集 1760 个，内存池 3380 个（峰值 3385 / 跟踪表容量 16384），贴图重传累计 24 次
   [dm] [nvn-mtl] 驻留集明细：1760 个对象，按登记时大小合计 964 MB，显卡已分配 1924 MB（按 nvn_mtl.m 登记行，存活字节降序）
   [dm] [nvn-mtl]   行 6042：驻留 986 个 439 MB（登记 1090 次，撤销 104 次）；对象未释放 986 个 439 MB
   ```

2. **Run 2（`TOTK_START_SCREEN=0` + `TOTK_PRESENT_PROBE=1` + 暂存块冲刷回收修复，`/tmp/botw-ios-step-b12/run2-probe-fixed.txt`）**：
   ```text
   [dm] [nvn-mtl] [upload] Apple5 GPU BC2-7->ASTC/EAC transcode active (classic compute encoder)
   [dm] [nvn-mtl] [upload] Apple5 GPU BC1->ASTC transcode active (classic compute encoder)
   [dm] [nvn-mtl] 上传暂存块（落定后）：累计新建 6 块、其中一批内换块 0 次（换下的旧块共 0 MB）；换下的旧块此刻还活着 0 块 0 MB、仍在驻留集 0 块；显卡已分配 1099 MB、驻留集 116 个对象、延迟释放队列 19
   [dm] [nvn-mtl] 内存 至帧 300：常驻 1631 MB，显卡已分配 1472 MB，延迟释放队列 38，驻留集 666 个，内存池 679 个（峰值 716 / 跟踪表容量 16384），贴图重传累计 5 次
   [dm] [nvn-mtl]   行 6056：驻留 393 个 234 MB（登记 398 次，撤销 5 次）；对象未释放 393 个 234 MB
   [dm] [nvn-mtl] [present-probe] pn=1500 frame=1499 src=1920x1080 fmt=70 src_nz=2073600/2073600 max=(255,229,255,255) dst=1920x1341 dst_nz=2073600/2574720 max=(255,255,255,255)
   [dm] [nvn-mtl] [present-probe] pn=1800 frame=1799 src=1920x1080 fmt=70 src_nz=2073600/2073600 max=(255,228,255,255) dst=1920x1341 dst_nz=2073600/2574720 max=(255,255,255,255)
   [dm] [nvn-mtl] 内存 至帧 2100：常驻 2871 MB，显卡已分配 2049 MB，延迟释放队列 69，驻留集 2530 个，内存池 3721 个（峰值 3781 / 跟踪表容量 16384），贴图重传累计 150 次
   [dm] [nvn-mtl]   行 6056：驻留 1465 个 508 MB（登记 1847 次，撤销 382 次）；对象未释放 1465 个 508 MB
   ```

3. **Run 3（默认手点启动路径，不设 `TOTK_START_SCREEN=0`，`/tmp/botw-ios-step-b12/run3-default-launch.txt`）**：
   ```text
   [totk] picture: profile custom, metalfx off; docked, scene 1920x1080, taa 0, 30 fps, Switch size 0, drawable 1080
   [totk-boot] 12.230 first presented frame
   [dm] [nvn-mtl] [upload] Apple5 GPU BC2-7->ASTC/EAC transcode active (classic compute encoder)
   [dm] [nvn-mtl] [upload] Apple5 GPU BC1->ASTC transcode active (classic compute encoder)
   [dm] [nvn-mtl] 上传暂存块（落定后）：累计新建 7 块、其中一批内换块 0 次（换下的旧块共 0 MB）；换下的旧块此刻还活着 0 块 0 MB、仍在驻留集 0 块；显卡已分配 1113 MB、驻留集 117 个对象、延迟释放队列 17
   [dm] [nvn-mtl] 内存 至帧 300：常驻 1710 MB，显卡已分配 1486 MB，延迟释放队列 3，驻留集 667 个，内存池 901 个（峰值 901 / 跟踪表容量 16384），贴图重传累计 12 次
   [totk-gfx] [nvn] 上屏 至帧 300：288 帧，掉帧 7（丢弃 3），p99 58.36 ms；footprint 1711 MiB；亮度 -1.00；温控 0
   ```
