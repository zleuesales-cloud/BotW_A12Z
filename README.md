# BotW A12Z — 《塞尔达传说：旷野之息》Apple A12Z (Apple5) iPadOS 原生 Metal 移植

> 基于 `ly05010419/MacNativeGames`（提交 `f770289b5d006a539b58698c88e41b80d8116e56`）的 Apple A12Z / Apple5 GPU 专项移植与深度优化工程。  
> 目标实体设备：**iPad Pro 11-inch (2nd gen, `iPad8,9`)**，Apple A12Z（6 GB 统一内存，`-mcpu=apple-a12`），**无 Metal 4 且仅支持 Argument Buffer Tier 1**。

---

## 一、快速开始：只需提供游戏本体即可打包 iPad

### a) 前置条件与免责说明
- 本仓库**不包含**任何游戏本体 ROM / RomFS、AOC/DLC、字体、密钥、着色器二进制缓存或 Apple 签名证书。
- 使用者只需自行准备：
  1. 合法导出的《塞尔达传说：旷野之息》（**BotW 1.9.0**）游戏本体目录（含解包或封装后的 `romfs/` 与 `romfs.bin`，以及可选的 `aoc/`、`shared-fonts/`）；
  2. 个人 Apple 开发者账号（Xcode 登录后的 Team ID，个人免费账号或付费账号均可）与已开启开发者模式的 **iPad Pro A12Z (`iPad8,9`)**。

### b) 目录放置与个人参数配置
- **游戏本体目录**（勿放入或提交至仓库，已写入 `.gitignore`）：放在工作区外的本地目录（例如 `<GAME_DATA_DIR>`，内含 `romfs/romfs.bin`、`romfs/index.json`，以及可选的 `aoc/`、`shared-fonts/`），通过 `install_device.py` 的 `--game-data` 参数推送进设备沙盒的 `Documents/GameData/`。
- **个人签名与设备参数**：

| 占位符 | 说明 | 获取方式 |
| --- | --- | --- |
| `<YOUR_TEAM_ID>` | Apple 开发者团队 ID（10 位字母数字） | Xcode > Settings > Accounts 查看 |
| `<YOUR_BUNDLE_ID>` | 自定义 App Bundle ID（如 `com.yourname.botw.a12z`） | 自行命名，与签名证书绑定 |
| `<YOUR_IPAD_UDID>` | iPad 设备 UDID（或设备名称） | `xcrun devicectl list devices` 或 `idevice_id -l` |
| `<GAME_DATA_DIR>` | 本地 BotW 1.9.0 游戏数据根目录 | 自行合法导出 |

### c) 完整可复制编译、打包与安装流程

```bash
# 1. 进入仓库并设置你的个人参数与游戏本体路径
export GAME_DATA_DIR=/path/to/botw190_data
export YOUR_TEAM_ID=<YOUR_TEAM_ID>
export YOUR_BUNDLE_ID=<YOUR_BUNDLE_ID>
export YOUR_IPAD_UDID=<YOUR_IPAD_UDID>

# 2.（可选）离线编译 discrete-v1 着色器种子，降低首次进游戏编译卡顿
#    严格注意：只允许使用 discrete-v1 Apple5 着色器种子，严禁混入 Mac 端 ab-ABI 或 .metalarchive
python3 upstream/games/totk/ios/tools/compile_seed.py <DISCRETE_V1_SEED_DIR> iphoneos

# 3. Release-iphoneos (-mcpu=apple-a12) 构建未签名 IPA（游戏本体外置至 Documents/GameData）
python3 upstream/games/totk/ios/tools/build_ipa.py \
  --tree upstream/games/totk \
  --out build/BotW_A12Z.ipa \
  --version botw190 \
  --logs build/logs \
  --platform ios \
  --image-outside \
  --app-name "BotW A12Z" \
  --bundle-id "$YOUR_BUNDLE_ID"

# 4. 使用个人 Team ID 重签名、推送游戏本体并安装到 iPad
python3 upstream/games/totk/ios/tools/install_device.py \
  build/BotW_A12Z.ipa \
  --device "$YOUR_IPAD_UDID" \
  --team "$YOUR_TEAM_ID" \
  --bundle-id "$YOUR_BUNDLE_ID" \
  --game-data "$GAME_DATA_DIR/romfs/romfs.bin"
```

