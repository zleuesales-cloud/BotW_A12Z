# BOTW A12Z: Route B 解除绘制拦截前只读审计报告 (Step B1)

**审计时间**：2026-11-20  
**审计性质**：只读静态代码与配置审计（严格无源码修改、无 commit、无编译运行）  
**审计对象**：`BotW_A12Z` 仓库及 Apple5 / A12Z 移植管线  

---

## 1. 仓库与工作区基线

### 1.1 仓库绝对路径与 Git 状态
* **仓库绝对路径**：`~/Documents/NS/ipad pro/BotW_A12Z`
* **当前分支 (`git branch --show-current`)**：
  ```
  master
  ```
* **最近 6 次提交 (`git log -n 6 --oneline`)**：
  ```
  df90f25 (HEAD -> master) validation/device: pass native copy-alias replay on physical A12Z
  1792f3d validation/device: record first A12Z startup failure before GPU probe
  a54cf47 gfx/apple5: bind discrete textures and samplers through native slots
  a8982c1 ios/apple5: run production copy-alias probe without game-data startup gates
  c1719c0 gfx/apple5: bind discrete constant buffers with native objects and offsets
  ac84cbf validation: confirm game modules match pinned BOTW baseline
  ```
* **工作区状态 (`git status -sb`)**：
  ```
  ## master
   M upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m
  ?? .DS_Store
  ?? upstream/tests/test_discrete_native_draw.py
  ```

### 1.2 iOS 构建配置与环境参数核对
* **Bundle ID 配置**：
  * `project.json` (第 194 行)：`"bundle_id": "org.macnativegames.botw.a12z"`
  * `compile-link-manifest.json` (`output.bundle_id`)：`"bundle_id": "org.macnativegames.botw"`
  * `upstream/games/totk/ios/project.yml` (第 3、63 行)：
    ```yaml
    options:
      bundleIdPrefix: org.macnativegames
    settings:
      PRODUCT_BUNDLE_IDENTIFIER: ${TOTK_BUNDLE_ID}
    ```
  * `upstream/games/totk/ios/tools/install_device.py` (第 343 行)：
    `bundle_id = a.bundle_id or f'{built_bundle_id(a.ipa, team)}.{team.lower()}'`（默认携带 team 后缀，例如 `org.macnativegames.botw.<team>`）。
* **Team ID 配置**：
  * 本机有效开发者签名证书（执行 `security find-identity -v -p codesigning` 原始输出，敏感标识已脱敏）：
    ```
    1) <CERT_SHA1_REDACTED> "Apple Development: <REDACTED> (<TEAM_ID>)"
       1 valid identities found
    ```
  * Team ID 为：`<TEAM_ID>`。
  * `upstream/games/totk/ios/tools/install_device.py` (第 84 行)：`DEVELOPMENT_TEAM: {team}`。
* **CPU 架构目标 (`-mcpu=apple-a12`)**：
  * `upstream/games/totk/ios/project.yml` (第 91 行)：
    ```yaml
    OTHER_CFLAGS: $(inherited) -mcpu=apple-a12 -I../runtime -I../third_party/d2r_gfx/gfx/nvn -I../third_party/d2r_gfx/gfx/nvn/include -IGen -I../build
    ```
  * `project.json` (第 106 行)：`"ios_compile": "arm64-apple-ios26.0 -mcpu=apple-a12; nvn_mtl.m object"`。
  * `compile-link-manifest.json` (`toolchain.cpu`)：`"cpu": "apple-a12"`, `"deployment_target": "26.0"`。
* **36-bit / 39-bit 虚拟地址空间配置 (`services.c`)**：
  * 文件路径：`upstream/games/totk/runtime/services.c` (第 25-45 行)：
    ```c
    /* Guest address layout. The SDK encodes IPC buffer addresses with 39 bits (HIPC descriptor bits 36..38),
       so all guest memory must lie below 2^39. Darwin arm64 keeps 0x10_0000_0000..0x70_0000_0000 reserved,
       leaving exactly 64 GiB at 0x70_0000_0000 for the alias region (the real 39-bit Horizon size, which the
       game's allocators depend on); the stack and heap regions are reserved separately below it. */
    #define GUEST_ADDRESS_LIMIT (1ULL<<39)
    #define ALIAS_SPACE_AT 0x7000000000ULL
    #define ALIAS_SIZE_MAX (64ULL<<30)
    #if TARGET_OS_IPHONE
    #define ALIAS_DEFAULT_GIB 16
    #define ALIAS_MIN_GIB 4ULL
    #else
    #define ALIAS_DEFAULT_GIB 64
    #endif
    ...
    #define LOW_SEARCH_FROM 0x300000000ULL
    #if TARGET_OS_IPHONE
    #define LOW_SIZE (HEAP_OFF+HEAP_CAP)   /* iOS: no slack after the heap cap; address space is scarce */
    ```
