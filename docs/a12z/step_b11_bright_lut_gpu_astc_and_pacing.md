# Step B11: `bright_lut` 离散化、Apple5 GPU `BC1 -> ASTC` 转码落地、帧步调与 `tex_tc` 审计报告

- 测试日期：2026-11-21
- 目标设备：`iPad8,9`（iPad Pro 2020, Apple A12Z GPU / Apple5 家族, UDID `<YOUR_IPAD_UDID>`）
- 目标 Bundle ID：`org.macnativegames.botw.a12z`
- 结论摘要：
  1. **`bright_lut()` 已补齐 Apple5 classic Metal 离散实现（`nvn_mtl.m#L20776-L20816`）**：通过 `blit()` -> `blit_close()` -> `enc_c_begin(NULL)` 离散绑定 `tmp [[texture(0)]]`、`lut [[texture(1)]]`、`b0 [[buffer(0)]]`，由 Metal 默认资源 hazard tracking 与 `g_classic_fence` 双重保障无脏读；实机在 BotW 下未触发 TotK 专属着色器键 `"552f45c348a61b86"`，且对 1080p 画面无任何过曝或异常。
  2. **Apple5 GPU `BC1 -> ASTC` 转码（`apple5_gtca`）一次落地并大幅消除上传尖峰**：在 `pkg_upload_()` 与 `up_flush()` 中建立无需 `g_nvn_deswz_gpu`（Metal 4）的混合路径（CPU 直接解排布 BC1 至共享暂存块 + `g_up_cb` 上 classic `MTLComputeCommandEncoder` 派发 `bc1astc_pso()` + classic `MTLBlitCommandEncoder` 拷入目标贴图）。第 175 帧 21 张大贴图（`112.6 MB`，含 83 层 `1024x1024` 数组）上传耗时从 Step B10 CPU 转码的 **`2135.2 ms` 降至 Run 1 的 `651.0 ms`（-69.5%）与 Run 2（管线热缓存）的 `370.5 ms`（-82.6%，提速 5.76x）**。
  3. **实机在 55s 窗口内推进至 2100～2461+ 帧（进入实际 3D 场景渲染）**：在每帧 `1443–1898` 次绘制、约 `120` 个渲染通道的完整 3D 负载下，第 2100/2400 帧窗口达到 **帧时间 p50 33.27–33.40 ms / p95 35.24–35.68 ms**、**GPU p50 14.64–15.28 ms / p95 16.61–16.70 ms**，`1920x1080` 全帧持续 `src_nz=2073600/2073600`，零 GPU 错误、零 Metal 4 报错、零音频欠载，离散着色器缓存沉淀至 **4517** 项。
  4. **“掉帧 299”与 p95 抖动根因查明，并完成 `tex_tc` 迁移评估**：`hle_nvn.c#L3336` 以 60 FPS 口径硬编码 `drop += x > 25.0`（`16.7 * 1.5 ms`），导致 iOS 30 FPS 锁帧（`33.35 ms` 稳定步调、`丢弃 0`）下的正常帧被计入 `drop`。

---

## 1. Step B11 源码修改明细（`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`，未执行 `git commit`）

1. **`up_flush()`（`L5482-L5540`）**
   - 为 `g_present_apple5` 实现两阶段 classic 编码器提交：
     - 保持 `if (g_nup_decs || g_nup_tcxs) MDIE(...)` 防护。
     - 当 `g_nup_tcas > 0` 时，在 `g_up_cb` 上开启 classic `MTLComputeCommandEncoder`，绑定 `bc1astc_pso()` 并逐项设置 `x->src`（slot 0）、`x->params` + `x->poff`（slot 1）、`x->dst`（slot 2），派发 `(x->bw + 7) / 8 x (x->bh + 7) / 8` 线程组后 `[ce endEncoding]`，首次触发打印 `[upload] Apple5 GPU BC1->ASTC transcode active (classic compute encoder)`。
     - 当 `g_nup_blits > 0` 时，在 `g_up_cb` 上开启 classic `MTLBlitCommandEncoder` 执行全部 `enc_b_copy_buffer_texture` 后 `[be endEncoding]`。
2. **`up_begin()`（`L5611-L5615`）**
   - 在 `g_present_apple5` 下仅创建 `g_up_cb = [g_q commandBuffer];` 并将 `g_up_ce` 置为 `nil`，将计算与拷贝编码器的开启推迟到 `up_flush()`，保证 `bc1astc` 计算先于贴图 blit 执行。
