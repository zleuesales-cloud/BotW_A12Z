# Step B8: Apple5 黑屏根因定位、全局 MTL4 清理、`Cube -> CubeArray` 解阻与显存观测报告

- 测试日期：2026-11-21
- 目标设备：`iPad8,9`（iPad Pro 2020, Apple A12Z GPU / Apple5 家族, UDID `<YOUR_IPAD_UDID>`）
- 目标 Bundle ID：`org.macnativegames.botw.a12z`
- 结论摘要：
  1. **Apple5 全黑屏根因明确**：通过全帧 `[present-probe]` 与 `TOTK_DRAW_LOG=1` 实测，问题 **不在** `bright_lut`、`present_encode()` 上屏拷贝或 `CAMetalLayer`（`dr.texture` 已忠实收到游戏源图拷贝与绿色 HUD/FPS 叠加文本）；在启动 Logo 及首段过渡期（`frame=0..119`），游戏提交给 `present` 的源纹理 `src` 本身全帧 RGB 均为 `(0,0,0)`（`src_nz = 0/2073600` 与 `0/921600`，`frame=119` 时仅 Alpha 变为 `170`），紧随其后在刚准备提交首批后序场景着色器时被离散绑定/Metal 4 拦截中止。
  2. **`cube-merge` 与全局 `MTL4*` 清理完成并实机验证通过**：`nvn_cube_group.h` 走 `enc_b_copy_texture` 回退路径；`botw_finish.h` 与 `totk_finish.h` 已补齐 classic Metal 离散绑定；`nvn_rtex.inc` 已在 Apple5 回退 `enc_b_copy_texture`；`totk_fxaa_quality.h` 与 `totk_smaa.h` 在 iOS BOTW 构建条件下原本即被 `#if TARGET_OS_OSX && !defined(GAME_BOTW)` 禁用；全仓剩余可达 `MTL4*` 调用已完成普查与处置。
  3. **额外解决 Run 1 暴露的 `Cube -> CubeArray` 视图失配**：`tex_type_view` 与 `encode_args` 支持 6 切片 `Cube <-> CubeArray` 视图转换，Run 2 实机已越过 `2ab186d4...` 阻塞点，`[shader-predict]` 推进至 `scan 6`，设备端离散着色器缓存扩充至 247 项（`devicectl device info files` 输出行数，较 Step B7 的 205 项新增 42 项）。
  4. **显存水位与 `unp=1` 根因确认**：`[tex-mem]` 实测 `ser=5073`（`fmt=0x46`, `1024x1024 x 83 layers x 11 mips`）因 `astc_on()` 对 `g_present_apple5` 关闭了 `TOTK_BC1_ASTC`，触发 CPU 解压（`unp=1, ubpp=4 -> mtl_pf=71 RGBA8`），单张纹理常驻分配 **443.5 MB**，设备累计已分配 **1440.6 MB**。
  5. **下一确定性阻塞点**：`nvn_mtl.m#L14908-L14909` 的离散绑定检查点尚未放行 `NVN_RB_SUPPORT`（`binding kind 7`），命中 `08fe2506..._b` 时按设计 `MDIE` 中止。

---

## 1. Step B8 源码修改明细（未执行 `git commit`）

1. **`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_cube_group.h`**
   - `cg_copy_kernel()`（`L127`）：增加 `if (g_present_apple5) return 0;`，强制跳过 `cg_table()`（`MTL4ArgumentTableDescriptor`）并使用其既有的 `enc_b_copy_texture` 逐面 blit 回退路径。
2. **`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/botw_finish.h`**
   - `bfin_pass()`（`L54-L69`）：新增 `if (g_present_apple5)` 分支，使用 `enc_c_begin()` classic `MTLComputeCommandEncoder` 与离散绑定（`setComputePipelineState:` / `setBuffer:offset:atIndex:` / `setTexture:atIndex:` / `dispatchThreadgroups:threadsPerThreadgroup:` / `enc_c_end()`）保留完整合成/输出路径，避免调用 `MTL4ArgumentTable` 与 `barrierAfter*`。
3. **`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/totk_finish.h`**
   - `fin_pass()`（`L66-L79`）：同上，为 `g_present_apple5` 增加 classic Metal 离散计算编码分支，保持原后处理/拷贝输出不断链。