* **RomFS 与 GameData 挂载路径配置**：
  * 文件路径：`upstream/games/totk/ios/Sources/main.m` (第 76, 744, 766, 814 行)：
    ```objc
    NSString *gameData = [docs stringByAppendingPathComponent:@"GameData"];
    setenv("TOTK_ROMFS_DATA", gameData.fileSystemRepresentation, 1);
    setenv("TOTK_ROMFS", [res stringByAppendingPathComponent:@"romfs"].fileSystemRepresentation, 1);
    setenv("TOTK_ROMFS_PATCHES", [res stringByAppendingPathComponent:@"romfs-patches"].fileSystemRepresentation, 1);
    setenv("TOTK_ROMFS_PROOF_DIR", docs.fileSystemRepresentation, 1);
    ```
  * 文件路径：`upstream/games/totk/runtime/romfs.c` (第 290-305 行)：
    * 从 `TOTK_ROMFS`（默认 `extracted/botw190/romfs`）读取 `runtime-index.bin`（元数据索引）；
    * 从 `TOTK_ROMFS_DATA`（在 iOS 上由 `main.m` 指向 `Documents/GameData`）读取大体积游戏镜像 `romfs.bin`（或 `romfs.bin.000` 分卷）；
    * 大小与 SHA256 由 `build/game-data/botw190/romfs-config.txt` 中的 `ROMFS_IMAGE_SIZE`、`ROMFS_IMAGE_SHA256` 校验锁定。

---

## 2. 未提交的原生绘制实现审计

### 2.1 `nvn_mtl.m` 关键修改源码与行号审计
修改文件：`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`

#### (1) `classic_resolve_buffer` (第 13442-13452 行)
```objc
static id<MTLBuffer> classic_resolve_buffer(uint64_t address, uint64_t length, uint64_t *offset, const char *what) {
    void *object = NULL;
    if (!address || !length || !offset || length > UINT64_MAX - address || !mtl_addr(address, length, &object, offset))
        MDIE("Apple5 %s address %#llx+%#llx is not one native buffer span", what,
             (unsigned long long)address, (unsigned long long)length);
    id<MTLBuffer> buffer = (__bridge id<MTLBuffer>)object;
    if (!buffer || *offset > buffer.length || length > buffer.length - *offset)
        MDIE("Apple5 %s address %#llx+%#llx exceeds its native buffer", what,
             (unsigned long long)address, (unsigned long long)length);
    return buffer;
}
```

#### (2) `classic_set_vertex_buffer` (第 13453-13459 行)
```objc
static void classic_set_vertex_buffer(id<MTLRenderCommandEncoder> encoder, NSUInteger slot,
                                      id<MTLBuffer> buffer, NSUInteger offset) {
    if (!encoder || slot >= 31 || !buffer || offset >= buffer.length || (offset & 3))
        MDIE("invalid Apple5 vertex buffer slot %lu object %p offset %lu", (unsigned long)slot,
             (__bridge void *)buffer, (unsigned long)offset);
    [encoder setVertexBuffer:buffer offset:offset atIndex:slot];
}
```

#### (3) `classic_bind_vertex_address` (第 13460-13466 行)
```objc
static void classic_bind_vertex_address(id<MTLRenderCommandEncoder> encoder, NSUInteger slot,
                                        uint64_t address, uint64_t size) {
    uint64_t offset = 0;
    id<MTLBuffer> buffer = classic_resolve_buffer(address, 1, &offset, "vertex stream");
    classic_set_vertex_buffer(encoder, slot, buffer, (NSUInteger)offset);
    use_rec("buf", address, size ? size : 1, 0, NULL);
}
```