> 说明：步骤 3 在构建时会按目标 `iphoneos` 启用 `-mcpu=apple-a12`（无 `udot` 指令；可通过 `xcrun -sdk iphoneos otool -tV ...` 验证 `nvn_mtl_udot=0`）。若后续仅更新 IPA 代码，重新运行 `install_device.py` 并保留相同的 `--team` 与 `--bundle-id`，会自动保留设备 `Documents/` 中的存档、`romfs.bin`（通过 `romfs.sha256` 校验跳过重复拷贝）及本地着色器缓存。

### d) 首次进游戏推荐画面设置
进入游戏设置（或 iOS「设置」>「BotW A12Z」）推荐采用以下经真机长稳验证的配置：
- **画面预设**：**自定义**
- **MetalFX 超分**：**关闭**（避免 Apple5 额外显存与耗时）
- **TAA 抗锯齿**：**关闭**
- **分辨率与帧率**：**底座模式 1080p (`1920x1080`) 30 FPS** 或 **掌机模式 720p (`1280x720`) 30 FPS**（两档均已通过 A12Z 真机 3,300+ 帧实测，稳态约 `33.4 ms / 30 FPS`，零崩溃、零音频欠载）

---

## 二、项目亮点与核心适配（Steps B1–B15）

针对 Apple5 GPU 不支持 Metal 4（`MTL4CommandQueue` / `MTL4ArgumentTable` / `gpuAddress` 绑定模型）与 6 GB 物理内存限制，本项目完成了从离线着色器转译到底层原生 Metal 执行全链路的彻底重写与稳定性打磨：

1. **`discrete-v1` 离散槽位着色器 ABI 与经典 Metal 后端**
   - 着色器转译器新增 `--binding-abi discrete-v1`，完全避开基于 `gpuAddress` 的 Argument Buffer。
   - 渲染、计算、Blit 与 Present 全部运行在经典 `MTLCommandQueue` / `MTLRenderCommandEncoder` / `MTLComputeCommandEncoder` 上，实现 **Apple5 路径零 `MTL4*` / 零 `gpuAddress` 调用**，并通过 `otool -tV` 校验 `nvn_mtl_udot=0`。
2. **GPU 计算纹理转码与显存压降（`BC -> ASTC / EAC`）**
   - 在经典 `MTLComputeCommandEncoder` 离散路径下完整打通 `BC1` 与 `BC2/3/4/5/7 -> ASTC 4x4 / EAC` 转码、像素格式/通道视图映射及 `bright_lut`，彻底告别 CPU 软件解压的显存暴涨与卡顿。
   - 启用全 iOS `res_over_working_set()`（Apple5 默认 `88%` 工作集上限）与更高安全裕度的 `pkg_evict_sweep()`（`400 / 550 / 250 MB`）。
3. **全量着色器离线批量转译与真机热预热**
   - 对 19,672 个原始 NVN 着色器离线转译 `15,258` 份 `discrete-v1` 缓存并同步平台无关管线清单，真机持久化 **`3,263` 条**本地 `.metalarchive` 管线存档，启动预热仅 **`0.1–0.2 s`**、3D 大世界运行期管线未命中近乎归零（`0–4` 次、`0` 次即时转译）。
