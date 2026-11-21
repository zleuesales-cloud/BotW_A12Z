# Step B6: `blk_decode` / `blk_transcode` / `widen_u8_gpu` Apple5 离散化与真机推进报告

- 测试日期：2026-11-21
- 目标设备：`iPad8,9`（iPad Pro 2020, Apple A12Z GPU / Apple5 家族, UDID `<YOUR_IPAD_UDID>`）
- 目标 Bundle ID：`org.macnativegames.botw.a12z`
- 结论摘要：**游戏运行推进至 6.13 秒、第 122 帧（较 Step B5 的约 9 帧 / ~2.5s 大幅延长）**，完成管线存档复读、并行预编与音频零欠载连续播放，并在进入下一批次大规模贴图上传时触发了新的容量型阻塞点：`[dm] [nvn-mtl] FATAL shared upload staging 0x1baaaa3c`（`pkg_upload_`，单次向 Metal 申请约 442.7 MiB `MTLResourceStorageModeShared` 暂存缓冲返回 `nil`）。

---

## 1. Step B6 源码修改明细（`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`，未执行 `git commit`）

1. **`blk_transcode`（`L6927-L6962`）**：
   - `L6929-L6930`：仅在 `!g_present_apple5 && !at` 时创建 `MTL4ArgumentTableDescriptor`（`maxBufferBindCount = 3`）及 `MTL4ArgumentTable`。
   - `L6938-L6949`：新增 `g_present_apple5` 路径——先在 `blit()` 上执行 `enc_b_copy_texture_buffer` 将 `aux` 拷贝到私有缓冲 `b`，随即 `blit_close()`，调用 `enc_c_begin(NULL)` 获取 classic `id<MTLComputeCommandEncoder>`，直接绑定 `[ce setComputePipelineState:ps]`、`[ce setBuffer:b offset:0 atIndex:0]`、`[ce setBuffer:pb offset:po atIndex:1]`、`[ce setBuffer:ab offset:0 atIndex:2]` 并 `dispatchThreadgroups:threadsPerThreadgroup:`，随后 `enc_c_end(ce)`，再重开 `blit()` 执行 `enc_b_copy_buffer_texture` 写入 `w->dst`。
2. **`blk_decode`（`L6963-L7001`）**：
   - `L6966-L6967`：仅在 `!g_present_apple5 && !at` 时创建 `MTL4ArgumentTable`。
   - `L6981-L6989`：新增 `g_present_apple5` 路径——先用 `blit()` 执行 `enc_b_copy_texture_buffer` 拷入 `b`，调用 `blit_close()`，再用 `enc_c_begin(NULL)` 打开 classic `MTLComputeCommandEncoder` 并绑定 `b` / `pb` / `v` 调度 `bcd_pso()` 或 `bcd3_pso()`，最后 `enc_c_end(ce)`。
3. **`widen_u8_gpu_apple5` 与 `widen_indices_gpu`（`L17790-L17823`）**：
   - 新增 `widen_u8_gpu_apple5(id<MTLBuffer> sb, uint64_t soff, ...)`（`L17790-L17810`），使用 classic `id<MTLComputePipelineState>` 与 `enc_c_begin(NULL)` + `[ce setBuffer:...]` 完成 8-bit 索引 GPU 扩宽，不触碰 `gpuAddress` 与 `MTL4ArgumentTable`。
   - `widen_indices_gpu`（`L17819-L17820`）：`g_present_apple5` 时调用 `widen_u8_gpu_apple5((__bridge id<MTLBuffer>)mb, mo, (uint32_t)di->count, &wb, &wo)`。
4. **`bright_lut`（`L20682-L20684`）**：
   - 入口增加 `if (g_present_apple5) return;`（`L20683`），隔离未移植的 `MTL4ArgumentTable` 调色表提亮核。

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
Ran 5 tests in 4.594s