3. **`pkg_upload_()`（`L6129-L6186`）**
   - 新增 `int apple5_gtca = g_present_apple5 && tca && t->d.target != 2 && bc1astc_pso();`。
   - 当 `apple5_gtca` 为真时，`total` 保持为 BC1 原始压缩字节数（仅占 ASTC 大小的一半），并开启逐切片分片（`chunked = 1`），同时在 `sigsetjmp` 回滚保护中同步记录与恢复 `nt0 = g_nup_tcas`。
   - 在 `(l, y)` 循环内：先对 `s_extra + p_extra` 进行 `g_up_batch_limit`（128 MiB）联合预检（必要时先 `up_flush(); up_begin();`），确保同一切片的共享 BC1 输入缓冲 `stg`（`up_shared_alloc`）与私有 ASTC 输出缓冲 `ast`（`up_alloc_block(..., 0)`）严格处于同一上传批次；随后 CPU 直接将 BC1 数据解排布写入 `stg.contents + off`（免除临时 `malloc/free`），填写 `up_param_alloc(&qoff)` 参数块并登记 `g_up_tcas` 与 `up_blit_add`。
4. **`bright_lut()`（`L20776-L20816`）**
   - 移除 `if (g_present_apple5) return;`；在 `g_present_apple5` 下跳过 `MTL4ArgumentTableDescriptor` 创建。
   - `enc_b_copy_texture` 完成 `lut -> tmp` 拷贝后，Apple5 分支调用 `blit_close()`，再开启 classic `enc_c_begin(NULL)`，离散绑定 `ps`、`tmp`（texture 0）、`lut`（texture 1）、`b0`（buffer 0）并派发 `(lut.width+3)/4 x (lut.height+3)/4 x (lut.depth+3)/4` 后调用 `enc_c_end(ce)`。
   - **同步与 Hazard 核查**：`tmp`（`MTLStorageModePrivate`）与 `lut` 均采用 Metal 默认 `MTLHazardTrackingModeDefault`（Tracked），且 `blit_close()`、`enc_c_begin()`、`enc_c_end()` 内部在 Apple5 上维护 `g_classic_fence` 等待与更新，确保 `blit -> compute -> 后续色调映射采样` 无竞态。

---

## 2. 帧时间 `p95` 抖动、“掉帧 299”统计根因与 `tex_tc` 迁移评估

### 2.1 “掉帧 299（丢弃 0）”与 `p95` 帧时间分析
1. **“掉帧 299”是 60 FPS 阈值硬编码导致的统计假象**：
   - `hle_nvn.c#L3335-L3336` 的上屏掉帧统计代码为：
     ```c
     // 上屏：掉帧 = 间隔 > 16.7 × 1.5 ms 或被丢弃
     for (unsigned i = 0; i < n; i++) { double x = g_pres_iv[i]; if (x < 0) { drop++; discard++; } else if (x > 0) { v[k++] = x; drop += x > 25.0; } }
     ```
   - 在 iOS 上，BotW 固定为 **30 FPS**（`phone frame rate: held at 30`、`游戏设出图间隔 1 → 2`，即 60 Hz 显示器每 2 个刷新周期上屏 1 帧，目标间隔 **33.33 ms**）。
   - 实测 `上屏节奏（近 600 张，显示器 60 Hz）：间隔 p50 33.35 / p95 33.35 ms；系统丢弃 0 张`，且 `上屏 至帧 1500` 的 `丢弃 0`、`p99 33.35 ms`，说明上屏步调完全锁定在 `33.35 ms`。因为 `33.35 > 25.0`，所有正常 30 FPS 帧均被 `x > 25.0` 计入 `drop`。
2. **`帧时间 p95 ~40.5–41.4 ms` 的来源**：
   - `帧时间`（`g_tw[0]`）测量的是游戏主线程相邻两次提交的 CPU 墙钟间隔。标题/过渡阶段每帧包含 `50.1` 次 uniform 副本更新、`2.40 M` 条指令提交及后台异步着色器编译，使部分帧 CPU 墙钟在 `25–42 ms` 间波动，但经 2 帧交换链缓冲吸收后，最终屏幕呈现间隔保持 `p50 33.35 / p95 33.35 / p99 33.35 ms`；进入第 2100/2400 帧稳态 3D 场景后，即便每帧 1443–1724 次绘制，`帧时间 p95` 也收敛至 **35.24–35.68 ms**（`max 37.50 ms`）。

