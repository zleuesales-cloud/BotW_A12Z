# Step B10: 关闭常驻全帧探针、重开 Apple5 `BC1 -> ASTC 4x4` 转码与 `bright_lut` 评估报告

- 测试日期：2026-11-21
- 目标设备：`iPad8,9`（iPad Pro 2020, Apple A12Z GPU / Apple5 家族, UDID `<YOUR_IPAD_UDID>`）
- 目标 Bundle ID：`org.macnativegames.botw.a12z`
- 结论摘要：
  1. **`[present-probe]` 默认关闭并受 `TOTK_PRESENT_PROBE=1` 控制**：默认运行（Run 1，55s+ 推进至 1596+ 帧）全日志 `probe_count=0`，消除了每 30 帧同步全帧回读开销。
  2. **Apple5 `astc_on()` 重开且零报错通过实机双轮验证**：`fmt=0x46`（`1024x1024 x 83 layers x 11 mips`）成功以 `ASTC_4x4_sRGB`（`mtl_pf=186, tca=1, unp=0`）驻留，单张分配从 **443.5 MB 降至 111.5 MB**（-74.9%）；`fmt=0x42`（`512x512 x 83 layers`）同样走 ASTC 转码并降至 32 MiB 阈值以下，`large_total` 从 **555.1 MB 降至 111.5 MB**（-79.9%），峰值 `dev_alloc` 在该节点从 1448.6/1821.1 MB 降至 **1118.1–1125.6 MB**。
  3. **ASTC 画面正确性与音频零欠载**：冒烟测试 Run 2（`TOTK_PRESENT_PROBE=1`）全日志命中 54 条探针，于 `pn=1350..1530` 稳定输出 **`src=1920x1080 src_nz=2073600/2073600`（100% 整屏非零画面）**，两轮均实现零 GPU 错误、零 Metal 4 报错、零音频欠载，且设备端着色器缓存沉淀至 **4113 个条目**。

---

## 1. Step B10 源码修改明细（`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`，未执行 `git commit`）

1. **重开 Apple5 `astc_on()`（`L4814-L4819`）**
   - 移除 `v = !g_present_apple5 && no_bc() && (!e || atoi(e));` 中的 `!g_present_apple5` 屏蔽以及忽略日志分支，改为 `v = no_bc() && (!e || atoi(e));`，使 Apple5 遵循 `TOTK_BC1_ASTC`（默认开启 `BC1 -> ASTC 4x4` 转码）。
2. **门控 `[present-probe]`（`L23143-L23145`）**
   - 在 `present_classic_submit()` 中加入环境变量读取 `probe_env = (e && atoi(e) != 0);`（`TOTK_PRESENT_PROBE`），将 `do_probe` 改为 `g_present_apple5 && probe_env && (pn == 1 || pn == 2 || pn == 10 || pn == 30 || pn == 60 || pn % 30 == 0);`，默认关闭。
3. **`bright_lut`（`L20722-L20745`）保持未修改**，仅作可行性与工作量评估（见第 5 节）。

---

## 2. `astc_on()` 与下游 ASTC 转码/上传链路审计结论

逐点审计 `nvn_mtl.m` 中所有依赖 `astc_on()` / `tex_astc()` / `pf_astc4()` 的路径，确认对 Apple5（无 `MTL4*` / `gpuAddress`）完全安全：

| 链路 / 函数 | 位置 | Apple5 安全机制确认 |
|---|---|---|
| `pkg_upload_` 上传转码 | `L6026, L6032, L6106, L6133, L6140-L6149` | `gtca = !g_present_apple5 && tca && bc1astc_pso()` 保持为 `0`，不向 `g_up_tcas` 投递 GPU 转码队列（不会触发 `L5490` 报错），稳定走 CPU `nvn_bc1_to_astc_image()` + Step B7 `up_shared_alloc()` 分片共享暂存 + classic blit。 |
| `blk_transcode`（渲染到 BC1） | `L6955-L6990` | Step B6 已改为 `if (g_present_apple5)` classic `blit()` / `blit_close()` / `enc_c_begin()` 离散绑定路径。 |
| `WriteTexels` | `L21186-L21200` | 使用 `nvn_bc1_to_astc_image()` CPU 转码 + `enc_b_begin(cb)` classic blit，无 MTL4 调用。 |
| `copy_t2t_astc`（ASTC 复制） | `L21276+` | Step B5 已改为 classic `enc_c_begin()` 离散绑定。 |
| `tex_view_desc` / 格式视图 | `L9489` | 当 `base.pixelFormat` 为 ASTC 且视图要求 `bc1_fmt(r.v.format)` 时，已映射为 `bc1_astc_pf(r.v.format)`，LDR/sRGB 视图格式一致。 |
| `tex_tc`（BC2/3/4/5/7 GPU 转码） | `L4840-L4847` | 继续保持 `!g_present_apple5` 关闭，不影响 `astc_on()`（BC1）。 |