#### (4) `classic_draw_arrays` (第 13467-13475 行)
```objc
static void classic_draw_arrays(id<MTLRenderCommandEncoder> encoder, MTLPrimitiveType primitive,
                                int first, int count, int instances, int base_instance) {
    if (!encoder || first < 0 || count < 0 || instances < 0 || base_instance < 0)
        MDIE("invalid Apple5 arrays draw first %d count %d instances %d baseInstance %d", first, count, instances, base_instance);
    if (!count || !instances) return;
    if (instances == 1 && !base_instance) [encoder drawPrimitives:primitive vertexStart:(NSUInteger)first vertexCount:(NSUInteger)count];
    else [encoder drawPrimitives:primitive vertexStart:(NSUInteger)first vertexCount:(NSUInteger)count
                    instanceCount:(NSUInteger)instances baseInstance:(NSUInteger)base_instance];
}
```

#### (5) `classic_index_type` 与 `classic_draw_indexed` (第 13476-13498 行)
```objc
static MTLIndexType classic_index_type(int nvn_type) {
    if (nvn_type == 1) return MTLIndexTypeUInt16;
    if (nvn_type == 2) return MTLIndexTypeUInt32;
    MDIE("Apple5 indexed draw requires 16-bit or 32-bit indices; NVN type %d needs the unported widening path", nvn_type);
}
static void classic_draw_indexed(id<MTLRenderCommandEncoder> encoder, MTLPrimitiveType primitive,
                                 int count, int nvn_type, uint64_t index_address,
                                 int instances, int base_vertex, int base_instance) {
    if (!encoder || count < 0 || instances < 0 || base_instance < 0)
        MDIE("invalid Apple5 indexed draw count %d instances %d baseInstance %d", count, instances, base_instance);
    if (!count || !instances) return;
    MTLIndexType type = classic_index_type(nvn_type);
    uint64_t width = type == MTLIndexTypeUInt16 ? 2 : 4, length = (uint64_t)count * width, offset = 0;
    if (length / width != (uint64_t)count) MDIE("Apple5 indexed draw byte count overflow");
    id<MTLBuffer> buffer = classic_resolve_buffer(index_address, length, &offset, "index buffer");
    if (offset % width) MDIE("Apple5 index buffer offset %llu is not aligned to %llu bytes",
                             (unsigned long long)offset, (unsigned long long)width);
    use_rec("buf", index_address, length, 0, NULL);
    [encoder drawIndexedPrimitives:primitive indexCount:(NSUInteger)count indexType:type indexBuffer:buffer
                 indexBufferOffset:(NSUInteger)offset instanceCount:(NSUInteger)instances
                        baseVertex:(NSInteger)base_vertex baseInstance:(NSUInteger)base_instance];
}
```

#### (6) `classic_draw_arrays_indirect` 与 `classic_draw_indexed_indirect` (第 13499-13520 行)
```objc
static void classic_draw_arrays_indirect(id<MTLRenderCommandEncoder> encoder, MTLPrimitiveType primitive,
                                          uint64_t arguments_address) {
    uint64_t offset = 0;
    id<MTLBuffer> arguments = classic_resolve_buffer(arguments_address, 16, &offset, "arrays indirect record");
    if (offset & 3) MDIE("Apple5 arrays indirect record offset %llu is not four-byte aligned", (unsigned long long)offset);
    use_rec("buf", arguments_address, 16, 0, NULL);
    [encoder drawPrimitives:primitive indirectBuffer:arguments indirectBufferOffset:(NSUInteger)offset];
}
static void classic_draw_indexed_indirect(id<MTLRenderCommandEncoder> encoder, MTLPrimitiveType primitive,
                                          int nvn_type, uint64_t index_address, uint64_t arguments_address) {
    MTLIndexType type = classic_index_type(nvn_type);
    uint64_t index_offset = 0, arguments_offset = 0;
    id<MTLBuffer> indices = classic_resolve_buffer(index_address, 1, &index_offset, "indirect index buffer");
    id<MTLBuffer> arguments = classic_resolve_buffer(arguments_address, 20, &arguments_offset, "indexed indirect record");
    uint64_t width = type == MTLIndexTypeUInt16 ? 2 : 4;
    if (index_offset % width) MDIE("Apple5 indirect index offset %llu is not aligned to %llu bytes",
                                   (unsigned long long)index_offset, (unsigned long long)width);
    if (arguments_offset & 3) MDIE("Apple5 indexed indirect record offset %llu is not four-byte aligned", (unsigned long long)arguments_offset);
    use_rec("buf", index_address, 1, 0, NULL); use_rec("buf", arguments_address, 20, 0, NULL);
    [encoder drawIndexedPrimitives:primitive indexType:type indexBuffer:indices indexBufferOffset:(NSUInteger)index_offset
                    indirectBuffer:arguments indirectBufferOffset:(NSUInteger)arguments_offset];
}
```