OK
```

---

## 3. iOS `Release-iphoneos` 编译与签名覆盖安装校验

- 构建结果：`** BUILD SUCCEEDED **`（`nvn_mtl.o` 大小 `5,687,728` 字节，`-mcpu=apple-a12`）。
- 目标文件指令检查：`nvn_mtl_udot=0`。
- 签名验证（`/tmp/botw-ios-step-b6/signed/TOTK.app`）：
  ```text
  /tmp/botw-ios-step-b6/signed/TOTK.app: valid on disk
  /tmp/botw-ios-step-b6/signed/TOTK.app: satisfies its Designated Requirement
  STAGE_SIGN_OK
  ```
- 增量覆盖安装至 `<YOUR_IPAD_UDID>`（`bundleID: org.macnativegames.botw.a12z`, `databaseSequenceNumber: 2464`）。
- 安装前后设备端 `Documents/GameData/romfs.sha256` 与 `Documents/GameData/shader-seed.txt` 字节级校验：
  ```text
  23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774  /tmp/botw-ios-step-b6/pre-romfs.sha256
  23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774  /tmp/botw-ios-step-b6/post-romfs.sha256
  189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9  /tmp/botw-ios-step-b6/pre-shader-seed.txt
  189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9  /tmp/botw-ios-step-b6/post-shader-seed.txt
  DATA_PRESERVED_OK
  ```

---

## 4. 真机运行结果与新阻塞点分析

### 4.1 关键进展（`/tmp/botw-ios-step-b6/device-log.txt`，共 271 行）

- **稳定运行至第 122 帧 / 6.13 秒**：
  - `L175`：加载自动收集清单 `pso_collected.tsv 19 行`。
  - `L192`：后台新建/落盘 **6 条渲染管线**到管线存档（耗时 `0.3s`）。
  - `L215`：`[totk-boot] 2.432 first presented frame`。
  - `L217–L226, L239`：连续输出 `帧 2..11`（GPU `0.4–1.4 ms`）并推进到 **`[totk-gfx] [长帧] 帧 122（6.13 s）`**。
  - `L235, L238, L251`：直接从管线存档读取复用 3 条已编译管线（`d274907cad...-1` 47 KB、`c457b36cbe...-1` 37 KB、`9639fd0734...-1` 30 KB）。
  - `L237`：5 秒音频回放无欠载（`underruns 0, overruns 0, missing frames 0, callback frames 239616`）。
  - `L244–L252`：执行了 `[shader-predict]`、并行预编（`100 次绘制，丢给后台 3 条管线`）及 `[totk] layered pass: first SetRenderTargets merged (layer 1 of 0-1)`。
- **离散着色器缓存增量**：
  - 设备端 `Documents/shader-cache/rt-totk-native-v9-unverified.discrete-v1` 从 Step B5 的 135 个文件增长至 **147 个文件**。

### 4.2 新阻塞点精确日志与源码定位（`L253-L271`）

```text
253: [dm] [nvn-mtl] FATAL shared upload staging 0x1baaaa3c
254: [totk] host abort, call stack:
...
259: 4   TOTK  0x00000001005009f4 pkg_upload_.cold.2 + 0
260: 5   TOTK  0x00000001001c2b18 pkg_upload_ + 9212
261: 6   TOTK  0x00000001001bed64 pkg_upload + 904
262: 7   TOTK  0x0000000100129374 tex_mtl_p + 664
263: 8   TOTK  0x000000010014f9a4 tex_by_id_p + 1892
264: 9   TOTK  0x00000001001507b8 prepare_draw_textures + 1104
```

根因核对（`nvn_mtl.m#L5966-L6140`）：
1. A12Z（Apple5）硬件不支持 BC 压缩且当前选择 CPU 解码回退路径（`unp = 1`，`L5966`），将多层纹理数组所有 mipmap 与所有 layer 按解压后像素大小累加得到 `total`（`L5968-L5973`）。
2. 本次触发的大尺寸 BC 纹理数组在 CPU 解码后总大小达到 **`0x1baaaa3c`（464,169,532 字节 ≈ 442.7 MiB）**。
3. `pkg_upload_`（`L6078-L6092`）在 `sneed > UP_SBLOCK_SIZE` 时尝试通过 `[g_dev newBufferWithLength:sneed options:MTLResourceStorageModeShared]` 一次性分配整张贴图所需的单一共享 `MTLBuffer`，在 A12Z iPadOS 上分配失败返回 `nil`，于 `L6090` 触发 `MDIE("shared upload staging %#zx", total)`。

---

## 5. Step B7 下一步修复建议

1. **在 `pkg_upload_`（`nvn_mtl.m#L6074-L6145`）中对超大 CPU 上传改为分片/逐层共享暂存分配**：
   - 将当前“循环外一次性 `newBufferWithLength:sneed` 容纳所有 mip 与 layer”的做法，改为当 `sneed > UP_SBLOCK_SIZE`（或超过单缓冲区安全分片上限，如 `[g_dev maxBufferLength]` / 分块阈值）时，**按单个 `(l, y)` 切片所需的 `slice_bytes` 从池化共享块或独立分片缓冲分配暂存空间**（每次 `up_blit_add` 仅引用该切片所在的 `stg` 与偏移，所有分片通过 `[g_up_keep addObject:...]` 保持生命周期至提交结束，并在累计暂存过大时适当触发分批提交）。
   - 同时确认并保护 `blk_decode` / `blk_transcode`（已在 Step B6 就绪）可在后续进入标题画面与世界渲染时被安全验证。