4. **Argument Arena 与大世界稳定性根治**
   - `dummy_buf()` iOS 默认由 64 MiB 降至 **4 MiB** 并于 `mtl_init()` 预分配常驻，根除进世界首绑存储缓冲时的 `nil` 崩溃。
   - Argument Arena iOS 块粒度由 `4 MiB` 调整为 **`1 MiB`**，放宽 `arena_trim` 至 `idle_submits = 150` / `keep_max = 32` 消除分配抖动，并加入精确写回引脚判断（`arena_is_pend_pinned`）、`defer_drain()` 排空、`g_cv` 等待复用与 `64 KiB` 步进降级兜底，已通过 **9,900 帧（352 秒）** 长稳压测验证零崩溃、零 Jetsam。
5. **Apple5 离散绑定状态缓存去重（-82.5% 冗余调用）**
   - 为经典 render / compute 编码器新增平铺状态缓存（`g_disc_r` / `g_disc_c`）与 `disc_cache_*` 包装器，对管线、深度模板状态及 Buffer / Texture / Sampler 绑定进行按槽位去重（仅偏移变更走轻量 `set*BufferOffset:atIndex:`），跳过 **`82.5%–82.6%`** 的冗余 Metal 绑定调用。
6. **iOS 音频零欠载（`underruns 0`）**
   - 针对 iPadOS RemoteIO `1024` 帧（`21.33 ms`）回调特性，将 iOS 默认预充水位提高至 **`40 ms`（`AUDIO_PREFILL_DEFAULT_BLOCKS=8`）**，消除此前每轮 `16` 帧的周期性欠载，实现 352 秒 **`underruns 0`**。

---

## 三、真机实测表现对比（iPad Pro A12Z，`present-probe` 默认关闭）

| 测试阶段 / 模式 | 推进帧数与时长 | 稳态帧时间 (`p50 / p95 / max`) | 掉帧统计（第 2101–3300 帧） | GPU / 提交耗时 | Footprint（稳态 / 峰值） | Arena 抖动 (`sub=6600/6900`) | 音频欠载 (`underruns`) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **B14 底座 1080p 长稳** | **9,900 帧** (352 s) | 第 9900 帧：`33.32 / 35.47 / 52.50 ms` | 102 帧 (`25+25+30+22`) | 第 3300 帧：提交 `16.45` / GPU `15.51 ms` | `2627 / 2645 MiB` | `kept=19`, `released=4,575` 块 | **0** |
| **B15 掌机 720p (`1280x720`)** | **3,300+ 帧** (128 s) | 第 3300 帧：`33.46 / 35.00 / 46.48 ms` | **19 帧 (-81.4%)** | **GPU `14.30 / p95 16.37 ms`** | **`2566 / 2584 MiB`** | `kept=28`, **`released=130` 块**（稳态 0 新增） | **0** |
| **B15 底座 1080p (`1920x1080`)** | **3,300+ 帧** (128 s) | 第 3300 帧：**`33.45 / 34.87 / 42.41 ms`** (`p99 33.35`) | **5 帧 (-95.1%，第 2700/3300 帧 0 掉帧)** | 提交 `17.07 / max 19.36` / GPU `17.13 ms` | `2843 / 2930 MiB` | `kept=28`, **`released=279` 块 (-93.9%)** | **0** |

> 全程零 `TOTK-*.ips` 崩溃、零 `TOTK` Jetsam、零 GPU/MTL4/gpuAddress 报错、`温控 0`，离散绑定跳过率稳定在 **`82.5%–82.6%`**。

---

## 四、仓库结构说明

```text
BotW_A12Z/
├── README.md                  # 本中文说明文档
├── project.json               # 项目依赖版本与基线审计记录
├── evidence/                  # Apple5 真机能力快照（APPLE5_CAPABILITIES.json 等）
├── docs/a12z/                 # Step B1–B15 全流程调试、剖析与验证报告
└── upstream/                  # 移植后的运行时、NVN-to-Metal 渲染器与单测套件
    ├── games/totk/
    │   ├── ios/               # iOS/iPadOS 宿主与画质设置界面
    │   ├── runtime/           # HLE 运行时、音频混音器 (audio_mixer.c) 与内存服务
    │   ├── scripts/           # 离线着色器批量转译与清单工具
    │   └── third_party/d2r_gfx/gfx/nvn/
    │       ├── nvn_mtl.m      # 核心 Apple5 经典 Metal + discrete-v1 渲染器
    │       ├── hle_nvn.c      # NVN 队列提交、帧步调与遥测统计
    │       └── ...
    ├── shared/                # 共享 NVN 扩展（含 MetalFX/TAA/上传适配）
    └── tests/                 # 离散绑定与预热策略单元测试（14 项）
```