#### (7) `draw_inner` 调度分支接入 (第 20340-20440 行)
* 间接数组绘制：`if (g_present_apple5) classic_draw_arrays_indirect((id<MTLRenderCommandEncoder>)en, mp, dw->ind);`
* 普通/实例化数组绘制：`if (g_present_apple5) classic_draw_arrays((id<MTLRenderCommandEncoder>)en, mp, dw->first, dw->count, dw->instances ? dw->instances : 1, dw->base_instance);`
* 索引绘制与间接索引绘制：
  ```objc
  if (g_present_apple5) {
      if (di->prim == NVN_DRAW_PRIMITIVE_QUADS || di->prim == NVN_DRAW_PRIMITIVE_QUAD_STRIP || di->prim == NVN_DRAW_PRIMITIVE_POLYGON)
          MDIE("Apple5 indexed primitive %d needs its NVN conversion path", di->prim);
      if (di->itype == 0) MDIE("Apple5 8-bit indices need the NVN widening path");
      if (di->ind) classic_draw_indexed_indirect((id<MTLRenderCommandEncoder>)en, mp, di->itype, di->ibuf, di->ind);
      else classic_draw_indexed((id<MTLRenderCommandEncoder>)en, mp, di->count, di->itype, di->ibuf,
                               di->instances, di->base_vertex, di->base_instance);
      return;
  }
  ```
* 未实现的图元转换（Quad Strip, Quads, Polygon, 8-bit index）在 Apple5 路径下显式 `MDIE` 拦截，杜绝未定义行为。

### 2.2 `upstream/tests/test_discrete_native_draw.py` 验证内容
* **输入配置**：
  * 着色器输入：依赖 `TOTK_DISCRETE_MSL_DIR` 中的真实 BOTW 标题顶点着色器（VS Key: `6073f8d9957a606cdb77c17d5a496c47f79e40d27a9d5b661b20403fb3c08e98-b7530f698e76_b`）及其反射 JSON。
  * 源码提取：动态从 `nvn_mtl.m` 源码提取上述 9 个生产函数的 AST 片段，注入探针 Harness 动态编译执行。
* **断言与验证覆盖**：
  * **元数据断言**：验证反射元数据中 `stage == 'vs'`、`bind_model == 'discrete-v1'`、`('cbuf', 'buffer', 0)` 与 `('c1', 'buffer', 1)`。
  * **绘制行为 4 项覆盖**：
    1. `Mode 0`：`classic_bind_vertex_address` 结合 `classic_draw_indexed`（16-bit 索引、基顶点 `baseVertex=1`、2 个实例）。
    2. `Mode 1`：`classic_draw_arrays_indirect` 间接数组绘制。
    3. `Mode 2`：`classic_draw_indexed_indirect` 间接索引绘制。
    4. `Mode 3`：`classic_draw_arrays` 点图元绘制并验证 `baseInstance=1` 实例偏移。
  * **GPU 回读比对断言**：
    * 验证 Targets 0..2 渲染输出像素精准符合着色器计算期望颜色值（`{64,128,191,255}` 与 `{32,64,96,255}`）。
    * 验证 Target 3 点坐标精确落在第 2 实例位置区间 `x in [10..14]`，确认 `baseInstance` 偏移行为正确。

---

## 3. discrete-v1 槽位命名空间与冲突核查

根据 `nvn_mtl.m`、`nvn_refl.h` 及 `Service.cs`，当前 Apple5 离散模式槽位规则如下：