---

## 3. 本地单元测试、编译、指令检查与安装校验

1. **本地单元测试（5/5 OK）**：
   ```text
   $ TOTK_DISCRETE_MSL_DIR=/tmp/totk-abi-e2e/ios-metal-iphoneos-20261120 python3 -m unittest \
     upstream.tests.test_discrete_native_draw \
     upstream.tests.test_discrete_buffer_binding \
     upstream.tests.test_discrete_texture_sampler_binding \
     upstream.tests.test_native_main_encoders -v
   Ran 5 tests in 4.169s — OK
   ```
2. **Release 编译与指令审计**：`** BUILD SUCCEEDED **`（`nvn_mtl.o` `5.4M`），`nvn_mtl_udot=0`。
3. **签名与增量覆盖安装校验**（`<YOUR_IPAD_UDID>`, `org.macnativegames.botw.a12z`, `databaseSequenceNumber: 2508`）：
   - `STAGE_SIGN_OK`
   - `23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774  /tmp/botw-ios-step-b10/pre-romfs.sha256` vs `post-romfs.sha256`
   - `189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9  /tmp/botw-ios-step-b10/pre-shader-seed.txt` vs `post-shader-seed.txt`
   - `DATA_PRESERVED_OK`

---

## 4. B9 与 B10 显存及性能对比与真机日志验证

### 4.1 B9 vs B10 实测对比表

| 指标 | Step B9 基线（`unp=1 RGBA8`，探针开） | Step B10 Run 1（默认关探针，`ASTC` 开） | Step B10 Run 2（`TOTK_PRESENT_PROBE=1`，`ASTC` 开） |
|---|---|---|---|
| `1024x1024 x 83`（`fmt=0x46`）格式 / 标记 | `mtl_pf=71, tca=0, unp=1(ubpp=4)` | `mtl_pf=186 (ASTC_4x4_sRGB), tca=1, unp=0` | `mtl_pf=186 (ASTC_4x4_sRGB), tca=1, unp=0` |
| `1024x1024 x 83` 纹理大小 (`total` / `mt_alloc`) | `442.7 MB` / **`443.5 MB`** | `55.3 MB` / **`111.5 MB`**（**-74.9%**） | `55.3 MB` / **`111.5 MB`**（**-74.9%**） |
| `512x512 x 83`（`fmt=0x42`）大纹理记录 | `110.7 MB` / `111.5 MB` | ASTC 后降至 `< 32 MiB`，未计入大纹理清单 | ASTC 后降至 `< 32 MiB`，未计入大纹理清单 |
| `large_total`（`>32 MiB` 常驻合计） | **`555.1 MB`** | **`111.5 MB`**（**-443.6 MB / -79.9%**） | **`111.5 MB`**（**-443.6 MB / -79.9%**） |
| 大数组上传时 `dev_alloc` | `1448.6 MB`（`ser=5073`）-> `1821.1 MB`（`ser=5074`） | **`1125.6 MB`**（`ser=5070`） | **`1118.1 MB`**（`ser=5078`） |
| `[present-probe]` 行数 / 整屏非零像素 | 开启；`pn=1350..1530` 达 `921600/921600` (720p) | **`0`**（已成功默认关闭） | `54`；`pn=1350..1530` 达 **`2073600/2073600`** (1080p) |
| 第 1500 帧 `footprint` | `1567 MiB` | `1569 MiB`（此时已切入 `1920x1080` 内部分辨率） | `1586 MiB`（`1920x1080`） |
| 第 1500 帧 帧时间 `p50 / p95 / max` | `33.56 / 40.65 / 44.54 ms` | `33.54 / 41.43 / 45.78 ms` | `33.68 / 42.03 / 48.72 ms` |
| 第 1500 帧 GPU `p50 / p95 / max` | `2.35 / 4.45 / 5.88 ms` | `3.65 / 5.77 / 7.85 ms` | `3.73 / 5.63 / 7.43 ms` |
| 音频 `underruns` / 错误统计 | `0` / 零 GPU 与 MTL4 报错 | `0` / 零 GPU 与 MTL4 报错 | `0` / 零 GPU 与 MTL4 报错 |

> 说明：B10 在着色器缓存热身完成后，Run 1 与 Run 2 到第 1500 帧附近均由 720p 切换到了 `1920x1080` 场景输出（`device-log-default.txt#L1039-L1040`、Run 2 探针 `1920x1080`），因此第 1500 帧的绝对 GPU 耗时（`3.65 ms` vs B9 720p `2.35 ms`）与进程整体 `footprint`（~`1569–1586 MiB`）包含了更高渲染目标分辨率及后续管线/渲染目标开销；而在大纹理上传节点，ASTC 转码实打实节省了 **443.6 MB** `large_total` 与 **323+ MB** `dev_alloc`。

