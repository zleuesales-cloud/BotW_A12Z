# Step B14：“点击继续”闪退定位、Argument Arena 4 MiB 根治、内存水位压降与长稳压测报告

## 1. 闪退取证与根因定位

通过 `/opt/homebrew/bin/idevicecrashreport -u <YOUR_IPAD_UDID> -k /tmp/botw-ios-step-b14/crashes` 拉取设备崩溃与 Jetsam 日志，并使用 `TOTK.app.dSYM`（`UUID: 651FA7BB-9DB5-3FE6-AFD5-D03BB0E5C5BA`）完成 `atos` 符号化：

1. **原“点击继续 / 长跑崩溃”实锤（`TOTK-2026-11-21-143317.ips`）**
   - 进程：`TOTK` PID `3566`，异常 `EXC_BAD_ACCESS / SIGABRT (abort() called)`，发生时间 `2026-11-21 14:33:15.2436 +0800`（即 B13 Run 2 第 `5,132` 帧 `FATAL argument arena allocation failed: 4194304 bytes`）。
   - `atos` 符号化崩溃线程（Thread 3）调用栈：
     `arena_alloc_u.cold.1 -> arena_alloc_u -> upd_shadow_resolve (nvn_mtl.m:3189) -> encode_args -> draw_inner -> draw (nvn_mtl.m:19017) -> mtl_exec (nvn_mtl.m:21931) -> submit_exec (hle_nvn.c:2931) -> h_nvnQueueSubmitCommands (hle_nvn.c:2891) -> runtime_hle_switch -> ... -> dm_thread_enter / core_put (threads.c:322)`。
   - **同期 Jetsam 检查（`JetsamEvent-2026-11-21-143122.ips`）**：`TOTK` 为 `frontmost`（`rpages = 148561`，约 `2,321 MiB`，`reason: None`），系统仅查杀后台 `fileindexerd` 与 `searchd`（`highwater`），**并未 Jetsam 强杀 `TOTK`**。
   - **`discrete-v1` 审计**：`encode_args()` 在 `discrete` 模式下直接提前返回，不进入 Metal 4 参数表 `arena_alloc_u(arg_len, ...)` 分支；Apple5 路径上的 arena 消耗全部来自 `upd_shadow_resolve`、`mtl_sub_update`、`usnap_take` 与 `driver_bank_gpu_off`。

2. **B14 Run 1b 捕获到的“进世界瞬间”独立新崩溃（`TOTK-2026-11-21-145208.ips`）**
   - 在修复 arena 后首次实测进入 3D 大世界（`world=1`，`1193 MiB`）时，在 `dispatch -> encode_args -> discrete_bind_buffer`（`nvn_mtl.m:13716`）触发：
     `FATAL invalid discrete buffer binding: stage 5 kind 12 class 1 slot 11 buffer 0x0 offset 0`。
   - **根因**：`dummy_buf()` 此前在首次遇到未绑定存储缓冲（`NVN_RB_SBUFP`，compute `stage 5`）时**延迟分配整块 64 MiB（`65536 << 10`）**共享 Metal 缓冲，进世界加载瞬间的内存压力导致 `[g_dev newBufferWithLength:]` 返回 `nil` 且无任何兜底，随后被 `discrete_bind_buffer` 捕获为 `buffer 0x0`。

---

## 2. 源码修复与改动明细

