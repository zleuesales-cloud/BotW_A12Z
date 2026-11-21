# Step B13：Mac 全量着色器/管线清单盘点、离线转译 `discrete-v1` 与真机预热验证

## 1. Mac 与 iPad 资产盘点与格式隔离

在推送任何着色器缓存或管线清单前，先将 iPad 端（`iPad8,9`, UDID `<YOUR_IPAD_UDID>`, `org.macnativegames.botw.a12z`）现有资产完整备份至 `/tmp/botw-ios-step-b13/backup/`，并与 Mac 端资产逐项比对。

| 资产位置 / 项目 | 文件与规模 | 内容与格式说明 | 是否兼容 iPad / Apple5 |
| --- | --- | --- | --- |
| iPad 备份：`Documents/GameData/` | `romfs.sha256`、`shader-seed.txt`、`apple5-discrete/`、`shader-seed-apple5/` | 运行基底校验与种子清单 | 是（只读保留，`DATA_PRESERVED_OK`） |
| iPad 备份：`Documents/shader-cache/rt-totk-native-v9-unverified.discrete-v1/` | `6,184` 文件（`706` 个 `.code`/`.res.json`/`.spv`/`_b.json`/`_b.metal`，`705` 个 `.variants`，`973` 个 `pipelines/.metalarchive`+`.keys`，`1` 个 `pipelines.lock`，`1` 个 `pipelines/.json`，`1` 份 `1,671` 行 `pso_collected.tsv`） | `pso_collected.tsv` 含 `23 B, 8 C, 942 R, 698 S`，引用 `698` 个唯一 variants | 是（基准合并底表，全量保留） |
| Mac：`~/Library/Application Support/MacNativeGames/BotW/shader-cache/rt-totk-native-v9-unverified` | `1,484` 文件（`6` 个 `.code`/`.res.json`/`.spv`/`.variants`/`_b.json`/`_b.metal`，`565` 个 `pipelines/.metalarchive`+`.keys`，`313` 个 `variants/.metal`，`10,241` 行 / `7,972,994` 字节 `pso_collected.tsv`） | `pso_collected.tsv` 含 `200 B, 10 C, 6,835 R, 3,175 S, 21 T`，引用 `4,108` 个 variants（覆盖 `3,325` 个基础着色器，其中 `3,163` 个位于 `botw190.inventory`） | **部分兼容**：仅复用平台无关的 `pso_collected.tsv` 与 `6` 个原始 `.code`；**严禁**拷贝 Mac `.metalarchive`、`variants/.metal` 或 `nvn_pso4` 二进制存档 |
| Mac：`BotW_build/totk/build/shader-seed/botw190.inventory` | `19,672` 个原始 NVN 着色器（`shaders.bin` `125,603,187` 字节） | `source_romfs_sha256` = `826b373b80cea5c581749a09dfbd9bb21e8d7a9cd3ed161c4d5315b9ae429dad`（与 iPad `romfs.sha256` 完全一致） | 是（作为 `--binding-abi discrete-v1` 离线转译原始输入） |
| Mac：`BotW_build/totk/build/shader-seed/botw190` | `50,678` 文件（`15,528` 个 `_b.metal`/`_b.json`，`19,619` 个 `.variants`） | `ab`-ABI（`spvDescriptorSetBuffer0`）Mac 产物 | **否**（严格隔离，禁止推送到 iPad） |
| Mac：`shader-list/botw-shader_list.tsv` | `28,394` 行（`8,539,984` 字节） | 全量管线/着色器状态清单 | 是（参与平台无关清单合并） |

## 2. 离线 `discrete-v1` 批量转译、增量合并与 100% 抽检

1. **构建离散转译器并离线转译**：
   - 构建支持 `--binding-abi discrete-v1` 的 `/tmp/botw-ios-step-b13/xlate-bin/totk_xlate`。
   - 调用 `shader_list.py merge` 生成 `/tmp/botw-ios-step-b13/test-merged-list.tsv`（`4804 new; 1266 variants over 1140 shaders, 509 bind states, 0 located; warm rows: 3288 S, 8 C, 26155 R, 634 B, 1415 T`）。
   - 执行 `prepare_shader_seed.py --game botw --version botw190 --binding-abi discrete-v1`（耗时 `39.3s`），生成 `15,252` 个 variants（`9,725` 个启动着色器，`150` 个跳过）；再补充转译 Mac `rt-totk-native-v9-unverified` 中额外的 `6` 个原始 `.code` 着色器，最终在 `/tmp/botw-ios-step-b13/seed/botw190.discrete-v1` 产出 `15,258` 个 variants（共 `50,007` 个文件）。