### 3.1 Buffer 槽位分配 (`[[buffer(n)]]`)
* **UBO (`cbuf`)**：
  * 占用 Metal 槽位：由 SPIRV-Cross 反射生成，通常分配在 `[[buffer(0..13)]]`（`nvn_mtl.m` 第 14880-14915 行）；
  * 绑定方法：`discrete_bind_buffer(en, nvn_stage, b, direct_buffer, direct_offset)`；
  * 限制（第 13431 行）：`binding->metal_slot < 31`。
* **C1 (内置常量缓冲)**：
  * 占用 Metal 槽位：由着色器离散反射分配（如标题着色器分配在 `[[buffer(1)]]`）；
  * 绑定方法：`nvn_mtl.m` 第 15078 行调用 `discrete_bind_buffer(en, nvn_stage, b, cb, sh->c1buf ? sh->c1off : 0)`。
* **SSBO (`sbuf`) 与 Driver 常量 (`drvcb`)**：
  * **现状**：目前 `discrete-v1` 的直绑逻辑**未实现 SSBO 与 Driver 常量直接下发**！
  * 证据（第 13431 行）：`discrete_bind_buffer` 显式限定 `(binding->kind != NVN_RB_CBUF && binding->kind != NVN_RB_C1)` 则 `MDIE`；在 `encode_args`（第 15040-15096 行）遇到 SSBO / Driver 常量时仍走 `slots[aid] = ...`（即向丢弃的临时参数缓冲数组写值），没有调用任何 Metal 命令编码器绑定。
* **Vertex Buffer (顶点缓冲流)**：
  * 占用 Metal 槽位：**固定占用 `[[buffer(16..30)]]`**。
  * 源码位置：`nvn_mtl.m` 第 19005-19010 行：
    ```objc
    classic_set_vertex_buffer((id<MTLRenderCommandEncoder>)en, 16 + stream, (__bridge id<MTLBuffer>)mb, (NSUInteger)mo);
    ```
  * Fallback 顶点输入：占用 `[[buffer(30)]]`（第 19013 行）。
  * 分层通道常量 (`LAY_BUF_INDEX`)：占用 `[[buffer(14)]]`（第 6871、20215 行）。
* **撞槽冲突检测机制**：
  * 源码位置：`nvn_mtl.m` 第 14730-14755 行：
    ```objc
    static uint32_t discrete_active_buffer_slots(const NvShader *sh, int stage) {
        uint32_t active = 0;
        if (stage == 0) {
            active = discrete_vertex_descriptor_slots(sh, NULL); // 标记 16+stream 及 30
            if (g_lay.active) active |= UINT32_C(1) << LAY_BUF_INDEX; // 标记 14
        } else if (stage == 1 && sh->hdr_fn && atomic_load(&g_hdr_frame_enabled)) {
            active |= UINT32_C(1) << 30;
        }
        return active;
    }
    static void discrete_validate_active_buffer_slots(const NvShader *sh, int stage) {
        uint32_t active = discrete_active_buffer_slots(sh, stage);
        for (int i = 0; i < sh->rf.nb; i++) {
            const nvn_rbind *b = &sh->rf.b[i];
            if (b->metal_class == NVN_METAL_CLASS_BUFFER && b->metal_slot < 31 &&
                (active & (UINT32_C(1) << b->metal_slot)))
                MDIE("discrete shader %s stage %d buffer slot %u collides with an active NVN render input", sh->key, stage, b->metal_slot);
        }
    }
    ```
  * **核查结论**：Vertex Buffer 占 `16..30`，Layer 占 `14`，UBO/C1 必须在 `0..13` 或 `15`；每次编码参数前均调用 `discrete_validate_active_buffer_slots`，**若有任何 UBO 槽位 >= 14 侵入顶点流或分层流，会立即硬崩溃拦截，不存在静默冲突**。

### 3.2 Texture 与 Sampler 槽位 (`[[texture(n)]]`, `[[sampler(n)]]`)
* **Texture 槽位分配与上限**：
  * 绑定函数：`discrete_bind_texture`（第 13520-13529 行）；
  * 槽位上限：`binding->metal_slot < 96`（Apple5 硬件上限 96，超出即 `MDIE`）；
  * 分发阶段：顶点阶段使用 `[render setVertexTexture:atIndex:]`，片元阶段使用 `[render setFragmentTexture:atIndex:]`。