| 模块 | 文件与精确行号 | 改动内容与作用 |
| --- | --- | --- |
| **Dummy Zero Buffer 预分配与降级** | `upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m` (`L1145`, `L1224-L1226`, `L13238-L13256`) | iOS 默认大小由 **64 MiB 降至 4 MiB**（`4096 << 10`，macOS 保持 64 MiB），省出 60 MiB 工作集；增加逐级减半兜底（最小 `64 KiB`）与 `MDIE` 校验；在 `mtl_init()` 内预先调用 `(void)dummy_buf()`，避免进世界内存尖峰期首次分配失败返回 `nil`。 |
| **Argument Arena 细粒度 + 快收 + 等待恢复** | `upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m` (`L9908-L10047`) | 1) iOS 块粒度由 `4 MiB` 降为 `1 MiB`（`quantum = 1u << 20`）；<br>2) `arena_trim()` 在 iOS 下将闲置阈值从 `STOR_IDLE_SUBMITS=600` 缩短至 **`30` 次提交**，并将常驻池上限钳位在 **`keep_max = 8` 块**（保留尾部最热 LIFO 块供复用）；<br>3) `arena_new()` 在 `newBufferWithLength:` 返回 `nil` 时不再直接 `MDIE`，而是先调用 `arena_purge_idle_locked(done)` 释放已完成的非 pinned 闲置块并重试，若仍失败则在 `g_cv` 上分 `10 × 50 ms`（最长 500 ms）等待 GPU 完成栅栏并尝试直接复用/释放后重试；<br>4) 在 `arena_commit()` 中每 `300` 次提交打印 `[arena] stats` 活跃/峰值/释放/恢复遥测。 |
| **Working-Set 驻留上限与贴图淘汰裕度** | `upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m` (`L9298-L9317`, `L9441-L9443`) | 1) 将 `res_over_working_set()` 的启用条件从 `#if TARGET_OS_IPHONE && defined(GAME_OOT)` 放开到全部 `#if TARGET_OS_IPHONE`，在 `g_present_apple5` 下默认 `TOTK_RES_CAP_PCT=88`（`3604 MB / 4096 MB`，超限后淘汰闲置贴图副本回落至 `3399 MB`）；<br>2) 将 Apple5 下 `pkg_evict_sweep()` 的 `LOW / HIGH / CRIT` 阈值从 `250 / 350 / 150 MB` 提高到 **`400 / 550 / 250 MB`**。 |
| **iOS 音频回调预充水位修复** | `upstream/games/totk/runtime/audio_mixer.c` (`L190-L209`) | iPadOS RemoteIO 每次拉取 `1024` 帧（`21.33 ms`），原默认 `4 × 5 ms = 20 ms`（`960` 帧）固定少 `16` 帧导致必然欠载；将 `#if TARGET_OS_IPHONE` 默认预充提高至 **`AUDIO_PREFILL_DEFAULT_BLOCKS=8`（`40 ms`）**（macOS 维持 `4 / 20 ms`）。 |

---

## 3. 单测、编译、签名与数据一致性校验

- **本地单测（14/14 OK）**：
  - `TOTK_DISCRETE_MSL_DIR=/tmp/totk-abi-e2e/ios-metal-iphoneos-20261120 python3 -m unittest upstream.tests.test_discrete_native_draw upstream.tests.test_discrete_buffer_binding upstream.tests.test_discrete_texture_sampler_binding upstream.tests.test_native_main_encoders upstream.tests.test_warm_policy -v`
  - 结果：`Ran 14 tests in 6.155s - OK`
- **构建与符号校验**：
  - `xcodebuild` `Release-iphoneos`：`** BUILD SUCCEEDED **`（`nvn_mtl.m` / `audio_mixer.c` 零新增编译警告）。
  - `nvn_mtl_udot=0`（`otool -tV ... nvn_mtl.o | grep -c '\budot\b'` = `0`）。
  - 签名与部署：`STAGE_SIGN_OK`（`CFBundleIdentifier=org.macnativegames.botw.a12z`，增量覆盖安装 `databaseSequenceNumber: 2576`）。
- **设备数据与 B13 着色器存档完整性校验**：
  - `romfs.sha256`（`23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774`）与 `shader-seed.txt`（`189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9`）安装前后完全一致：**`DATA_PRESERVED_OK`**。
  - `Documents/shader-cache/rt-totk-native-v9-unverified.discrete-v1/pipelines/` 保持 **`3222`** 个 `.metalarchive` 管线存档完好未损。

---

## 4. 真机两轮验证与 B13 vs B14 对比

两轮验证均保持 `[present-probe]` 默认关闭（`present_probe=0`），零 `MTL4*` / `gpuAddress` 报错：