2. **增量集合并 (`build_delta.py`) 与全量一致性审计**：
   - 将 iPad 已有 `1,671/1,671` 行 + Title `30/30` 行 + Mac `9,347/10,241` 行（其余 `894` 行为非 `botw190` 或缺失条目）合并去重，产出 `5,934` 行唯一 `pso_collected.tsv`，引用 `2,879` 个唯一 variants。
   - 准备 `/tmp/botw-ios-step-b13/push-delta`（新增 `2,173` 个 variants，对应 `4,346` 个 `_b.metal`/`_b.json` 与 `5,003` 个 `.variants` 文件）。
   - 对离线产物与合并集执行 100% 校验：
     - 首批 `15,252` 个 `_b.json`/`_b.metal`：`bad_json=0`、`bad_metal=0`，全部满足 `bind_model == "discrete-v1"`。
     - 推送合并包校验：`bad_json=0`、`bad_metal=0`、`missing_needed_variants=0`，零 `ab`-ABI / `MTL4ArgumentTable` / `gpuAddress` 残留。
3. **安全合并推送至真机**：
   - 由于 `afcclient put -rf` 在远端目录已存在时会嵌套 `<basename(src)>` 子目录，采用先在本地通过 `rsync` 合并生成 `/tmp/botw-ios-step-b13/merged-cache`（根目录 `13,570` 项、`5,934` 行 `pso_collected.tsv`、`1,947` 个 `pipelines/` 条目），移除远端旧目录后整包推送至 `Documents/shader-cache/rt-totk-native-v9-unverified.discrete-v1`。
   - 推送后真机回读校验：`wc -l post-pso_collected.tsv -> 5934`、`post-cache-ls.txt -> 13570`、`post-pipelines-ls.txt -> 1947`、`DATA_PRESERVED_OK`。

## 3. Apple5 预热防 OOM / 句柄限制保护与 `up_drain_inflight` 死锁修复

| 修改文件与位置 | 修改内容 | 作用与根因说明 |
| --- | --- | --- |
| `upstream/games/totk/third_party/d2r_gfx/gfx/nvn/totk_pipeline_archive.h` (`L270`) | `#if defined(GAME_OOT)` 扩展为 `#if defined(GAME_OOT) \|\| TARGET_OS_IPHONE` | 在 iOS 上启用 `PAR_OPEN_NOTE`，将同时打开的 `MTLBinaryArchive` 文件句柄数上限限制在 `TOTK_PAR_OPEN_MAX=256`，防止上千条管线集中读写时耗尽 iOS vnode/fd 配额或触发内存峰值 |
| `upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m` (`L8146`) | 确认保留 `const int fallback = g_present_apple5 ? WARM_RESIDENT_TITLE_KINDS : WARM_RESIDENT_TITLE_LOCAL;` | 确保 Apple5 默认落盘并采用分阶段安全常驻预热策略，避免 6 GB 内存设备 OOM |
| `upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m` (`L12104-L12108`) | `warm_threads()` 增加 `#if TARGET_OS_IPHONE`，按 `activeProcessorCount - 2` 钳位在 `1..4` | 在 iPadOS 上开启 4 线程标题前并发预热，同时避免过度并发导致临时 `MTLLibrary`/编译对象暴涨 |
| `upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m` (`L5592-L5593`) | 将 `atomic_fetch_sub(&g_up_pending, 1)` 移入 `g_up_slot_mu` 锁内且置于 `pthread_cond_broadcast(&g_up_slot_cv)` 之前 | 修复 Run 1 在 385 帧后触发 `TOTK-2026-11-21-142737.ips`（`SIGABRT` watchdog）暴露出的 `up_shared_alloc -> up_flush_and_drain -> up_drain_inflight` 丢失唤醒死锁（原先先 broadcast/unlock 再减 `g_up_pending`） |

## 4. 构建、单测、符号与数据完整性校验

