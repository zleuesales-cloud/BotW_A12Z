# Step B5: Apple5 渲染阻塞修复、构建校验与真机首次出图报告

- 测试日期：2026-11-21
- 目标设备：`iPad8,9`（iPad Pro 2020, Apple A12Z GPU / Apple5 家族, UDID `<YOUR_IPAD_UDID>`）
- 目标 Bundle ID：`org.macnativegames.botw.a12z`（Team ID / 签名证书 SHA1 已脱敏）
- 结论摘要：**完成首帧呈现与启动 Logo，推进到 Logo 后首个块压缩渲染目标回写点**：
  1. 修复 Step B4 暴露的 3 项真机阻塞（`clear_tex`、`services.c` `ALIAS_MIN_GIB=1ULL`、`main.m` `Documents/GameData/aoc` 自动回退）及 `copy_t2t_astc` / `copy_t2t_draw` / `bloomq_capture` / `enc_r_begin` Apple5 防护。
  2. **无需传入 `TOTK_ALIAS_GIB` 或 `TOTK_AOC` 环境变量**，自动完成 1 GiB 别名区保留与 7,094 个 DLC 资源挂载，通过 `clear_tex`。
  3. 真机运行期间成功在 `2.127s` 呈现 **首帧 (`[totk-boot] 2.127 first presented frame`)**，连续呈现并完成至少 **9 帧**（GPU 耗时 `0.2–0.4 ms`），运行时 `totk_xlate` 即时翻译了 **8 个 `discrete-v1` 着色器**（缓存目录写入 135 个文件），并走完启动 Logo（`[totk] game finished startup logo`）。
  4. 启动 Logo 之后命中下一处明确阻塞点：`blk_decode`（`nvn_mtl.m#L6952-L6976`）在向压缩格式 `0x49`（通过视图格式 `0x2c` 经 `8x8` 辅助块贴图）渲染结束后，尝试创建 `MTL4ArgumentTableDescriptor` 并报红中止：`FATAL block decode argument table: Device does not support Metal 4.`

---

## 1. Step B5 源码修改明细（仅仓库内修改，未执行 `git commit`）

### 1.1 `upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`

- **`enc_r_begin`（`L2212-L2221`）**：
  - 在 `if (g_present_apple5)` 分支调用 `[g_cb renderCommandEncoderWithDescriptor:(MTLRenderPassDescriptor *)d]` 前新增运行时描述符类型校验：
    `if (![(id)d isKindOfClass:MTLRenderPassDescriptor.class]) MDIE("Apple5 enc_r_begin received non-MTLRenderPassDescriptor (%s)", object_getClassName((id)d));`（`L2216`）。
  - 保留函数签名与自包含函数体，确保 `test_native_main_encoders.py` 提取正常。
- **`bloomq_capture`（`L17571-L17573`）**：
  - 入口增加 `if (g_present_apple5) return 0;`（`L17572`），避免 Apple5 触及 `MTL4RenderPassDescriptor` 与 `g_bloomq_args`。
- **`copy_t2t_astc`（`L21188-L21215`）**：
  - 仅在 `!g_present_apple5` 时创建 `MTL4ArgumentTable`（`L21193`），并将管线失败判断改为 `if (!ps || (!g_present_apple5 && !at))`（`L21194`）。
  - 计算派发分支（`L21203-L21211`）：在 `g_present_apple5` 下改为通过 `enc_c_begin(NULL)` 获取 classic `id<MTLComputeCommandEncoder>`，直接绑定 `[ce setTexture:sv atIndex:0]`、`[ce setTexture:dv atIndex:1]`、`[ce setBuffer:pb offset:0 atIndex:0]`、`[ce setComputePipelineState:ps]` 与 `[ce dispatchThreadgroups:... threadsPerThreadgroup:...]`，随后 `enc_c_end(ce)`。
