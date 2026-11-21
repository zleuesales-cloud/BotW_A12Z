# Step B7: 大贴图分片共享暂存改造与 `blk_decode` 真机闭环验证报告

- 测试日期：2026-11-21
- 目标设备：`iPad8,9`（iPad Pro 2020, Apple A12Z GPU / Apple5 家族, UDID `<YOUR_IPAD_UDID>`）
- 目标 Bundle ID：`org.macnativegames.botw.a12z`
- 结论摘要：**`pkg_upload_` 分片暂存与 Step B6 的 `blk_decode` 离散计算路径均一次验证通过**：
  1. `1024x1024 x 83 layers x 11 mips`（CPU 解码总计 `0x1baaaa3c` ≈ 442.7 MiB）纹理数组通过分片共享暂存成功上传。
  2. `blk_decode` 成功在 A12Z GPU 上将渲染到格式 `0x49`（`8x8` 辅助块贴图）的块解压写入目标格式贴图（`无 BC：渲染进 BC 贴图的块在 GPU 上解压进解压格式的贴图（首例 32x32 fmt 4）`）。
  3. 离散着色器缓存增至 **205 个文件**（新增 8 个着色器翻译 + `[shader-predict]` 扫描至第 9 次、命中 184 个 urgent draw）。
  4. 随后命中下一处孤立的 Metal 4 编码点：`nvn_cube_group.h` 中的 `cg_table()`（`cube-merge` 临时贴图组建立后创建 12 贴图 `MTL4ArgumentTableDescriptor`），触发 `FATAL cube-merge argument table: Device does not support Metal 4.`。

---

## 1. Step B7 源码修改明细（`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`，未执行 `git commit`）

1. **新增 `up_shared_alloc`（`L5940-L5960`）**：
   - 将池化 `8 MiB` 共享暂存块（`UP_SBLOCK_SIZE`）与大块独立共享分配逻辑抽取为统一辅助函数，内部调用 `up_begin()` 与 `up_cpu_reserve(sneed)`（超过 `g_up_batch_limit = 128 MiB` 时自动冲刷前序批次并开启新批）。
2. **改造 `pkg_upload_`（`L6096-L6163`）**：
   - 当 `sneed > UP_SBLOCK_SIZE` 时标记 `chunked = 1`，不再在循环外一次性向 Metal 申请整张贴图所需的单一共享缓冲。
   - 在 `(l, y)` 循环内部（`L6120-L6127`）按当前切片字节数 `slice_bytes`（ASTC / CPU 解码 / 原始块三路径各自计算）临时恢复 `runtime_fault_guard = cprev` 后调用 `stg = up_shared_alloc(slice_bytes, &off)`，再恢复 `&cjb`；首次触发时打印 `[upload] chunked shared staging for large texture ...`。
   - 记录 `seq0 = g_up_seq`，使 `sigsetjmp(cjb, 1)` 在跨批次 `up_flush()` 后能正确回退 `g_nup_blits = (g_up_seq == seq0) ? nb0 : 0`（`L6104-L6108`）。

---

## 2. 本地单元测试原始输出

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
Ran 5 tests in 4.416s

