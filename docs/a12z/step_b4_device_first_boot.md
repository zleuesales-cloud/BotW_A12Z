# BOTW A12Z: Step B4 增量覆盖安装与真机首次真实游戏启动日志报告

**执行时间**：2026-11-21  
**目标设备**：实体 iPad Pro 11-inch (2nd gen) `iPad8,9` / Apple A12Z GPU（UDID：`<YOUR_IPAD_UDID>`）  
**Bundle ID**：`org.macnativegames.botw.a12z`（Team ID / 签名证书 SHA1 已脱敏）  

---

## 1. 打包签名、覆盖安装与数据完整性核验

### 1.1 打包与签名
基于 Step B3 编译产物 `/tmp/botw-ios-step-b2/build-tree/build/ios-derived-b2/Build/Products/Release-iphoneos/TOTK.app`，在 `/tmp/botw-ios-step-b4/signed/TOTK.app` 中仅注入小体积元数据（`GameData/game-data`、`GameData/romfs/runtime-index.bin`、`GameData/shared-fonts`），保留设备沙盒内原有的 15 GB `Documents/GameData/romfs.bin.*` 与存档：
```text
$ codesign --verify --deep --strict --verbose=2 /tmp/botw-ios-step-b4/signed/TOTK.app
/tmp/botw-ios-step-b4/signed/TOTK.app: valid on disk
/tmp/botw-ios-step-b4/signed/TOTK.app: satisfies its Designated Requirement
```

### 1.2 增量覆盖安装与安装前后数据一致性校验
使用 `xcrun devicectl device install app --device <YOUR_IPAD_UDID> /tmp/botw-ios-step-b4/signed/TOTK.app` 覆盖安装，未卸载 App、未覆盖 `Documents/`。安装前后从设备取回标记文件校验字节完全一致：

| 标记文件 | 安装前 SHA-256 | 安装后 SHA-256 | 结果 |
| :--- | :--- | :--- | :--- |
| `Documents/GameData/romfs.sha256` | `23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774` | `23abd8cf3fdbc1c0b939dc83170abaf35db0a7a74297ffdfd8b7882ec4209774` | 完全一致 (`DATA_PRESERVED_OK`) |
| `Documents/GameData/shader-seed.txt` | `189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9` | `189d449485ed1c7c2d8eff8cda45848bc281212357dc1b38665234c2086311a9` | 完全一致 (`DATA_PRESERVED_OK`) |

---

## 2. 真机首次真实启动观测与阻塞点定位

### 2.1 第一轮默认参数启动 (`TOTK_DEBUG_LOG=1`, `TOTK_START_SCREEN=0`)
* **控制台退出码**：`1`
* **设备 `Documents/log.txt` 关键输出**：
  ```text
  [totk] ENTER original nn::init::Start (SDK 20.5)
  [totk] ENTER nnosInitialize
  [totk-mem] free 0x124c00000..0x16d2bc000
  [totk-mem] free 0x7000000000..(end of map, at most) 0x8000000000
  [totk] FAIL: reserve guest stack, heap and alias regions
  ```
* **根因**：`services.c` 中 `LOW_SIZE`（4.75 GiB）+ iOS 默认最小 `ALIAS_MIN_GIB`（4 GiB）= **8.75 GiB**，超出 A12Z（6 GB RAM、无 `extended-virtual-addressing` 权限）的可用连续虚拟地址预留上限；而 `pre-update.log` 历史记录显示该设备可使用 `alias 0x7130000000+0x40000000`（1 GiB alias，总计 5.75 GiB）。

### 2.2 第二轮补充 `TOTK_ALIAS_GIB=1` 启动
* **日志关键进展**：成功通过内存预留并挂载主 RomFS：
  ```text
  [totk] guest layout: alias 0x7130000000+0x40000000 stack 0x7010000000+0x10000000 heap 0x7030000000 (cap 0x100000000)
  ...
  [totk] native read-only RomFS ready: 41062 files
  ...
  [totk] ENTER nnMain
  ...
  [dm] [nvn-mtl] Apple5 without Metal4: native g_q execution and discrete-v1 shader binding ABI enabled
  [dm] [nvn-mtl] Metal 设备 Apple A12Z GPU
  ...
  [totk] FAIL: add-on content index missing (games/botw/scripts/extract_aoc_romfs.py, then make)
  ```
* **根因**：`TOTK.app/GameData` 未携带 `aoc/` 时 `main.m` 未设置 `TOTK_AOC`，而真机沙盒 `Documents/GameData/aoc` 已包含完整 DLC 1/2。