- **`copy_t2t_draw`（`L21237-L21313`）**：
  - 仅在 `!g_present_apple5` 时创建 `MTL4ArgumentTable`（`L21257-L21258`），并在 `L21261` 使用 `(!g_present_apple5 && !at)` 检查。
  - 在 `for (int z = 0; z < nz; z++)` 内（`L21283-L21305`）：在 `g_present_apple5` 下分配 `[MTLRenderPassDescriptor renderPassDescriptor]`，经 `enc_r_begin((MTL4RenderPassDescriptor *)rp)` 获取 classic `id<MTLRenderCommandEncoder>`，直接绑定 `[en setFragmentTexture:sv atIndex:0]`、`[en setFragmentSamplerState:samp[!!linear] atIndex:0]`、`[en setFragmentBuffer:pb offset:po atIndex:0]`，绘制后调用 `enc_r_end(en); es_reset();`；非 Apple5 保留原 Metal 4 分支不变。
- **`clear_tex`（`L21422-L21433`）**：
  - `L21423` 将 `[MTL4RenderPassDescriptor new]` 替换为：
    `MTL4RenderPassDescriptor *rp = g_present_apple5 ? (id)[MTLRenderPassDescriptor renderPassDescriptor] : [MTL4RenderPassDescriptor new];`

### 1.2 `upstream/games/totk/runtime/services.c`

- **`ALIAS_MIN_GIB`（`L36-L41`）**：
  - 将 iOS (`TARGET_OS_IPHONE`) 的 `#define ALIAS_MIN_GIB 4ULL` 修改为 `#define ALIAS_MIN_GIB 1ULL`（`L38`），使无 `extended-virtual-addressing` 的 6 GB 物理 iPad（如 A12Z）无需额外设置 `TOTK_ALIAS_GIB=1`，启动时可从 16 GiB 向下探测并选中 1 GiB 别名区（实机日志 `alias 0x7130000000+0x40000000`）。

### 1.3 `upstream/games/totk/ios/Sources/main.m`

- **`TOTK_AOC` 自动回退（`L769-L772`）**：
  - 若 App 包内 `aoc` 不存在，则检查沙盒 `[docs stringByAppendingPathComponent:@"GameData/aoc"]`（`L770-L772`），存在时以 `overwrite=0` 调用 `setenv("TOTK_AOC", ..., 0)`；包内 `aoc` 检查同样改为 `overwrite=0`，保留命令行环境变量覆盖能力。

---

## 2. 本地单元测试原始输出

运行命令与原始输出：
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
Ran 5 tests in 4.292s