OK
```

---

## 3. iOS `Release-iphoneos` 编译与签名覆盖安装校验

- 构建结果：`** BUILD SUCCEEDED **`（`nvn_mtl.o` 大小 `5,688,840` 字节，`-mcpu=apple-a12`）。
- 目标文件指令检查：`nvn_mtl_udot=0`。
- 签名验证（`/tmp/botw-ios-step-b7/signed/TOTK.app`）：`STAGE_SIGN_OK`。
- 增量覆盖安装至 `<YOUR_IPAD_UDID>`（`bundleID: org.macnativegames.botw.a12z`, `databaseSequenceNumber: 2472`）。
- 安装前后设备端 `Documents/GameData/romfs.sha256` 与 `Documents/GameData/shader-seed.txt` 字节级校验：
  ```text
  23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774  /tmp/botw-ios-step-b7/pre-romfs.sha256
  23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774  /tmp/botw-ios-step-b7/post-romfs.sha256
  189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9  /tmp/botw-ios-step-b7/pre-shader-seed.txt
  189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9  /tmp/botw-ios-step-b7/post-shader-seed.txt
  DATA_PRESERVED_OK
  ```

---

## 4. 真机运行推进与新阻塞点精确定位

### 4.1 关键进展（`/tmp/botw-ios-step-b7/device-log.txt`，共 284 行）

- **完整跑过 10s+ 窗口**（进程在实机持续推进至 `09:16:36`，约 14 秒后中止）：
  - `L175–L196`：管线存档复用 6/6 条（含前轮未落盘的新管线、`pso_collected.tsv 20 行`），预热仅 147 ms。
  - `L218`：`[totk-boot] 2.163 first presented frame`；`L221–L231` 连续呈现 `帧 1..11`（GPU `0.4–0.6 ms`）；`L233` `[totk] game finished startup logo`；`L241` CoreAudio 5s 零欠载。
  - `L249`（**Step B7 分片上传验证通过**）：
    `[dm] [nvn-mtl] [upload] chunked shared staging for large texture 1024x1024 layers=83 mips=11 total=0x1baaaa3c`
  - `L254–L255`（**Step B6 `blk_decode` 离散计算验证通过**）：
    `[dm] [nvn-mtl] [totk] rendering into compressed texture format 0x49 through view format 0x2c via a 8x8 block texture`
    `[dm] [nvn-mtl] 无 BC：渲染进 BC 贴图的块在 GPU 上解压进解压格式的贴图（首例 32x32 fmt 4）`
  - `L258–L270`：`[shader-predict]` 扫描至第 9 次（1792 条命令，184 urgent），又即时翻译了 **8 个着色器**（`vs` x3, `ps` x5），设备端 `Documents/shader-cache/rt-totk-native-v9-unverified.discrete-v1` 文件数从 147 增至 **205 个**。

### 4.2 新阻塞点（`L262, L271–L284`）及全仓库剩余 `MTL4ArgumentTableDescriptor` 普查

日志末尾：
```text
262: [dm] [nvn-mtl] [cube-merge] 临时贴图组 1：16x16 格式 92，6 x 6 层
...
271: [dm] [nvn-mtl] FATAL cube-merge argument table: Device does not support Metal 4.
272: [totk] host abort, call stack:
...
278: 5   TOTK  0x00000001047a5810 cg_member_state + 0
279: 6   TOTK  0x00000001046f6288 cg_flush + 1452
280: 7   TOTK  0x0000000104715d80 mtl_exec + 7644
```

全目录 grep（`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/`）确认了以下包含 `MTL4ArgumentTableDescriptor` 的头文件/子模块（Step B8 建议一次性清理或离散化）：
1. **`nvn_cube_group.h`（`L119-L139`）**：`cg_copy_kernel()` 为立方体贴图合批加载/写回调用 `cg_table()`（`MTL4ArgumentTableDescriptor`）。注释已明示返回 `0` 即可自动回退至 `enc_b_copy_texture` 逐面 blit（`L125, L160-L163, L175-L178`），故 `if (g_present_apple5) return 0;` 即可立即解阻。
2. **`nvn_rtex.inc`（`L87-L88`）**、**`botw_finish.h`（`L55-L56`）**、**`totk_finish.h`（`L67-L68`）**、**`totk_fxaa_quality.h`（`L35-L36`）**、**`totk_smaa.h`（`L76-L79`）**：其余独立渲染/后处理辅助文件中的 `MTL4ArgumentTableDescriptor` 及配套 `MTL4*` 编码调用点，后续到达画面合成与后处理阶段时同样需要 Apple5 离散化或安全回退。

---

## 5. Step B8 下一步修复建议

1. 优先审查并修复 [nvn_cube_group.h](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_cube_group.h#L126-L139) 与其余后处理/收尾头文件（`botw_finish.h`、`nvn_rtex.inc`、`totk_fxaa_quality.h`、`totk_smaa.h`、`totk_finish.h`）中的 `MTL4ArgumentTable` / `MTL4RenderPassDescriptor` / `barrierAfter*` 调用点，让出图链路一次性具备通过启动后全景预热与画面合成的能力。