1. **Run 1（默认启动路径，不设 `TOTK_START_SCREEN=0`）**：
   - 现版安装包以 `{"TOTK_DEBUG_LOG": "1", "TOTK_AUTOLOAD": "1"}` 启动（`PID 3841`），正常加载 `Settings` 与 `TOTKStartView` UI 并按 `main.m:428-430` 设计停留在开始界面等待物理点击（此前同策略下 `PID 3778` 在手动点击后稳定推进至标题画面 `footprint 1140 MiB`、`underruns 0`）。
   - 修复前的 `PID 3791` 捕获了上述 `dummy_buf` `64 MiB -> nil` 问题；修复后的完整读档“继续游戏 -> 进大世界”链路已由 Run 2 通过 `[autoload]` 全流程验证。

2. **Run 2（长稳压测，`PID 3907`，`TOTK_START_SCREEN=0` + `TOTK_DEBUG_LOG=1` + `TOTK_AUTOLOAD=1`）**：
   - 干净突破原 B13 第 `5,132` 帧崩溃点，稳定推进至 **第 `9,900` 帧 / `352 s`（约 5.9 分钟）**（`[world] entered at frame 378`），压测结束后拉取 `/tmp/botw-ios-step-b14/crashes-post` 确认 `14:52:08` 之后**零新崩溃、零 Jetsam**。

| 对比指标 | Step B13 Run 2（热重启，第 `5,132` 帧中止） | Step B14 Run 2（长稳压测，第 `9,900` 帧达标收工） |
| --- | --- | --- |
| 启动预热与管线复用 | `3204` 条（`175.3 MB`），填充 `0.1 s`；运行期仅 `6` 次未命中、`1` 次即时转译 | `3222` 条（`176.7 MB`），填充 `0.1 s`；预热后 `861 + 8 + 2` 命中；**运行期未命中 `0`、即时转译 `0`** |
| `dummy_buf` 与 GPU 报错 | 64 MiB 懒加载，无显式报错但曾诱发 Run 1b `0x0` 崩溃 | `[dummy-buf] allocated 4096 KB zero buffer`，**零 GPU / MTL4 / `gpuAddress` 报错** |
| Argument Arena 生命周期 | `4 MiB` 块 + `600` 提交回收，最终第 `5,132` 帧 `FATAL 4194304 bytes` | `1 MiB` 块 + `30` 提交/上限 `8` 块；**`kept=19 (19.0 MB)`，峰值 `134` 块（`134.0 MB`），累计释放 `10,830` 块（`10,830.0 MB`），触发兜底恢复 `0` 次，零崩溃** |
| Footprint 峰值与稳态 | 峰值 **`2974 MiB`**（第 3,900 帧），第 5,100 帧 **`2885 MiB`** | 峰值 **`2645 MiB`**（第 1,800 帧，**-329 MiB**），第 6,300..9,900 帧稳态 **`2627 MiB`**（**-258 MiB**） |
| 帧步调（稳态对比） | 第 5,100 帧：`p50 33.40 / p95 34.78 / 最大 58.18 ms`；掉帧 `19`（丢弃 `0`） | 第 9,900 帧：**`p50 33.32 / p95 35.47 / 最大 52.50 ms`**；掉帧 `14`（丢弃 `0`） |
| 音频输出与欠载 (`underruns`) | `187 s` 累计 **`underruns 11`**（默认 `20 ms` 预充） | `352 s`（`16,799,744` 回调帧）**`underruns 0, overruns 0, missing frames 0, backlog trims 0`**（iOS 默认 `40 ms` 预充） |

---

## 5. 下一步建议

1. 如需再次人工走一遍“主屏幕点击图标 -> 点击 Start Screen Play -> 标题界面手点‘继续游戏’”的手势交互回归，可直接点开当前已安装在 iPad 上的应用完成手点进大世界确认。
2. Step B14 后，6 GB `iPad8,9` 在大世界稳态运行 `footprint` 已收口在 `2627 MiB` 且 5.9 分钟零崩溃零音频欠载；后续（Step B15）可将验证重心转向 **高负载跑图/切换神庙/大量贴图淘汰（触发 `pkg_evict_sweep`）时的流畅度与温度控制**，以及在确认长稳后对 `TOTK` 进行常规画面细节与按键体验打磨。