OK
```

---

## 3. iOS Release-iphoneos 编译与符号检查

- 同步 `upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m` 与 `upstream/games/totk/ios/Sources/main.m` 到 `/tmp/botw-ios-step-b2/build-tree`（其中 `build-tree/runtime` 为指向仓库 `upstream/games/totk/runtime` 的符号链接，已直接读取修改后的 `services.c`）。
- 构建命令：
  ```bash
  cd /tmp/botw-ios-step-b2/build-tree && xcodebuild \
    -project ios/TOTKIOS.xcodeproj -scheme TOTKIOS -configuration Release \
    -destination generic/platform=iOS -derivedDataPath build/ios-derived-b2 \
    CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=
  ```
- 编译结果：`** BUILD SUCCEEDED **`（`nvn_mtl.o`、`main.o`、`services.o` 均重新编出）。
- 编译参数核验（`*.resp`）：确认包含 `'-mcpu=apple-a12'`、`'-DTOTK_DEVICE_XLATE=1'`、`'-DGAME_BOTW=1'`、`'-DGAME_BOTW190=1'`。
- 目标文件 `udot` 指令检查（`otool -tv`）：
  ```text
  nvn_mtl.o udot=0
  main.o udot=0
  services.o udot=0
  ```

---

## 4. 打包签名、增量覆盖安装与沙盒数据完整性校验

- 临时签名包路径：`/tmp/botw-ios-step-b5/signed/TOTK.app`（`CFBundleIdentifier = org.macnativegames.botw.a12z`，附带 `GameData/game-data`、`GameData/romfs/runtime-index.bin`、`GameData/shared-fonts`、`embedded.mobileprovision` 及原 entitlements，未打包 15 GB `romfs.bin.*` 与 1.3 GB `aoc`）。
- 签名验证：
  ```text
  /tmp/botw-ios-step-b5/signed/TOTK.app: valid on disk
  /tmp/botw-ios-step-b5/signed/TOTK.app: satisfies its Designated Requirement
  STAGE_SIGN_OK
  ```
- 覆盖安装结果：
  ```text
  App installed:
  • bundleID: org.macnativegames.botw.a12z
  • installationURL: file:///private/var/containers/Bundle/Application/ADB86CA7-34FB-4A3E-B127-5DB70FA6718D/TOTK.app/
  • databaseUUID: 06424394-F6DA-4EDE-80DD-7FA45644EB68
  • databaseSequenceNumber: 2456
  ```
- 安装前后设备端 `Documents/GameData/romfs.sha256` 与 `Documents/GameData/shader-seed.txt` 字节级校验：
  ```text
  23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774  /tmp/botw-ios-step-b5/pre-romfs.sha256
  23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774  /tmp/botw-ios-step-b5/post-romfs.sha256
  189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9  /tmp/botw-ios-step-b5/pre-shader-seed.txt
  189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9  /tmp/botw-ios-step-b5/post-shader-seed.txt
  DATA_PRESERVED_OK
  ```

---

## 5. 真机实测启动与核心日志摘要

### 5.1 启动命令与退出状态

仅传 `TOTK_DEBUG_LOG=1` 与 `TOTK_START_SCREEN=0`（**未传 `TOTK_ALIAS_GIB` 和 `TOTK_AOC`**）：
```text
$ xcrun devicectl device process launch --terminate-existing --console \
  --device <YOUR_IPAD_UDID> \
  --environment-variables '{"TOTK_DEBUG_LOG":"1","TOTK_START_SCREEN":"0"}' \
  org.macnativegames.botw.a12z