### 2.2 `tex_tc`（`BC2/3/4/5/7 -> ASTC 4x4 / EAC`，`nvn_mtl.m#L4826-L4847`）Apple5 迁移评估表（只审计未开启）

| 评估维度 | 现状与卡点位置 | 迁移到 Apple5 所需的改造点 | 预期收益与风险评估 |
|---|---|---|---|
| 启用条件 (`tex_tc`, `L4840-L4847`) | 要求 `!g_present_apple5` 且 `g_nvn_deswz_gpu != 0` 且 `mtl_addr_known`（仅限池内块线性贴图） | 像 B11 的 `apple5_gtca` 一样，允许在 `!g_nvn_deswz_gpu` 下走“CPU 解排布原始 BC2–7 压缩块到共享暂存 + GPU `bctc_pso()` 转码到私有 `ast` + Blit 入贴图” | 免除移植整套 `nvn_deswz_gpu_encode_m4` 的复杂性与风险 |
| 转码内核 (`bctc_pso`, `L5391-L5400`, `tex/nvn_bctc_msl.h`) | `bctc` 核函数使用 `buffer(0)`（源 BC 块）、`buffer(1)`（24B 参数块 `BP`）、`buffer(2)`（目标 ASTC/EAC 块），已与 classic 离散槽位完全兼容 | 无需改动 MSL 内核；`bctc_pso()` 本身已通过 `mtl_counted_library` + `mtl_counted_compute` 创建 | 零着色器兼容性风险 |
| 上传编码 (`pkg_upload_` `L6029-L6030, L6077-L6085, L6101-L6104`) | 当前 `if (tck)` 在 `!use_gpu` 时直接落入 `L6102` `MDIE("Apple5 lacks the CPU BC-to-ASTC/EAC upload path...")` | 在 `(l, y)` 循环中为 `tck` 复用 B11 `apple5_gtca` 的联合配额预检 + `up_shared_alloc(pitch * rows)` + `up_alloc_block(x_bytes)` + `nvn_deswizzle` + `g_up_tcxs` 登记 | 代码结构与 `apple5_gtca` 完全同构（约 25 行） |
| 批次冲刷 (`up_flush`, `L5486, L5498-L5504`) | `L5486` 拦截 `g_nup_tcxs`，`L5498` 仅实现 MTL4 `g_up_ct` 派发 | 在 `up_flush()` 的 `if (g_present_apple5)` 计算编码器中增加 `if (g_nup_tcxs)` 循环，绑定 `bctc_pso()` 与 `buffer(0..2)` 派发 | 约 12 行代码；可使镜像池内 `BC2/BC3/BC7` 显存占用降至 **1/4**（`RGBA8 -> ASTC_4x4`）、`BC4/BC5` 降至 **1/2**（`R8/RG8 -> EAC_R11/RG11`），同时省去 CPU `nvn_bc_decode` 软解耗时 |

---

## 3. 本地单元测试、编译、指令检查与安装校验

1. **本地单元测试（5/5 OK）**：
   ```text
   $ TOTK_DISCRETE_MSL_DIR=/tmp/totk-abi-e2e/ios-metal-iphoneos-20261120 python3 -m unittest \
     upstream.tests.test_discrete_native_draw \
     upstream.tests.test_discrete_buffer_binding \
     upstream.tests.test_discrete_texture_sampler_binding \
     upstream.tests.test_native_main_encoders -v
   Ran 5 tests in 5.657s — OK
   ```
2. **Release 编译与指令审计**：`** BUILD SUCCEEDED **`（`nvn_mtl.o` `5.4M`），`nvn_mtl_udot=0`。
3. **签名与增量覆盖安装校验**（`<YOUR_IPAD_UDID>`, `org.macnativegames.botw.a12z`, `databaseSequenceNumber: 2516`）：
   - `STAGE_SIGN_OK`
   - `23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774  /tmp/botw-ios-step-b11/pre-romfs.sha256` vs `post-romfs.sha256`
   - `189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9  /tmp/botw-ios-step-b11/pre-shader-seed.txt` vs `post-shader-seed.txt`
   - `DATA_PRESERVED_OK`

---

## 4. B10 vs B11 实机性能与显存对比及日志证据

### 4.1 B10 vs B11 实测对比表