### 2.3 第三轮补充 `TOTK_ALIAS_GIB=1` + `TOTK_AOC=GameData/aoc` 启动（直抵 GPU 队列提交与首个图形指令）
* **完整推进节点（来自 `/tmp/botw-ios-step-b4/device-log-aoc.txt`）**：
  1. **游戏模块与系统服务全通**：`main`/`subsdk0`/`sdk` `DT_INIT` 完成，`41,062` 个 RomFS 文件、`7,094` 个 AOC DLC 文件、`6` 个共享字体、存档目录及 `CoreAudio` 立体声音频全部正常就绪；
  2. **Step B3 `discrete-v1` 运行时翻译与 ABI 隔离生效**：
     ```text
     [totk-gfx] [shader-seed] 【红】导入 App 附带的着色器失败：Startup shader seed binding ABI does not match the runtime；改为边玩边翻译
     ...
     [totk-gfx] [shader] runtime translation in the app (linked totk_xlate), cache /var/mobile/Containers/Data/Application/C4FA05BA-2693-4F35-AC30-C094BA7E8376/Documents/shader-cache/rt-totk-native-v9-unverified.discrete-v1
     ```
     （设备沙盒内原有的 `ab` 格式 `shader-seed-apple5` 被正确拒绝，进程内 `libtotk_xlate.a` 在 `.discrete-v1` 缓存目录就绪）
  3. **真实 NVN 指令录制并进入首次 GPU 提交**：
     记录到首轮游戏提交包含 `CommandBufferBindProgram=2`、`CommandBufferDrawElements=2`、`CommandBufferDrawElementsInstanced=92`、`CommandBufferClearColor=5`、`CommandBufferClearTexture=2`、`CommandBufferSetRenderTargets=10` 等，随后进入 `h_nvnQueueSubmitCommands` -> `submit_exec` -> `mtl_exec` -> `clear_tex` -> `enc_r_begin`。
  4. **首个图形执行硬阻塞点（Signal 6 Crash）**：
     ```text
     *** Terminating app due to uncaught exception 'NSInvalidArgumentException', reason: '-[MTL4RenderPassDescriptor pointCoordYFlipEnabled]: unrecognized selector sent to instance 0x10e999960'
     ...
     14  AGXMetalA12                         0x0000000255543b2c ...
     15  AGXMetalA12                         0x0000000255463788 ...
     16  TOTK                                0x0000000100da449c enc_r_begin + 132
     17  TOTK                                0x0000000100daee7c clear_tex + 2552
     18  TOTK                                0x0000000100d91a2c mtl_exec + 6836
     19  TOTK                                0x0000000100d35c54 submit_exec + 844
     20  TOTK                                0x0000000100d29bb0 h_nvnQueueSubmitCommands + 1200
     ```

---

## 3. 根因总结与 Step B5 精准修复清单

本轮真机启动已验证 **C# / NativeAOT / RomFS / AOC / Audio / 多线程 / `discrete-v1` ABI / NVN 录制链路全部跑通**，并在进入 A12Z Metal 编码第一刻命中了 3 个明确可修的问题：

1. **P0（直接崩溃原因）：Apple5 经典 Render Encoder 传入了 `MTL4RenderPassDescriptor`**
   * `clear_tex`、`rp_begin` 等路径仍以 `[MTL4RenderPassDescriptor new]` 构造描述符并强转为 `(MTLRenderPassDescriptor *)d` 传给 classic `[g_cb renderCommandEncoderWithDescriptor:]`，iOS 26 `AGXMetalA12` 在内部访问 `pointCoordYFlipEnabled` 时因类不匹配抛出 `NSInvalidArgumentException`。
   * **修复方向**：在 `nvn_mtl.m` 的 `enc_r_begin`（或所有构造 `MTL4RenderPassDescriptor` 的路径如 `clear_tex` / `rp_begin`）中，当 `g_present_apple5` 为真时构造或转换为原生 `[MTLRenderPassDescriptor renderPassDescriptor]`（拷贝 color/depth/stencil attachments、clearColor/clearDepth/clearStencil、loadAction/storeAction）。
2. **P1（iPad8,9 虚拟地址上限适配）：`services.c` 的 `ALIAS_MIN_GIB` 下探至 1 GiB**
   * 无 `extended-virtual-addressing` 权限的 6 GB A12Z iPad 无法分配 4.75 + 4 GiB，必须允许 `services.c` 在 iOS 上从 16 GiB 自动下探到 `1ULL` GiB（`ALIAS_MIN_GIB 1ULL`），无需手动传 `TOTK_ALIAS_GIB=1`。
3. **P1（DLC 索引自动挂载）：`main.m` 自动检测 `Documents/GameData/aoc`**
   * 当 App 内 `GameData/aoc` 不存在时，若沙盒 `Documents/GameData/aoc` 存在，自动将 `TOTK_AOC` 指向 `Documents/GameData/aoc`。
