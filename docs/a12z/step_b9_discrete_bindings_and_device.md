# Step B9: 全离散绑定类型放行与 A12Z 真机越过标题画面实测报告

- 测试日期：2026-11-21
- 目标设备：`iPad8,9`（iPad Pro 2020, Apple A12Z GPU / Apple5 家族, UDID `<YOUR_IPAD_UDID>`）
- 目标 Bundle ID：`org.macnativegames.botw.a12z`
- 结论摘要：**在未发生任何 GPU 错误、Metal 4 报错或 Jetsam 的情况下，真机在 50+ 秒观测窗口内稳定推进至 1531+ 帧并持续点亮整屏非零画面与背景音乐**：
  1. 解除 `encode_args()` 仅放行 `CBUF/C1/TEX/SAMPT` 的旧检查点，完整放行已有离散实现的 `NVN_RB_SUPPORT`、`NVN_RB_DRV0`、`NVN_RB_DRV2`、`NVN_RB_SBUFP`、`NVN_RB_SBUFU`、`NVN_RB_UBOP` 以及原生离散支持的 `NVN_RB_SAMP`、`NVN_RB_IMG`、`NVN_RB_TBUF`。
  2. 全帧 `[present-probe]` 实测游戏源纹理 `src` 从前 180 帧全黑，先过渡至载入图标（`src_nz=936..990`），再在 `pn=690..1320` 渐显（`src_nz` 由 `59,294` 攀升至 `332,206`），最终从 `pn=1350` 起达到 **整帧 100% 非零像素输出**：`src_nz = 921600/921600`（`max=(255,254,228,255)`）。
  3. 同步触发真实音频输出（`peak 0.3261`、`callback frames 3359744 / nonzero 1498462`、零欠载），3D 场景内部分辨率与 1280x720 多目标渲染（`640x360`、`160x90`、计算着色器与修补版边界采样着色器）均正常执行。
  4. 至第 1500 帧统计：帧时间 p50 **33.56 ms** / p95 **40.65 ms**，GPU p50 **2.35 ms** / p95 **4.45 ms**，`footprint 1567 MiB`，设备端离散着色器缓存扩充至 **2385 个条目**（含 300+ 运行时即时翻译，较 Step B8 Run 2 的 247 个增长逾 9 倍）。

---

## 1. Step B9 源码修改明细（`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`，未执行 `git commit`）

1. **`image_fast_rid()`（`L9611`）**
   - 保护 `gpuResourceID` 读取：`r->image_rid=g_present_apple5?0:image.gpuResourceID._impl;`。
2. **`discrete_bind_texture()`（`L13592`）**
   - 放行 `NVN_RB_IMG` 与 `NVN_RB_TBUF`：将校验从 `binding->kind != NVN_RB_TEX` 扩展为 `(binding->kind != NVN_RB_TEX && binding->kind != NVN_RB_IMG && binding->kind != NVN_RB_TBUF)`。
3. **`discrete_bind_sampler()`（`L13604`）**
   - 放行独立采样器绑定 `NVN_RB_SAMP`：将校验从 `binding->kind != NVN_RB_SAMPT` 扩展为 `(binding->kind != NVN_RB_SAMPT && binding->kind != NVN_RB_SAMP)`。
4. **`encode_args()` 离散检查点（`L14908-L14916`）**
   - 将检查点白名单更新为：
     `NVN_RB_CBUF`、`NVN_RB_C1`、`NVN_RB_TEX`、`NVN_RB_SAMPT`、`NVN_RB_SAMP`、`NVN_RB_IMG`、`NVN_RB_TBUF`、`NVN_RB_SBUFP`、`NVN_RB_SBUFU`、`NVN_RB_UBOP`、`NVN_RB_SUPPORT`、`NVN_RB_DRV0`、`NVN_RB_DRV2`。
   - 对纹理/采样器句柄表越界检查同步覆盖 `NVN_RB_SAMP` 与 `NVN_RB_TBUF`（`reg >= 128` 报错）。
5. **`NVN_RB_SAMP`（`L15021-L15027`）**
   - 移除 `if (discrete && b->kind != NVN_RB_SAMPT) MDIE(...)`，使独立采样器槽走已有 `S.samp[nvn_stage][reg] >> 20` -> `discrete_bind_sampler()`。
6. **`NVN_RB_IMG`（`L15086-L15099`）**
   - `discrete` 下跳过 `hdr.gpuResourceID` 与 `g_dummy_img_rid` 写入，并在 `!t` 回退为 `dummy_img()` 后调用 `discrete_bind_texture(en, nvn_stage, b, t); USE(t, MTLResourceUsageRead | MTLResourceUsageWrite); continue;`。
7. **`NVN_RB_TBUF`（`L15100-L15110`）**
   - `discrete` 下改用 `tex_fast(tid)` 解析缓冲贴图，跳过 `g_dummy_tbuf_rid`，并在 `!t` 回退为 `dummy_tbuf()` 后调用 `discrete_bind_texture(en, nvn_stage, b, t); USE(t, MTLResourceUsageRead); continue;`。
8. **`present_classic_submit()` `[present-probe]` 采样频率（`L23144`）**
   - 将常规采样周期从每 60 帧缩短为每 30 帧（`pn % 30 == 0`），细粒度捕捉启动后图像渐显过程。

---

## 2. 本地单元测试、编译、指令检查与签名安装校验