| 指标 | Step B10 Run 1（CPU `BC1->ASTC`，探针关） | Step B11 Run 1（GPU `BC1->ASTC`，探针关） | Step B11 Run 2（GPU `BC1->ASTC`，`TOTK_PRESENT_PROBE=1`） |
|---|---|---|---|
| `BC1 -> ASTC` 转码执行位置 | CPU (`nvn_bc1_to_astc_image`) | **A12Z GPU (`bc1astc_pso` classic compute)** | **A12Z GPU (`bc1astc_pso` classic compute)** |
| 第 175 帧 21 张贴图（`112.6 MB`）上传耗时 | `2135.2 ms`（整帧 `2243.6 ms`） | **`651.0 ms`**（整帧 `744.8 ms`，**-69.5%**） | **`370.5 ms`**（整帧 `452.5 ms`，**-82.6%**） |
| `1024x1024 x 83`（`fmt=0x46`）`[tex-mem]` | `mtl_pf=186, tca=1, unp=0, mt_alloc=111.5MB, dev_alloc=1125.6MB` | `mtl_pf=186, tca=1, unp=0, mt_alloc=111.5MB, dev_alloc=1118.0MB` | `mtl_pf=186, tca=1, unp=0, mt_alloc=111.5MB, dev_alloc=1126.3MB` |
| 55s 观测窗口内推进帧数 | `1596` 帧 | **`2100+` 帧**（进入 1720–1884 draw/帧 3D 场景） | **`2461+` 帧**（进入 1443–1898 draw/帧 3D 场景） |
| 第 1500 帧 帧时间 `p50 / p95 / max` | `33.54 / 41.43 / 45.78 ms` | `33.53 / 41.41 / 48.12 ms` | `33.47 / 40.56 / 42.42 ms` |
| 第 1500 帧 GPU `p50 / p95 / max` | `3.65 / 5.77 / 7.85 ms` | `3.60 / 5.64 / 7.82 ms` | `3.80 / 6.07 / 8.12 ms` |
| 第 1500 帧 `footprint` / 上屏 `p99` | `1569 MiB` / `33.35 ms`（掉帧计数 299，丢弃 0） | `1664 MiB` / `33.35 ms`（掉帧计数 299，丢弃 0） | `1657 MiB` / `33.35 ms`（掉帧计数 300，丢弃 0） |
| 第 2100 / 2400 帧（3D 场景稳态）帧时间 `p50 / p95 / max` | 未到达 | **帧 2100：`33.40 / 35.24 / 49.79 ms`**（1720 draws/帧） | **帧 2400：`33.27 / 35.68 / 37.50 ms`**（1443 draws/帧） |
| 第 2100 / 2400 帧（3D 场景稳态）GPU `p50 / p95 / max` | 未到达 | **帧 2100：`14.64 / 16.70 / 22.10 ms`** | **帧 2400：`15.28 / 16.61 / 19.81 ms`** |
| 第 2100 / 2400 帧 `footprint` / `显卡已分配` | 未到达 | `2611 MiB` / `1900 MB` | `2597 MiB` / `1900 MB` |
| `[present-probe]` 1080p 整屏非零验证 | `0`（默认关闭） | `0`（默认关闭） | `85` 条；`pn=1500..2460` 持续 **`2073600/2073600`** |
| 音频 `underruns` / 错误统计 | `0` / 零 GPU 与 MTL4 报错 | `0` / 零 GPU 与 MTL4 报错 | `0` / 零 GPU 与 MTL4 报错 |