* **Sampler 槽位分配与上限**：
  * 绑定函数：`discrete_bind_sampler`（第 13530-13539 行）；
  * 槽位上限：`binding->metal_slot < 16`（Apple5 硬件上限 16，超出即 `MDIE`）；
  * 分发阶段：顶点阶段使用 `[render setVertexSamplerState:atIndex:]`，片元阶段使用 `[render setFragmentSamplerState:atIndex:]`。
* **反射静态检查**：
  * 载入着色器时由 `discrete_validate_slots`（第 8494-8512 行）执行静态断言：
    `buffers < 31`、`textures < 96`、`samplers < 16`，并对同 class 内重叠 slot 抛出 `duplicate active discrete slot` 异常。

---

## 4. 着色器翻译/加载链路核查

### 4.1 路线 B 着色器获取、翻译与加载流程
* **离线打包模式**：
  * 脚本：`upstream/games/totk/scripts/prepare_shader_seed.py`（第 788-825 行）与 `build_shader_libs.py`；
  * 指定 `--binding-abi discrete-v1` 参数，将 SPIR-V 编译生成直接槽位的 MSL 2.4 代码（`.metal`）及反射元数据（`.json`），打包并编译进 `.metallib`，最终落盘在 App 的 `shader-seed/` 目录。
* **iOS 运行时动态翻译 (JIT)**：
  * 源码位置：`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/totk_shader_rt.m`（第 55-63、312、780 行）；
  * 实现机制：由于 iOS 禁止创建独立进程，代码通过静态链接的 .NET NativeAOT 库 `libtotk_xlate.a`（`LibExports.cs`）执行进程内翻译：
    * 初始化：`totk_xlate_init_abi(cache_dir, DMSH_CHAIN, debug, "discrete-v1")`（第 312 行）；
    * 翻译请求：`totk_xlate_request(...)`（第 780 行）；
    * SPIRV-Cross 核心：`upstream/games/totk/tools/totk_xlate/spirv_cross_inproc.cpp`（第 45-70 行），在参数中指定 `args.msl_argument_buffers = false`，生成无参数缓冲区的直接绑定 MSL 2.4 代码。
* **运行时加载链路**：
  * 源码位置：`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`（第 8530-8580 行 `shader_load_mode`）；
  * 从预编译库 `.metallib` 或源码现编加载 `id<MTLLibrary>`；
  * 读取对应 `.json` 反射元数据，调用 `nvn_refl_from_json_mode`（`nvn_refl.c:48`）解析；
  * 检查 `o->rf.binding_model == NVN_BIND_MODEL_DISCRETE_V1`（第 8554 行），非离散着色器直接 `MDIE("Apple5 refuses shader %s: reflection binding model is not discrete-v1")`。

### 4.2 采样器去重与 >16 采样器处理策略核查
* **是否已做“同槽采样器去重（<=16）”**：
  * **结论：未做去重**。
  * 源码证据：
    1. `upstream/games/totk/tools/totk_xlate/Service.cs` 第 411 行：
       `if (slots.TryGetValue(slotKey, out string prior)) throw new RuntimeError($"discrete-v1 duplicate {cls} slot {slot}: {prior} and {nm}");`
       对 entry point 上的 sampler 声明执行简单去重校验，若两个声明映射至同一 Metal slot，直接抛出异常崩溃。
    2. `upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m` 第 8510 行：
       `if (duplicate) MDIE("shader %s stage %d has duplicate active discrete slot class %u slot %u");`
* **对 >16 采样器着色器的当前处理策略**：
  * **结论：完全硬拒绝并致命退出（Fail-fast）**。
  * 源码证据：
    1. 翻译器层：`Service.cs` 第 408-409 行：
       `long limit = cls switch { "buffer" => 31, "texture" => 96, "sampler" => 16, ... };`
       `if (slot >= limit) throw new RuntimeError($"discrete-v1 {cls} slot {slot} for {nm} exceeds A12Z Family5 Tier1 limit {limit}");`
    2. 加载器层：`nvn_mtl.m` 第 8508 行：
       `else if (b->metal_class == NVN_METAL_CLASS_SAMPLER && b->metal_slot < 16) { ... } else MDIE("shader %s has invalid discrete class/slot %u/%u");`
    3. 运行时绑定层：`nvn_mtl.m` 第 13532 行：
       `if (binding->metal_slot >= 16) MDIE("invalid discrete sampler binding: stage %d kind %u class %u slot %u sampler %p");`

