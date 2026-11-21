# BOTW A12Z: Route B 补齐离散绑定与解除绘制拦截报告 (Step B2)

**执行时间**：2026-11-21  
**仓库根目录**：`~/Documents/NS/ipad pro/BotW_A12Z`（分支 `master`，未执行 `git commit`，未启动 Mac 游戏）  
**修改文件**：
* [nvn_mtl.m](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m)
* [test_discrete_native_draw.py](../../upstream/tests/test_discrete_native_draw.py)

---

## 1. 代码改动摘要（含准确行号）

### 1.1 扩展原生离散绑定器（Render + Compute Stage 5）
* **`discrete_bind_buffer`**（[nvn_mtl.m#L13439-L13454](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L13439-L13454)）：
  * 保持原单测提取所需的函数签名不变，并在函数体内联放行 `NVN_RB_CBUF`、`NVN_RB_C1`、`NVN_RB_SBUFP`、`NVN_RB_SBUFU`、`NVN_RB_UBOP`、`NVN_RB_DRV0`、`NVN_RB_DRV2`、`NVN_RB_SUPPORT`。
  * 按 `nvn_stage` 分发原生绑定：
    * `nvn_stage == 0`：`[(id<MTLRenderCommandEncoder>)en setVertexBuffer:buffer offset:offset atIndex:binding->metal_slot]`
    * `nvn_stage == 1`：`[(id<MTLRenderCommandEncoder>)en setFragmentBuffer:buffer offset:offset atIndex:binding->metal_slot]`
    * `nvn_stage == 5 || nvn_stage == 2`：`[(id<MTLComputeCommandEncoder>)en setBuffer:buffer offset:offset atIndex:binding->metal_slot]`
* **`discrete_bind_texture`**（[nvn_mtl.m#L13533-L13544](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L13533-L13544)）：
  * 增加 `nvn_stage == 5 || nvn_stage == 2` 计算阶段分支：`[(id<MTLComputeCommandEncoder>)en setTexture:texture atIndex:binding->metal_slot]`。
* **`discrete_bind_sampler`**（[nvn_mtl.m#L13545-L13556](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L13545-L13556)）：
  * 增加 `nvn_stage == 5 || nvn_stage == 2` 计算阶段分支：`[(id<MTLComputeCommandEncoder>)en setSamplerState:sampler atIndex:binding->metal_slot]`。

### 1.2 `driver_bank_gpu_off` 与 `encode_args` 离散直绑补齐
* **`g_dispatch_indirect` 与 `driver_bank_gpu_off`**（[nvn_mtl.m#L13804](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L13804)、[nvn_mtl.m#L13848-L13903](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L13848-L13903)）：
  * 扩展 `g_dispatch_indirect` 携带 `drv0_buf` 与 `drv0_off`，避免 classic compute encoder 开启后再重开 blit 编码器；
  * 新增 `driver_bank_gpu_off(..., size_t *offset_out)`（保留 `driver_bank_gpu` 兼容内联包装），在 Apple5 下使用 `c->buffer != nil` 判定驱动常量缓存命中并输出准确的 arena 字节偏移。
* **`encode_args` 阶段放行与资源直绑**（[nvn_mtl.m#L14786-L15180](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L14786-L15180)）：
  * 第 14786 行：将阶段检查放宽为允许计算阶段：`if (discrete && nvn_stage != 0 && nvn_stage != 1 && nvn_stage != 5)`。
  * 第 15043–15064 行（`NVN_RB_SBUFU`）：当 `discrete` 时，通过 `mtl_addr` 解析 `va` 对应的 `MTLBuffer + offset`；若未绑定、未映射、越界或非 4 字节对齐，则回退绑定 `dummy_buf()` 偏移 0（防止 GPU PageFault），调用 `discrete_bind_buffer(...)` 后 `continue`。
  * 第 15084–15111 行（`NVN_RB_SBUFP || NVN_RB_UBOP`）：当 `discrete` 时，解析 `S.ssbo[nvn_stage][reg]` / `S.ubo[nvn_stage][reg]`（对 `NVN_RB_UBOP` 同时检查 `upd_shadow_resolve` 与 `usnap_find`）；未映射/空地址时安全回退绑定 `dummy_buf()` 偏移 0，调用 `discrete_bind_buffer(...)` 后 `continue`。
  * 第 15155–15159 行（`NVN_RB_SUPPORT`）：绑定 `support_buf()` 偏移 0，调用 `discrete_bind_buffer(...)` 后 `continue`。
  * 第 15160–15179 行（`NVN_RB_DRV0 || NVN_RB_DRV2`）：按 `drv_read[bank]` 选择 `zero_bank()` 偏移 0 或 `driver_bank_gpu_off(bank, nvn_stage, &db, &dboff)`，调用 `discrete_bind_buffer(...)` 后 `continue`。

### 1.3 Apple5 Compute Dispatch 离散化与解除 Draw / Dispatch 拦截
* **`enc_c_begin` 与 `enc_c_end`**（[nvn_mtl.m#L2306-L2318](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L2306-L2318)）：
  * 移除 `enc_c_begin` 中的 Apple5 `MDIE`；在 `g_present_apple5` 下直接创建 classic `[g_cb computeCommandEncoder]` 并执行 `classic_fence_wait(g_cb, ce)`，跳过 MTL4 `barrierAfterQueueStages` 与 `setArgumentTable:g_ct`。
  * `enc_c_end` 在 `g_present_apple5` 下执行 `classic_fence_update(e); [e endEncoding]; return;`。
* **解除 `draw_inner()` 拦截**（[nvn_mtl.m#L19773](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L19773)）：
  * 移除原第 19692 行的 `if (g_present_apple5) MDIE("Apple5 NVN draw replay requires the discrete-v1 shader binder; refusing raw Tier2 argument bytes");`；
  * 保留所有未实现图元（`QUADS`、`QUAD_STRIP`、`POLYGON`、`8-bit index`、geometry scratch）的显式 `MDIE` 阻断。
* **离散化并解除 `dispatch()` 拦截**（[nvn_mtl.m#L20685-L20835](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L20685-L20835)）：
  * `disp_enc`（第 20686 行）添加 `if (g_present_apple5) MDIE(...)` 防止误入共享 `MTLBlitCommandEncoder` 路径；
  * 移除 `dispatch` 原第 20643 行 `MDIE("Apple5 NVN compute replay requires the discrete-v1 shader binder")`；
  * `dp->indirect` 在 `g_present_apple5` 下改用 `mtl_addr(dp->indirect, 12, ...)` 解析并校验 4 字节对齐；
  * 新增 `NVN_RB_IMG` 检查（第 20747–20753 行）：若计算着色器包含未移植的存储图像写（`NVN_RB_IMG`），调用 `RED_ONCE(201, ...)` 打印一次性告警并安全 `return;`；
  * `cs->builtin` 内嵌计算核在 `g_present_apple5` 下使用原生 `[ce setBuffer:offset:atIndex:]`（0/1/2 槽）与 `[ce dispatchThreadgroupsWithIndirectBuffer:indirectBufferOffset:threadsPerThreadgroup:]`；
  * 常规 compute 路径禁用 `reuse` 与 `pre`，若为 indirect dispatch 且读取 bank 0 驱动常量则在开启 compute encoder 前预填充 `g_dispatch_indirect.drv0_buf`；跳过 `dp_time_begin`/`dp_time_end`，indirect dispatch 走 classic `[ce dispatchThreadgroupsWithIndirectBuffer:indirectBufferOffset:threadsPerThreadgroup:]`。

---

## 2. 本地单元测试原始输出

### 2.1 全组离散绑定与原生编码器回归测试
执行命令：
```bash
TOTK_DISCRETE_MSL_DIR=/tmp/totk-abi-e2e/ios-metal-iphoneos-20261120 python3 -m unittest \
  upstream.tests.test_discrete_native_draw \
  upstream.tests.test_discrete_buffer_binding \
  upstream.tests.test_discrete_texture_sampler_binding \
  upstream.tests.test_native_main_encoders -v
```
原始输出：
```text
test_discrete_compute_ssbo_drvcb_tex_sampler_binding (upstream.tests.test_discrete_native_draw.DiscreteNativeDraw.test_discrete_compute_ssbo_drvcb_tex_sampler_binding) ... ok
test_native_draw_semantics_with_title_vertex_shader (upstream.tests.test_discrete_native_draw.DiscreteNativeDraw.test_native_draw_semantics_with_title_vertex_shader) ... ok
test_production_binder_with_title_vertex_shader (upstream.tests.test_discrete_buffer_binding.DiscreteBufferBinding.test_production_binder_with_title_vertex_shader) ... ok
test_title_texture_sampler_bindings_and_sampling (upstream.tests.test_discrete_texture_sampler_binding.DiscreteTextureSamplerBinding.test_title_texture_sampler_bindings_and_sampling) ... ok
test_production_native_clear_upload_and_alias_fences (upstream.tests.test_native_main_encoders.NativeMainEncoders.test_production_native_clear_upload_and_alias_fences) ... ok

----------------------------------------------------------------------
Ran 5 tests in 3.593s

OK
```

### 2.2 `test_discrete_native_draw.py` 独立执行输出（含 4 项绘制模式 + Stage 5 Compute SSBO/DRV0/TEX/SAMPT 验证）
执行命令：
```bash
TOTK_DISCRETE_MSL_DIR=/tmp/totk-abi-e2e/ios-metal-iphoneos-20261120 python3 -m unittest upstream.tests.test_discrete_native_draw -v
```
原始输出：
```text
test_discrete_compute_ssbo_drvcb_tex_sampler_binding (upstream.tests.test_discrete_native_draw.DiscreteNativeDraw.test_discrete_compute_ssbo_drvcb_tex_sampler_binding) ... ok
test_native_draw_semantics_with_title_vertex_shader (upstream.tests.test_discrete_native_draw.DiscreteNativeDraw.test_native_draw_semantics_with_title_vertex_shader) ... ok

----------------------------------------------------------------------
Ran 2 tests in 1.860s

OK
```

---

## 3. iOS Release-iphoneos 编译链接验证

### 3.1 构建环境与执行命令
将当前修改的 `nvn_mtl.m`、`nvn_copy_alias_probe.h`、`main.m` 与 `runtime`/`zeldaVR` 同步至临时构建树 `/tmp/botw-ios-step-b2/build-tree`，执行：
```bash
cd /tmp/botw-ios-step-b2/build-tree && xcodebuild -project ios/TOTKIOS.xcodeproj -scheme TOTKIOS -configuration Release -destination generic/platform=iOS -derivedDataPath build/ios-derived-b2 CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=
```

### 3.2 `nvn_mtl.m` 编译参数与产物摘要
从 `/tmp/botw-ios-step-b2/xcodebuild.log` 与 clang 响应文件 `e6072d4f65d7061329687fe24e3d63a7-common-args.resp` 采集到的真实编译参数：
* Target：`-target arm64-apple-ios26.0`
* SDK：`-isysroot /Applications/Xcode.app/Contents/Developer/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS27.2.sdk`
* CPU 目标与宏定义：`'-DTOTK_DEVICE_XLATE=1' '-mcpu=apple-a12' ... '-DGAME_BOTW=1' '-DGAME_BOTW190=1'`
* 最终链接与输出末尾日志：
```text
    /usr/bin/touch -c /tmp/botw-ios-step-b2/build-tree/build/ios-derived-b2/Build/Products/Release-iphoneos/TOTK.app

PruneExplicitPrecompiledModules /tmp/botw-ios-step-b2/build-tree/build/ios-derived-b2/Build/Intermediates.noindex/SwiftExplicitPrecompiledModules

PruneExplicitPrecompiledModules /tmp/botw-ios-step-b2/build-tree/build/ios-derived-b2/SDKExplicitPrecompiledModules

PruneExplicitPrecompiledModules /tmp/botw-ios-step-b2/build-tree/build/ios-derived-b2/Build/Intermediates.noindex/ExplicitPrecompiledModules

** BUILD SUCCEEDED **
```
* 产物 SHA-256：
  * `TOTK.app/TOTK`：`4f3de57dab5b472c9782044be8d8ceea47d92f5df492086d16927bfdc105eb32`
  * `Objects-normal/arm64/nvn_mtl.o`：`3510d4aa164a61885ae42455e04f167ffbb04b67eb29b6d9a5907a2086627dd4`

---

## 4. `nvn_mtl.o` 指令集与符号审计 (`otool` / `nm`)

### 4.1 `udot` 指令检查
执行命令：
```bash
otool -tV /tmp/botw-ios-step-b2/build-tree/build/ios-derived-b2/Build/Intermediates.noindex/TOTKIOS.build/Release-iphoneos/TOTKIOS.build/Objects-normal/arm64/nvn_mtl.o | grep -i udot | wc -l
```
原始输出：
```text
       0
```
**结论**：`udot` 指令数量为 **0**，满足 Apple A12Z (ARMv8.3-A / `-mcpu=apple-a12`) 指令集约束。

### 4.2 MTL4 / ArgumentTable / ResidencySet / `gpuAddress` 未定义符号与隔离情况
执行命令：
```bash
nm -u /tmp/botw-ios-step-b2/build-tree/build/ios-derived-b2/Build/Intermediates.noindex/TOTKIOS.build/Release-iphoneos/TOTKIOS.build/Objects-normal/arm64/nvn_mtl.o | grep -E "MTL4|ArgumentTable|ResidencySet|gpuAddress"
```
原始输出：
```text
_MTL4CommandQueueErrorDomain
_OBJC_CLASS_$_MTL4ArgumentTableDescriptor
_OBJC_CLASS_$_MTL4CommandAllocatorDescriptor
_OBJC_CLASS_$_MTL4CommandQueueDescriptor
_OBJC_CLASS_$_MTL4CommitOptions
_OBJC_CLASS_$_MTL4CounterHeapDescriptor
_OBJC_CLASS_$_MTL4RenderPassDescriptor
_OBJC_CLASS_$_MTLResidencySetDescriptor
_objc_msgSend$addResidencySet:
_objc_msgSend$gpuAddress
_objc_msgSend$newArgumentTableWithDescriptor:error:
_objc_msgSend$newMTL4CommandQueueWithDescriptor:error:
_objc_msgSend$newResidencySetWithDescriptor:error:
_objc_msgSend$setArgumentTable:
_objc_msgSend$setArgumentTable:atStages:
```
**`g_present_apple5` 运行时路径隔离核查**：
* `MTL4CommandQueueDescriptor` / `MTL4CommandAllocatorDescriptor` / `MTL4ArgumentTableDescriptor` / `MTLResidencySetDescriptor` / `newMTL4CommandQueueWithDescriptor:` / `newArgumentTableWithDescriptor:` / `newResidencySetWithDescriptor:`：仅在 `init_mtl` 的 `if (!g_present_apple5)` 分支调用（[nvn_mtl.m#L1165-L1225](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L1165-L1225)），Apple5 设备启动后完全跳过。
* `setArgumentTable:` / `setArgumentTable:atStages:`：Render 经由 `enc_r_begin` 的 `if (g_present_apple5)` 返回原生 `id<MTLRenderCommandEncoder>` 跳过；Compute 经由 `enc_c_begin` 的 `if (g_present_apple5)` 返回原生 `id<MTLComputeCommandEncoder>` 跳过，且 `disp_enc` 禁止 Apple5 进入。
* `addResidencySet:`：主初始化与上屏在 `g_present_apple5` 下跳过 `g_res` 与 `g_q4`；仅 `present` 中 `if (layer_residency)` 时对 `g_q` 做了防御判断（A12Z 上 `layer.residencySet` 为 `nil`）。
* `gpuAddress`：Apple5 主渲染与计算路径全部通过 `mtl_addr` 解析 `MTLBuffer + offset` 经原生 `set*Buffer:offset:atIndex:` 传入，不再依赖 `mtl_gpu_addr` 或 `slots[aid]` 参数表写入；但仍注意未在本次范围修改的启动着色器 ABI 选择（`totk_rt_set_binding_abi("discrete-v1")` 激活点）以及 `NVN_RB_IMG` 离散写绑定尚待后续步骤接入。