### 4.2 真机关键日志摘录
- **Run 1 默认模式（`/tmp/botw-ios-step-b11/device-log-default.txt`，874 行）**：
  ```text
  381:[dm] [nvn-mtl] [tex-mem] ser=5070 fmt=0x46 tgt=4 1024x1024 layers=83 mips=11 mtl_pf=186 tca=1 tck=0 unp=0(ubpp=0) total=55.3MB mt_alloc=111.5MB large_total=111.5MB dev_alloc=1118.0MB
  383:[dm] [nvn-mtl] [upload] Apple5 GPU BC1->ASTC transcode active (classic compute encoder)
  428:[totk-gfx] [长帧] 帧 175（14.73 s）整帧 744.8 ms，提交+出图 703.2 ms，GPU 329.7 ms；新建管线 3 条 + 着色器库 2 个共 263.5 ms；上传贴图 首传 21 张 + 重传 0 次 112.6 MB / 651.0 ms
  658:[totk-gfx] [nvn] 计时 至帧 1500： 帧时间 p50 33.53 / p95 41.41 / 最大 48.12 ms（300 帧）； ... GPU p50 3.60 / p95 5.64 / 最大 7.82 ms（300 帧）；
  761:[dm] [nvn-mtl] 内存 至帧 1800：常驻 2607 MB，显卡已分配 1900 MB，延迟释放队列 22，驻留集 1399 个，内存池 3400 个（峰值 3401 / 跟踪表容量 16384），贴图重传累计 74 次
  843:[totk-gfx] [nvn] 计时 至帧 2100： 帧时间 p50 33.40 / p95 35.24 / 最大 49.79 ms（300 帧）； ... GPU p50 14.64 / p95 16.70 / 最大 22.10 ms（300 帧）；
  847:[totk-gfx] [nvn] 翻译 至帧 2100：每次绘制 40064 条指令，每帧提交 68.89 M 条 / CPU 10.694 ms，每帧绘制 1720
  ```
- **Run 2 探针冒烟模式（`/tmp/botw-ios-step-b11/device-log-probe.txt`，842 行，`TOTK_PRESENT_PROBE=1`）**：
  ```text
  261:[dm] [nvn-mtl] [tex-mem] ser=5062 fmt=0x46 tgt=4 1024x1024 layers=83 mips=11 mtl_pf=186 tca=1 tck=0 unp=0(ubpp=0) total=55.3MB mt_alloc=111.5MB large_total=111.5MB dev_alloc=1126.3MB
  263:[dm] [nvn-mtl] [upload] Apple5 GPU BC1->ASTC transcode active (classic compute encoder)
  304:[totk-gfx] [长帧] 帧 175（10.33 s）整帧 452.5 ms，提交+出图 412.0 ms，GPU 336.0 ms；新建管线 3 条 + 着色器库 2 个共 0.8 ms；上传贴图 首传 21 张 + 重传 0 次 112.6 MB / 370.5 ms
  594:[dm] [nvn-mtl] [present-probe] pn=1500 frame=1501 src=1920x1080 fmt=70 src_nz=2073600/2073600 max=(255,254,228,255) dst=1920x1341 dst_nz=2073600/2574720 max=(228,255,255,255)
  794:[dm] [nvn-mtl] [present-probe] pn=2100 frame=2101 src=1920x1080 fmt=70 src_nz=2073600/2073600 max=(101,204,255,0) dst=1920x1341 dst_nz=2073600/2574720 max=(255,255,101,255)
  817:[totk-gfx] [nvn] 计时 至帧 2400： 帧时间 p50 33.27 / p95 35.68 / 最大 37.50 ms（300 帧）； ... GPU p50 15.28 / p95 16.61 / 最大 19.81 ms（300 帧）；
  842:[dm] [nvn-mtl] [present-probe] pn=2460 frame=2461 src=1920x1080 fmt=70 src_nz=2073600/2073600 max=(173,235,255,0) dst=1920x1341 dst_nz=2073600/2574720 max=(255,255,173,255)
  ```
- 运行结束后设备端 `Documents/shader-cache/rt-totk-native-v9-unverified.discrete-v1` 文件条目增至 **4517**。

---

## 5. 下一步建议（Step B12）

1. **按第 2.2 节评估表迁移并开启 Apple5 `tex_tc`（`BC2/3/4/5/7 -> ASTC 4x4 / EAC` GPU 转码）**：在 3D 场景（帧 1800–2400）中，`footprint` 已升至 `~2600 MiB`（`显卡已分配 1900 MB`）；复用 B11 验证成功的“CPU 解排布压缩块 + classic 计算编码器 `bctc_pso()` 转码”模式放开 `tex_tc`，可进一步把池内 `BC2/3/4/5/7` 贴图显存压缩 50%–75%，增大 6 GB 设备长时间跑图的内存安全裕度。
2. **可选校准 `hle_nvn.c#L3336` 在 30 FPS 模式下的掉帧统计阈值**：当前 `x > 25.0` 为 60 FPS（16.7 ms × 1.5）硬编码，若根据当前出图间隔（30 FPS 时为 `33.33 ms × 1.5 = 50.0 ms`）动态计算阈值，日志中的 `掉帧` 计数即可真实反映 30 FPS 下的实际掉帧情况。