---

## 5. Game Draw / Dispatch 拦截点与 Metal 3/4 残留检查

### 5.1 拦截点定位与代码
1. **Game Draw 拦截点**：
   * **文件路径**：`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`
   * **行号**：第 19692 行
   * **拦截代码**：
     ```c
     static void draw_inner(const p_draw *dw, const p_draw_idx *di) {
         if (g_present_apple5) MDIE("Apple5 NVN draw replay requires the discrete-v1 shader binder; refusing raw Tier2 argument bytes");
     ```
2. **Compute Dispatch 拦截点**：
   * **文件路径**：`upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`
   * **行号**：第 20643 行
   * **拦截代码**：
     ```c
     static void dispatch(const p_dispatch *dp) {
         ...
         if (g_present_apple5) MDIE("Apple5 NVN compute replay requires the discrete-v1 shader binder");
     ```

### 5.2 Metal 3/4 残留与未隔离项检查
* **已完成隔离的组件**：
  * **设备与队列初始化 (`init_mtl`)**：
    * `g_present_apple5` 时跳过创建 `MTLResidencySet`、`MTL4CommandQueue`、`MTL4CommandAllocator`、`MTL4ArgumentTable`（`g_vt`, `g_ft`, `g_ct` 为 nil）；
    * 采用 classic `g_q = [g_dev newCommandQueue]` 与 `g_classic_fence = [g_dev newFence]`（第 1165-1195 行）。
  * **Render Encoder 创建 (`enc_r_begin`)**：
    * `g_present_apple5` 时调用 `[g_cb renderCommandEncoderWithDescriptor:(MTLRenderPassDescriptor *)d]`，跳过 `[e setArgumentTable:g_vt]` 与 `[e setArgumentTable:g_ft]`（第 2208-2216 行）。
  * **画面呈现 (`present_classic_submit`)**：
    * 走 `present_encode_classic`（第 22580-22630 行），使用纯 classic Metal `setFragmentTexture` / `setFragmentBytes`，不经过 `present_encode_m4`。
* **放开绘制拦截前的关键残留与未适配隐患（风险警示）**：
  1. **Compute Dispatch 链路完全未离散化（致命）**：
     * 若解除绘制拦截后，游戏在初载或帧渲染中派发了计算任务（Compute Dispatch），将在第 20643 行触发 `MDIE`；
     * 且其内部创建函数 `enc_c_begin`（第 2306 行）与 `disp_enc`（第 20609 行）仍包含无条件调用 `[g_blit setArgumentTable:g_ct]` 及 Metal 4 阶段屏障代码。
  2. **SSBO / Driver 常量 / 图像缓冲直绑缺失（黑屏/画面错乱风险）**：
     * `encode_args`（第 14880-15096 行）仅对 `NVN_RB_CBUF`、`NVN_RB_C1`、`NVN_RB_TEX`、`NVN_RB_SAMPT` 实现了直绑并 `continue`；
     * 若遇到带 `sbuf`（存储缓冲）或 `drvcb`（驱动常量段）的着色器，代码将跳过实际 Metal 设置直接 `return 1`，着色器将读到未初始化缓冲。
  3. **特殊图元转换未移植（崩溃风险）**：
     * `draw_inner` 中针对 `QUAD_STRIP`、`QUADS`、`POLYGON` 以及 `8-bit index`（第 20351, 20372, 20385, 20387, 20425 行）在 Apple5 下均显式硬编码为 `MDIE`，未接入 classic 索引展宽转换。

---

## 6. 审计结论与 Step B2 准入建议

1. **工作区状态确认**：
   * 当前工作区在 `nvn_mtl.m` 中已实现了 `classic_draw_*` 与 `classic_bind_vertex_address`，并通过了 `test_discrete_native_draw.py` 单测。
2. **放开绘制拦截（解除 line 19692 `MDIE`）的先决条件**：
   * 仅解除第 19692 行的 `MDIE` 无法安全运行完整游戏，必须确保进入绘制流程的着色器仅依赖 `CBUF`、`C1`、`TEX`、`SAMPT` 与标准三角形/线/点图元；
   * 必须保持 Compute Dispatch 拦截（line 20643）或在拦截点安全跳过（例如打桩或记录警告），避免首帧直接崩溃。