4. **`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_rtex.inc`**
   - `rx_setup()`（`L73`）：新增 `if (g_present_apple5) return 0;`，使渲染目标拷贝回退到上层 `enc_b_copy_texture` 显式 blit，避免加载包含 `MTL4ArgumentTableDescriptor` 的计算拷贝内核。
5. **`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`**
   - **显存水位统计（`[tex-mem]`, `L5968-L5975`, `L5996-L6007`）**：记录累计 `MTLTexture` 分配字节数及 `> 32 MiB` 大纹理明细（含 NVN `fmt`、`tgt`、`layers`、`mips`、`mtl_pf`、`tca`、`tck`、`unp(ubpp)`、`total`、`mt_alloc`、`large_total`、`dev_alloc`）。
   - **`Cube <-> CubeArray` 视图兼容（`L12971-L12982`, `L15061-L15070`）**：`tex_type_view()` 在目标类型为 `MTLTextureTypeCube` / `MTLTextureTypeCubeArray` 时使用 `slices:NSMakeRange(0, 6)`（否则 `1`）；`encode_args()` 离散路径同时识别 `Cube <-> CubeArray` 类型失配并生成对应类型视图。
   - **全帧上屏像素探针（`[present-probe]`, `L23132-L23192`）**：在 `present_classic_submit()` 中，在 `present_encode()` 之后、提交回调内通过 `MTLBlitCommandEncoder` 将整帧 `src`（`src.width x src.height x 4`）与 `dr.texture`（`dr.texture.width x dr.texture.height x 4`）回读到共享缓冲（采样帧 `pn == 1, 2, 10, 30, 60` 及每 60 帧），统计全帧非零 RGB 像素数与通道最大值。

---

## 2. 黑屏根因核查与 `[present-probe]` / `[draw]` 实测证据

### 2.1 `bright_lut` 短路影响核查
- `nvn_mtl.m#L20682` 的 `bright_lut()` 仅针对着色器键 `"552f45c348a61b86"`（游戏世界色彩增强 3D LUT）进行 GPU 查表/亮度调整；主画面合成与 `present_encode()`（`totk_hdr_present.h` / `nvn_mtl.m#L22469`）完全不采样 `bright_lut` 输出纹理。因此 `if (g_present_apple5) return;` **并非黑屏原因**。

### 2.2 Apple5 `present` 上屏链路与 `CAMetalLayer` 核查
- `frontend_ios.m` 设置 `metalLayer.pixelFormat = MTLPixelFormatBGRA8Unorm` 与 `metalLayer.framebufferOnly = NO`，并在首次呈现时隐藏 `bootOverlay`。
- `present_classic_submit()` 正常等待 `producer_fence`，通过 `present_encode(cb, src, dr.texture, crop, slot, 1)` 建立 classic `MTLRenderCommandEncoder`、绑定 `g_blit_pso` / `g_blit_samp`、绘制源纹理并叠加 HUD，最终呈现 `dr`。