Launched application with org.macnativegames.botw.a12z bundle identifier.
Waiting for the application to terminate…
[totk-gfx] [nvn] 空设备：名字 739，已实现 440（语义 437 + 空实现 3），其余 299 个接报红桩
App terminated due to signal 6.
```

### 5.2 设备 `Documents/log.txt` 关键推进里程碑（共 270 行）

日志已拉取至 `/tmp/botw-ios-step-b5/device-log.txt`：
- **自动内存与资源挂载全部通过**：
  - L29：`[totk] guest layout: alias 0x7130000000+0x40000000 stack 0x7010000000+0x10000000 heap 0x7030000000 (cap 0x100000000)`
  - L35 / L106–108：`[botw] add-on content 1 ready: 7094 files, 1390370680 bytes`，`[botw] fs mount aoc: -> add-on content 1`
  - L43 / L109：`native read-only RomFS ready: 41062 files`，`shared fonts loaded: 6 fonts, 17408104 bytes`
- **Apple5 离散路径初始化与 ABI 隔离生效**：
  - L72：`[dm] [nvn-mtl] Apple5 without Metal4: native g_q execution and discrete-v1 shader binding ABI enabled`
  - L173 / L193：拒绝旧版 `shader-seed-apple5` 并启用进程内 `totk_xlate` 到 `Documents/shader-cache/rt-totk-native-v9-unverified.discrete-v1`
- **`clear_tex` 通过并完成首帧及 9+ 帧实际呈现**：
  - L199：`[dm] [nvn-mtl] [present-ring] 1 presents copied (no free slot 0 times); this one 1920x1080 at 0,0 of the 1920x1080 window image`
  - L204–208：`帧 1`、`帧 2`、`帧 3` 均完整执行了 NVN 提交、清屏、绘制与出图（`QueueSubmitCommands=1` / `QueuePresentTexture=1`）
  - L209–210, L230–231, L237–238, L245–246：先后即时翻译了 `translated 1..8`（共 4 组 VS/PS，8 个着色器）
  - L217：**`[totk-boot] 2.127 first presented frame`**
  - L218–224：`[totk-gfx] [长帧] 帧 2 .. 帧 9` 持续出图（GPU 耗时 `0.2–0.4 ms`）
  - L226：**`[totk] game finished startup logo`**
  - L251 / L254：进游戏后继续推进至并行预编译管线（单次提交 120 次与 292 次绘制）
- **设备端离散着色器缓存生成验证**：
  - 查询设备沙盒 `Documents/shader-cache/rt-totk-native-v9-unverified.discrete-v1` 返回 **135 个文件**（`.code` / `.spv` / `.res.json` / `_b.json` / `_b.metal` / `.variants`）。

### 5.3 下一处阻塞点精确定位（L256–L270）

日志末尾原始输出：
```text
256: [dm] [nvn-mtl] [totk] rendering into compressed texture format 0x49 through view format 0x2c via a 8x8 block texture
257: [dm] [nvn-mtl] FATAL block decode argument table: Device does not support Metal 4.
258: [totk] host abort, call stack:
...
263: 4   TOTK  0x0000000104d050ec blk_copy.cold.5 + 0
264: 5   TOTK  0x000000010494c444 enc_r_begin + 0
265: 6   TOTK  0x00000001049327b8 rp_end_from + 9204
266: 7   TOTK  0x000000010493d2c0 mtl_exec + 21276
```

根因核对（`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`）：
1. `rp_end_from`（`L7143`）在向块压缩纹理（格式 `0x49`）借助辅助贴图 `g_blkaux` 完成渲染后，调用 `blk_copy`（`L6978-L6991`）-> `blk_decode`（`L6950-L6977`）将块解压/写回目标贴图。
2. `blk_decode` 在 `L6952-L6954` 无条件调用 `[MTL4ArgumentTableDescriptor new]` 与 `[g_dev newArgumentTableWithDescriptor:ad error:&e]`，并触发 `MDIE("block decode argument table: %s", ...)`。
3. 此外，在 Metal 4 分支中 `blk_decode`（`L6968-L6974`）与同类 `blk_transcode`（`L6927-L6948`）共用统一 `id<MTL4ComputeCommandEncoder> e = blit()` 做 blit 与 compute dispatch，并直接调用 `barrierAfterEncoderStages`、`setArgumentTable:`、`gpuAddress` / `gpuResourceID`；而在 Apple5 下 `blit()` 返回经典 `id<MTLBlitCommandEncoder>`，不能直接在同一个编码器上执行计算派发。

---

## 6. Step B6 下一步修复建议

1. 改造 `blk_decode`（`nvn_mtl.m#L6950-L6977`）与 `blk_transcode`（`nvn_mtl.m#L6927-L6949`）的 Apple5 分支：
   - 仅在 `!g_present_apple5` 时创建 `MTL4ArgumentTable`。
   - `g_present_apple5` 下先用 `blit()` 执行 `enc_b_copy_texture_buffer` 拷入私有缓冲 `b`，随即 `enc_b_end(g_blit); g_blit = nil;` 关闭 blit 编码器，再用 `enc_c_begin(NULL)` 打开经典 `id<MTLComputeCommandEncoder>`，通过 `setBuffer:offset:atIndex:`、`setTexture:atIndex:`、`setComputePipelineState:`、`dispatchThreadgroups:threadsPerThreadgroup:` 调度 `bcd_pso()` / `bcd3_pso()` / `bc1astc_pso()` 并 `enc_c_end(ce)`；对 `blk_transcode` 则在 `enc_c_end(ce)` 之后重新取 `blit()` 执行 `enc_b_copy_buffer_texture`。
2. 顺带审计 `nvn_mtl.m` 中其余仍在运行期创建 `MTL4ArgumentTableDescriptor` 或对通用/探测编码器调用 `barrierAfter*Stages` 的孤立点（例如 `L17752`、`L24237` 等）并做好 Apple5 隔离。