### 2.1 本地单元测试（5/5 OK）
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
Ran 5 tests in 4.368s

OK
```

### 2.2 Release 编译、`udot` 检查与覆盖安装前后数据校验
- 编译：`xcodebuild -project ios/TOTKIOS.xcodeproj -scheme TOTKIOS -configuration Release -destination generic/platform=iOS -derivedDataPath build/ios-derived-b2 CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=` -> `** BUILD SUCCEEDED **`。
- `nvn_mtl.o`（`5.4M`）：`nvn_mtl_udot=0`。
- 签名验证（`/tmp/botw-ios-step-b9/signed/TOTK.app`, `org.macnativegames.botw.a12z`）：`STAGE_SIGN_OK`。
- 增量覆盖安装至 `<YOUR_IPAD_UDID>`（`databaseSequenceNumber: 2500`）前后文件校验：
  ```text
  23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774  /tmp/botw-ios-step-b9/pre-romfs.sha256
  23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774  /tmp/botw-ios-step-b9/post-romfs.sha256
  189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9  /tmp/botw-ios-step-b9/pre-shader-seed.txt
  189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9  /tmp/botw-ios-step-b9/post-shader-seed.txt
  DATA_PRESERVED_OK
  ```

---

## 3. A12Z 真机 50+ 秒实测数据与画面里程碑（`/tmp/botw-ios-step-b9/device-log.txt`，共 683 行）

启动环境变量：`TOTK_DEBUG_LOG=1 TOTK_START_SCREEN=0`（50s 定时窗口满后进程仍在健康运行，主动停止并拉取日志）。

### 3.1 `[present-probe]` 全帧像素演化与音频/3D 里程碑
- **启动黑屏阶段（`pn=1..180`）**：`src_nz = 0`（`frame=119/149` Alpha 升至 `170/255`），对应开场黑帧。
- **载入与菜单前过渡（`pn=210..660`）**：
  - `pn=210..300`：`src_nz=933..936/921600 max=(251,250,225,255)`
  - `pn=390..510`：`src_nz=981..990/921600 max=(252,251,225,255)`
  - `pn=600..660`：`src_nz=930/921600 max=(248,247,222,255)`
- **标题画面渐显并播放实机音频（`pn=690..1320`）**：
  - `L508` (`pn=690`): `src_nz=59294/921600 max=(231,231,221,255)`
  - `L513`: `[totk-audio] output level: rms 0.0085 peak 0.0549 ... callback frames 1439744 / nonzero 168746`
  - `L518..L584` (`pn=870..1320`): `src_nz=324191..332206/921600 max=(253,253,253,255)`，`audio peak` 升至 `0.1028`。
- **整屏非零渲染与 3D 场景合成（`pn=1350..1530`）**：
  - `L585` (`pn=1350 frame=1351`): `src=1280x720 fmt=70 src_nz=921600/921600 max=(250,250,250,255) dst=1280x894 dst_nz=921600/1144320 max=(250,255,250,255)`
  - `L586`: `[totk-audio] output level: rms 0.0286 peak 0.3261 ... callback frames 2399232 / nonzero 1128234`
  - `L669` (`pn=1530 frame=1531`): `src=1280x720 fmt=70 src_nz=921600/921600 max=(255,254,228,255) dst=1280x894 dst_nz=921600/1144320 max=(228,255,255,255)`
  - `L670-L671`: `[dm] [nvn-mtl] 内部分辨率（合成采样的场景）：1280x720`、`output 1280x720, internal 1280x720`。

### 3.2 显存与性能遥测（第 1500 帧）
- `[tex-mem]`：
  - `L267`: `ser=5073 fmt=0x46 tgt=4 1024x1024 layers=83 mips=11 mtl_pf=71 unp=1(ubpp=4) mt_alloc=443.5MB large_total=443.5MB dev_alloc=1448.6MB`
  - `L317`: `ser=5074 fmt=0x42 tgt=4 512x512 layers=83 mips=10 mtl_pf=70 unp=1(ubpp=4) mt_alloc=111.5MB large_total=555.1MB dev_alloc=1821.1MB`
- 第 1500 帧统计（`L592-L598`）：
  - 帧时间：`p50 33.56 / p95 40.65 / max 44.54 ms`；NVN 提交+出图 `p50 3.38 ms`；GPU `p50 2.35 / p95 4.45 ms`。
  - 内存占用：`footprint 1567 MiB`（`内存池 1755 个 / 976 MB`，未触发 Jetsam）。
- 着色器翻译：`[shader] translated 300`，设备端 `Documents/shader-cache/rt-totk-native-v9-unverified.discrete-v1` 文件条目达 **2385**。

---

## 4. 下一步建议

Step B9 已无崩溃、无 Metal 4 报错、无 GPU 错误地穿过启动流程与 3D 标题界面（且首轮冷编译完成 300+ 着色器缓存沉淀）。建议后续按需要：
1. 移除诊断用的全帧回读探针 `[present-probe]`（或设为需环境变量触发，避免每 30 帧同步拷贝 `src` / `dr.texture` 带来带宽开销）。
2. 在开启真机触控/手柄交互后继续推进实机进入初始台地游玩，并进一步评估是否在 Apple5 上为超大数组纹理（`ser=5073`/`5074` 共 555.1 MB）重新校准 `astc_on()` / `unp=1` 策略以压低长期显存水位。