### 2.3 全帧 `[present-probe]` 与 `TOTK_DRAW_LOG=1` 实测数值
`/tmp/botw-ios-step-b8/device-log-run2.txt` 原始记录：
```text
210: [dm] [nvn-mtl] [draw] #1 vs=e4b5b0967a3cd0b34a651ab97dd905f69265b56fc2adfbe154fcfb65b4287e76-c8b9a76304b7_b ps=ecb4242efaf4042be5db42db3b06b93d16e1087c47db79882dc534df9e646f73-915b3e91ee53_b rt0=0x115172800 1920x1080 fmt=0x25
211: [dm] [nvn-mtl] [draw]   ps slot 0 handle 0x102002fa tid 762 ok 1920x1080 fmt=0x3d tgt=1 layout=0 mem=0x136a2fe00 ser=2788
222: [dm] [nvn-mtl] [present-probe] pn=1 frame=0 src=1920x1080 fmt=70 src_nz=0/2073600 max=(0,0,0,0) dst=1280x894 dst_nz=10362/1144320 max=(120,255,80,255)
230: [dm] [nvn-mtl] [present-probe] pn=2 frame=1 src=1280x720 fmt=70 src_nz=0/921600 max=(0,0,0,0) dst=1280x894 dst_nz=10164/1144320 max=(120,255,80,255)
252: [dm] [nvn-mtl] [present-probe] pn=10 frame=9 src=1280x720 fmt=70 src_nz=0/921600 max=(0,0,0,0) dst=1280x894 dst_nz=10274/1144320 max=(120,255,80,255)
303: [dm] [nvn-mtl] [present-probe] pn=30 frame=29 src=1280x720 fmt=70 src_nz=0/921600 max=(0,0,0,0) dst=1280x894 dst_nz=10274/1144320 max=(120,255,80,255)
324: [dm] [nvn-mtl] [present-probe] pn=60 frame=59 src=1280x720 fmt=70 src_nz=0/921600 max=(0,0,0,0) dst=1280x894 dst_nz=10274/1144320 max=(120,255,80,255)
325: [totk] game finished startup logo
334: [dm] [nvn-mtl] [present-probe] pn=120 frame=119 src=1280x720 fmt=70 src_nz=0/921600 max=(0,0,0,170) dst=1280x894 dst_nz=10274/1144320 max=(120,255,80,255)
```
- **判定（属于 B 类，非上屏/图层故障）**：
  1. `dr.texture`（`dst`）在全帧扫描下呈现 `10164–10362 / 1144320` 个非零 RGB 像素，最大值为 `(120, 255, 80, 255)`，对应叠加在 `dr.texture` 上的绿色 HUD 信息文字；且 Run 1 中心 `64x64` 探针已确认在 `frame=119` 时 `dst` Alpha 同步复制为 `170`。说明 `present_encode()` 与 `CAMetalLayer` 上屏完全工作。
  2. 相反，游戏提交的 `src` 在整张 `1920x1080`（`2,073,600` 像素）和 `1280x720`（`921,600` 像素）全范围内，第 0～119 帧 RGB **全为 0**（`src_nz = 0`）。
  3. 结合 `TOTK_DRAW_LOG=1`（`draw #1..#40`）与反查出的 `draw_ps.metal`：该阶段每次绘制均采样 `ps slot 0` 线性纹理（`layout=0`, `fmt=0x3d`, `1280x720`, `tid=762/772`）直接写入 `rt0`（`1920x1080`, `fmt=0x25`）。在 `startup logo` 阶段结束（`L325`）进入 `frame=119` 后，`src` Alpha 转为 `170`，游戏随即将首批 3D/过渡场景提交至队列并触发后序着色器检查点（尚未来得及输出首帧亮画面）。

---

## 3. 全局 `MTL4` / `barrierAfter` / `argumentTable` 符号普查与处置表

检索范围：`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/` 与 `ios/`。