- **本地单测**：
  - `TOTK_DISCRETE_MSL_DIR=/tmp/totk-abi-e2e/ios-metal-iphoneos-20261120 python3 -m unittest upstream.tests.test_discrete_native_draw upstream.tests.test_discrete_buffer_binding upstream.tests.test_discrete_texture_sampler_binding upstream.tests.test_native_main_encoders upstream.tests.test_warm_policy`
  - 结果：`Ran 14 tests in 7.355s - OK`
- **真机构建与安装**：
  - `xcodebuild` `Release-iphoneos`：`** BUILD SUCCEEDED **`
  - 汇编指令隔离检查：`otool -tV ... nvn_mtl.o | grep -c '\budot\b' -> 0`（`nvn_mtl_udot=0`）
  - 签名与部署：`STAGE_SIGN_OK`（`CFBundleIdentifier=org.macnativegames.botw.a12z`，安装 `databaseSequenceNumber: 2560`）
  - 数据保留校验：`DATA_PRESERVED_OK`
    - `post-fix-romfs.sha256` SHA-256：`23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774`
    - `post-fix-shader-seed.txt` SHA-256：`189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9`

## 5. 真机验证对比：Run 1（冷预热） vs Run 2（热重启）

两次真机运行均保持 `[present-probe]` 默认关闭（不传 `TOTK_PRESENT_PROBE`），采用 `TOTK_DEBUG_LOG=1`。

| 指标 | Run 1（冷预热，`/tmp/botw-ios-step-b13/run1-device.log`） | Run 2（修复死锁后热重启，`/tmp/botw-ios-step-b13/run2-device.log`，PID `3566`） |
| --- | --- | --- |
| 启动管线存档状态 | 基于已有 `973` 条扩展 | `管线存档：3204 条管线在 3204 个文件里（共 175.3 MB）` |
| 冷/热预热编译结果 | `管线存档：填充完成，新建渲染管线 2231 条、计算管线 0 条、网格管线 0 条，存档里已有 973 条，失败 / 缺着色器 3，用时 135.9 s`；`着色器库：预编译加载 0 个，现编 2201 个` | `管线存档：填充完成，新建渲染管线 0 条、计算管线 0 条、网格管线 0 条，存档里已有 3204 条，失败 / 缺着色器 3，用时 0.1 s` |
| 标题后 `pso_played.tsv` (324 行) | — | `324 / 324` 全部命中，耗时 `457.1 ms`（`渲染 取到 324 / 没存过 0 / 不认 0`） |
| 推进进度与首帧耗时 | `first presented frame` 于 `140.298s`；`[world] entered at frame 385` | `press A #6 at 8.3 s`；`[world] entered at frame 1908`；稳定推进至 **5,132 帧（187.27 s）** |
| 运行期未命中与即时转译 | — | `0..5132` 帧期间仅 `6` 次 `管线存档没命中`、`1` 次 `[shader] translated`（`cs b98505657eeb076e1f7b5003a5d84b20f5f655aadac73bee0d435e5cecedf7c1-6dd40068f678`），零 GPU / MTL4 / `gpuAddress` 报错 |
| 帧步调与内存占用 | 第 `300` 帧 `footprint 1169 MiB` | 第 `5100` 帧：`帧时间 p50 33.40 / p95 34.78 / 最大 58.18 ms（300 帧）`，`掉帧 19（丢弃 0），p99 50.02 ms`，`868 frames in 30.0 s (28.9 fps)`；第 `5100` 帧 `footprint 2885 MiB`（第 `3900` 帧峰值 `2974 MiB`，对比 Step B12 `2871 MiB`） |
| 设备端 `pipelines/` 终态 | 增长至 `6,409` 文件（`3,204` `.metalarchive` + `3,204` `.keys` + `identity.json`） | `6,409` 文件（`3,204` 条管线） |

## 6. 第 5,132 帧长稳跑尾声遗留问题（供 Step B14 跟进）

Run 2 验证了离散着色器离线批量转译与真机热重启预热零卡顿的核心目标，但在长稳推进至第 `5,132` 帧（`187.27 s`）时记录到两项需在 Step B14 专门处理的运行时问题：
1. **Argument Arena 耗尽崩溃**：Run 2 于第 ~`5,132` 帧（`187.27 s`）触发 `FATAL argument arena allocation failed: 4194304 bytes`（`arena_alloc_u` / `arena_new`）退出。
2. **音频累计欠载**：`187 s` 全程累计 `underruns 11`。