### 阶段文档索引（`docs/a12z/`）
- [Step B1：仓库与 Apple5 能力审计](docs/a12z/step_b1_audit.md)
- [Step B2：解锁绘制与基础约束](docs/a12z/step_b2_unlock_draw.md)
- [Step B3：激活 `discrete-v1` 着色器 ABI](docs/a12z/step_b3_activate_discrete_abi.md)
- [Step B4：真机首次启动与增量安装验证](docs/a12z/step_b4_device_first_boot.md)
- [Step B5：阻塞项修复与首帧出图](docs/a12z/step_b5_device_render_fix.md)
- [Step B6：块解码与标题画面](docs/a12z/step_b6_blk_decode_and_title.md)
- [Step B7：分块上传与真机联调](docs/a12z/step_b7_chunked_upload_and_device.md)
- [Step B8：黑屏、MTL4 残留清理与显存排查](docs/a12z/step_b8_blackscreen_mtl4_and_vram.md)
- [Step B9：离散绑定闭环与真机验证](docs/a12z/step_b9_discrete_bindings_and_device.md)
- [Step B10：关闭常驻探针与重开 ASTC 转码](docs/a12z/step_b10_probe_gate_and_astc.md)
- [Step B11：`bright_lut` 离散化、GPU ASTC 与帧步调](docs/a12z/step_b11_bright_lut_gpu_astc_and_pacing.md)
- [Step B12：开启 `tex_tc`、30 FPS 阈值校准与启动核查](docs/a12z/step_b12_tex_tc_fps_and_tap_launch.md)
- [Step B13：Mac 着色器清单盘点、离线转译与预热](docs/a12z/step_b13_shader_inventory_discrete_and_warmup.md)
- [Step B14：Arena 4 MiB 崩溃根治、内存压降与 9900 帧压测](docs/a12z/step_b14_arena_continue_crash_and_soak.md)
- [Step B15：掌机 720p 闪退定罪修复、掉帧剖析与性能优化](docs/a12z/step_b15_handheld_crash_and_perf_opt.md)

---

## 五、本地单测与构建规范

1. **运行全部 14 项本地离散绑定与预热单元测试**（需提供生成的 `discrete-v1` 标题着色器夹具目录）：
   ```bash
   TOTK_DISCRETE_MSL_DIR=/tmp/totk-abi-e2e/ios-metal-iphoneos-20261120 \
   python3 -m unittest \
     upstream.tests.test_discrete_native_draw \
     upstream.tests.test_discrete_buffer_binding \
     upstream.tests.test_discrete_texture_sampler_binding \
     upstream.tests.test_native_main_encoders \
     upstream.tests.test_warm_policy -v
   ```
2. **真机编译要点**：
   - iOS CPU 编译目标保持 `-mcpu=apple-a12`，禁止生成 `udot` 等新架构指令（需验证 `nvn_mtl.o` 反汇编零 `udot`）。
   - 严禁将 Mac 端 `ab`-ABI MSL 或 `.metalarchive` 混入 Apple5 运行目录。

---

## 六、上游与版权声明

- 上游代码与开源组件均保留原有许可证文件。
- 本仓库**不包含**任何游戏本体 ROM / RomFS、DLC、个人存档或 Apple 开发者签名证书/私钥；所有测试文档中的个人 Apple ID 与证书标识已脱敏。