| 模块 / 文件 | 涉及符号 / 用途 | Apple5 可达性 | 处置方案与状态 |
|---|---|---|---|
| `nvn_cube_group.h` | `MTL4ArgumentTableDescriptor` (`cg_table`) | 可达（14s 触发点） | **已修复**：`cg_copy_kernel` (`L127`) 在 `g_present_apple5` 返回 `0`，回退 `enc_b_copy_texture`（实机验证通过） |
| `botw_finish.h` | `MTL4ArgumentTable` / `MTL4ComputeCommandEncoder` / `barrierAfter` | 后处理/合成阶段可达 | **已修复**：`bfin_pass` (`L54-L69`) 新增 `g_present_apple5` classic 离散绑定路径 |
| `totk_finish.h` | `MTL4ArgumentTable` / `MTL4ComputeCommandEncoder` / `barrierAfter` | 后处理/合成阶段可达 | **已修复**：`fin_pass` (`L66-L79`) 新增 `g_present_apple5` classic 离散绑定路径 |
| `nvn_rtex.inc` | `MTL4ArgumentTableDescriptor` (`rx_setup`) | 渲染纹理拷贝阶段可达 | **已修复**：`rx_setup` (`L73`) 在 `g_present_apple5` 返回 `0`，回退 `enc_b_copy_texture` |
| `totk_fxaa_quality.h`, `totk_smaa.h` | `MTL4*` | **不可达** | 原文件已有 `#if TARGET_OS_OSX && !defined(GAME_BOTW)` 隔离（iOS + BOTW 不编译执行） |
| `totk_taa.m/.h`, `totk_mfx.m/.h` | `MTL4*` / `barrierAfter*` | **不可达** | iOS 下 MetalFX / TAA 关闭（`device-log-run2.txt#L209, L214-L215`: `off`） |
| `totk_hdr_shadow.h`, `totk_hdr_render.h` | `MTL4*` | **不可达** | `frontend_ios.m` 仅启用 SDR `BGRA8Unorm` 呈现 |
| `nvn_pso4.h`, `nvn_pso4_build.h`, `nvn_pso4_fn.h`, `nvn_render_stream.h`, `nvn_archive_map.h` | `MTL4*` 管线与流编码 | **不可达** | `g_present_apple5` 下走 `enc_r_begin`/`enc_c_begin`/classic PSO 路径，不走 PSO4/MTL4Stream |
| `nvn_mesh_profile.inc`, `nvn_mesh_probe.inc`, `nvn_mesh_batch*.inc`, `nvn_mesh_tasks.inc` | 网格着色器 `MTL4*` | **不可达** | `OOT_IOS_MESH_COMPILE=0`，iOS/Apple5 禁用 mesh shader |
| `nvn_dispatch_profile.inc`, `nvn_*_probe.h`, `nvn_raw_write_domain.inc`, `nvn_geometry_*.inc`, `nvn_gpu_error.h` | 诊断探针与仅 macOS/MTL4 路径 | **不可达** | 默认开关关闭或仅被 MTL4 执行路径调用 |
| `nvn_stor_migrate.h`, `nvn_stor_alias.h` | `blit()` 封装调用 `enc_b_copy_texture` / 注释 | 安全 | `blit()` 在 Apple5 自动创建 classic `MTLBlitCommandEncoder` |
| `nvn_mtl.m` | 核心后端（`enc_r_begin`, `enc_c_begin`, `discrete`, `present_classic_submit` 等） | 可达主路径 | 已全量按 `!g_present_apple5` / `discrete` / `classic` 分流 |

---

## 4. 显存水位（`[tex-mem]`）与 `unp=1` 格式分析

真机 Run 2 日志（`device-log-run2.txt#L342-L343`）：
```text
342: [dm] [nvn-mtl] [tex-mem] ser=5073 fmt=0x46 tgt=4 1024x1024 layers=83 mips=11 mtl_pf=71 tca=0 tck=0 unp=1(ubpp=4) total=442.7MB mt_alloc=443.5MB large_total=443.5MB dev_alloc=1440.6MB
343: [dm] [nvn-mtl] [upload] chunked shared staging for large texture 1024x1024 layers=83 mips=11 total=0x1baaaa3c
```
- **触发 `unp=1` 的具体 NVN 格式码与条件分支**：
  - `fmt=0x46` 为 `BC1_sRGB` 压缩纹理，`tgt=4` 为 2D 纹理数组（`1024x1024 x 83 layers x 11 mips`）。
  - `nvn_mtl.m#L4814-L4821` 的 `astc_on()` 逻辑为 `v = !g_present_apple5 && no_bc() && ...`，即当前在 `g_present_apple5` 下强制关闭了 `BC1 -> ASTC` GPU/离线转码（`tca=0, tck=0`）。
  - 因 A12Z/iOS 不开放原生 BC 硬件解码（`no_bc()` 为真）且 ASTC 转码被关闭，该纹理落入 CPU 解压分支 `unp=1`，以 `ubpp=4` 解码为 `mtl_pf=71`（`MTLPixelFormatRGBA8Unorm` / `_sRGB` 级别 4B/像素）常驻显存，导致单张纹理占用 **443.5 MB**（`dev_alloc=1440.6 MB`）。在 6 GB 物理内存 + `increased-memory-limit` 环境下需持续关注后续此类大数组累积导致的 Jetsam 风险。

---

## 5. 本地单元测试、编译、指令检查与真机校验记录