### 4.2 真机关键日志摘录
- **Run 1 默认模式（`/tmp/botw-ios-step-b10/device-log-default.txt`，1078 行，推进至 1596+ 帧）**：
  ```text
  166:[dm] [nvn-mtl] 无 BC：BC1 贴图转成 ASTC 4x4（TOTK_BC1_ASTC=0 改回解压成 RGBA8）
  723:[dm] [nvn-mtl] [tex-mem] ser=5070 fmt=0x46 tgt=4 1024x1024 layers=83 mips=11 mtl_pf=186 tca=1 tck=0 unp=0(ubpp=0) total=55.3MB mt_alloc=111.5MB large_total=111.5MB dev_alloc=1125.6MB
  1001:[totk-gfx] [nvn] 计时 至帧 1500： 帧时间 p50 33.54 / p95 41.43 / 最大 45.78 ms（300 帧）； ... GPU p50 3.65 / p95 5.77 / 最大 7.85 ms（300 帧）；
  1003:[totk-gfx] [nvn] 上屏 至帧 1500：300 帧，掉帧 299（丢弃 0），p99 33.35 ms；footprint 1569 MiB；亮度 -1.00；温控 0
  1039:[dm] [nvn-mtl] 内部分辨率（合成采样的场景）：1920x1080
  1076:[totk-audio] output level: rms 0.0031 peak 0.0368 over 5.0 s, underruns 0, overruns 0, missing frames 0, backlog trims 0 (0 frames), callback frames 3599360 / nonzero 1607904
  ```
- **Run 2 冒烟模式（`/tmp/botw-ios-step-b10/device-log-probe.txt`，1091 行，`TOTK_PRESENT_PROBE=1`）**：
  ```text
  722:[dm] [nvn-mtl] [tex-mem] ser=5078 fmt=0x46 tgt=4 1024x1024 layers=83 mips=11 mtl_pf=186 tca=1 tck=0 unp=0(ubpp=0) total=55.3MB mt_alloc=111.5MB large_total=111.5MB dev_alloc=1118.1MB
  1029:[dm] [nvn-mtl] [present-probe] pn=1350 frame=1350 src=1920x1080 fmt=70 src_nz=2073600/2073600 max=(250,250,249,255) dst=1920x1341 dst_nz=2073600/2574720 max=(249,255,250,255)
  1059:[dm] [nvn-mtl] [present-probe] pn=1500 frame=1500 src=1920x1080 fmt=70 src_nz=2073600/2073600 max=(255,254,228,255) dst=1920x1341 dst_nz=2073600/2574720 max=(228,255,255,255)
  1091:[dm] [nvn-mtl] [present-probe] pn=1530 frame=1530 src=1920x1080 fmt=70 src_nz=2073600/2073600 max=(255,254,228,255) dst=1920x1341 dst_nz=2073600/2574720 max=(228,255,255,255)
  ```
- 运行结束后设备端 `Documents/shader-cache/rt-totk-native-v9-unverified.discrete-v1` 累计达 **4113** 个文件条目。

---

## 5. `bright_lut`（`nvn_mtl.m#L20722-L20745`）评估与下一步建议

- **改造工作量评估（本轮未修改）**：
  - `k_lut_msl`（`L20708-L20719`）的计算核函数 `lutk` 已直接声明标准离散参数索引：`src [[texture(0)]]`, `dst [[texture(1)]]`, `k [[buffer(0)]]`。
  - `bright_lut()` 中唯一依赖 Metal 4 的部分是 `MTL4ArgumentTableDescriptor`（`L20733`）、`[b setArgumentTable:at]` / `gpuResourceID` / `gpuAddress`（`L20742`）和 `enc_c_begin(NULL)` 强转。
  - 改为支持 Apple5 的工作量极小（约 15 行）：在 `g_present_apple5` 下跳过创建 `at`，在 `enc_b_copy_texture` 拷贝完 `lut -> tmp` 后调用 `blit_close()` 结束拷贝编码器，再开启 classic `enc_c_begin(NULL)` 并直接调用 `[ce setTexture:tmp atIndex:0]`、`[ce setTexture:lut atIndex:1]`、`[ce setBuffer:b0 offset:0 atIndex:0]` 与 `enc_c_dispatch(...)`。
- **下一步建议**：
  1. 如需室内/洞穴/阴影场景保持与 macOS 版一致的 PQ 调色曝光提亮，可在 Step B11 为 `bright_lut()` 补齐上述 ~15 行 classic 离散计算分支并实机校验。
  2. 继续通过真机输入（New Game / Continue）测试初始台地实时开放世界渲染与长时间游玩的显存/热稳定性。