### 5.1 本地单元测试（5/5 OK）
```text
$ TOTK_DISCRETE_MSL_DIR=/tmp/totk-abi-e2e/ios-metal-iphoneos-20261120 python3 -m unittest \
  upstream.tests.test_discrete_native_draw \
  upstream.tests.test_discrete_buffer_binding \
  upstream.tests.test_discrete_texture_sampler_binding \
  upstream.tests.test_native_main_encoders -v

test_discrete_compute_ssbo_drvcb_tex_sampler_binding (upstream.tests.test_discrete_native_draw.DiscreteNativeDraw.test_discrete_compute_ssbo_drvcb_tex_sampler_binding) ... ok
test_native_draw_semantics_with_title_vertex_shader (upstream.tests.test_discrete_native_draw.DiscreteNativeDraw.test_native_draw_semantics_with_title_vertex_shader) ... ok
test_production_binder_with_title_vertex_shader (upstream.tests.test_discrete_buffer_binding.DiscreteBufferBinding.test_production_binder_with_title_vertex_shader) ... ok
test_title_texture_sampler_bindings_and_sampling (upstream.tests.test_discrete_texture_sampler_binding.DiscreteTextureSamplerBinding.test_title_texture_sampler_bindings_and_sampling) ... ok
test_production_native_clear_upload_and_alias_fences (upstream.tests.test_native_main_encoders.NativeMainEncoders.test_production_native_clear_upload_and_alias_fences) ... ok

----------------------------------------------------------------------
Ran 5 tests in 4.645s

OK
```

### 5.2 iOS Release 编译、指令检查、签名与安装前后数据保留
- 编译：`xcodebuild -project ios/TOTKIOS.xcodeproj -scheme TOTKIOS -configuration Release -destination generic/platform=iOS -derivedDataPath build/ios-derived-b2 CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=` -> `** BUILD SUCCEEDED **`。
- `nvn_mtl.o`（`5.4M`）指令扫描：`nvn_mtl_udot=0`。
- 签名验证（`/tmp/botw-ios-step-b8/signed/TOTK.app`, `org.macnativegames.botw.a12z`）：`STAGE_SIGN_OK`。
- 增量覆盖安装至 `<YOUR_IPAD_UDID>`（`databaseSequenceNumber: 2492`）前后校验：
  ```text
  23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774  /tmp/botw-ios-step-b8/pre-romfs.sha256
  23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774  /tmp/botw-ios-step-b8/post-romfs.sha256
  189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9  /tmp/botw-ios-step-b8/pre-shader-seed.txt
  189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9  /tmp/botw-ios-step-b8/post-shader-seed.txt
  DATA_PRESERVED_OK
  ```

---

## 6. 实机推进与下一阻塞点分析

Run 2 实机启动（`TOTK_DEBUG_LOG=1 TOTK_START_SCREEN=0 TOTK_DRAW_LOG=1`）已完全越过 Step B7 的 `MTL4ArgumentTableDescriptor`（`cube-merge`）及 Run 1 的 `Cube -> CubeArray`（`2ab186d4...`）阻塞点，并完成 `scan 5`（1728 命令）与 `scan 6`（320 命令），设备端 `Documents/shader-cache/rt-totk-native-v9-unverified.discrete-v1` 文件条目升至 **247**。

日志末尾停在下一处离散绑定检查点（`device-log-run2.txt#L350-L364`）：
```text
350: [dm] [nvn-mtl] FATAL discrete shader 08fe25061d583637b4746ae37cad9d79176fe008756134ac5f0cad048948fc50-137a646d935d_b binding kind 7 is outside the CBUF/C1/TEX/SAMPT checkpoint
```
对照从设备拉取的 `/tmp/botw-ios-step-b8/kind7_err.json` 与 `nvn_mtl.m#L14908-L14909, L15215-L15219`：
- 该着色器声明了 `support_buffer`（`"kind": "support"`, `"metal_slot": 0`, 即 `NVN_RB_SUPPORT = 7`）。
- `encode_args()` 实际上已实现 `if (discrete) { discrete_bind_buffer(en, nvn_stage, b, sb, 0); USE(sb, MTLResourceUsageRead); continue; }`（`L15215-L15218`），但被上方旧的检查点 `if (discrete && b->kind != NVN_RB_CBUF && b->kind != NVN_RB_C1 && b->kind != NVN_RB_TEX && b->kind != NVN_RB_SAMPT)`（`L14908-L14909`）提前拦截。

**Step B9 建议方向**：在 `encode_args()` 检查点中按已有离散实现逐步放行 `NVN_RB_SUPPORT` / `NVN_RB_DRV0` / `NVN_RB_DRV2` / `NVN_RB_SBUFP` / `NVN_RB_SBUFU` / `NVN_RB_UBOP` / `NVN_RB_IMG` 等已支持类型，推进至实际 3D 场景渲染并复测非零画面输出。
