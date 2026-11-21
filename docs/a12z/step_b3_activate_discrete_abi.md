# BOTW A12Z: Step B3 激活运行时 discrete-v1 ABI 与缓存隔离报告

**执行时间**：2026-11-21  
**仓库根目录**：`~/Documents/NS/ipad pro/BotW_A12Z`（分支 `master`，未执行 `git commit`，未启动 Mac 游戏）  
**修改文件**：
* [nvn_mtl.m](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m)
* [main.m](../../upstream/games/totk/ios/Sources/main.m)

---

## 1. 代码改动摘要（含准确行号）

### 1.1 在 Apple5 初始化阶段激活 `discrete-v1` 运行时 ABI
* 文件：[nvn_mtl.m](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L1156-L1164)
* 改动内容（`init_mtl()` `#if TARGET_OS_IPHONE` 分支内）：
  ```objc
  if (g_present_apple5) {
      const char *async = getenv("TOTK_ASYNC_REPLAY");
      if (async && atoi(async) > 0) MDIE("Apple5 native execution requires synchronous replay for pool mapping and submission ownership; TOTK_ASYNC_REPLAY is unsupported");
      int abi_res = totk_rt_set_binding_abi("discrete-v1");
      if (abi_res != 0)
          MDIE("Apple5 failed to select discrete-v1 shader binding ABI (code %d)", abi_res);
      MLOG("Apple5 without Metal4: native g_q execution and discrete-v1 shader binding ABI enabled");
  }
  ```
* **作用**：在首次调用 `totk_rt_dir()` 或 `totk_xlate_init_abi()` 前将运行时 ABI 锁定为 `discrete-v1`，使 `totk_rt_dir()` 自动落盘到 `rt-<DMSH_CHAIN>.discrete-v1` 目录，并使 `libtotk_xlate.a` 以 `discrete-v1` 模式输出无 Argument Buffer 的直绑 MSL 2.4 着色器。

### 1.2 隔离离线 `g_msl_dir` 回退目录
* 文件：[nvn_mtl.m](../../upstream/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m#L7688)
* 改动内容：
  ```objc
  g_msl_dir = [NSString stringWithFormat:(g_present_apple5 ? @"%@/b-%s.discrete-v1" : @"%@/b-%s"), m ? @(m) : exe_rel("shader-cache"), DMSH_CHAIN];
  ```
* **作用**：防止 Apple5 运行时在离线查找 `.metallib` / `.json` 时误加载到非离散（`ab`）产物。

### 1.3 保护 iOS `.discrete-v1` 着色器缓存目录不被后台清理误删
* 文件：[main.m](../../upstream/games/totk/ios/Sources/main.m#L783-L793)
* 改动内容：
  ```objc
  NSString *discreteChainDir = [chainDir stringByAppendingString:@".discrete-v1"];
  if (!g_probe_copy_alias) {
  dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY,0),^{
      NSFileManager *files = [NSFileManager new]; NSUInteger removed = 0;
      for (NSString *name in [files contentsOfDirectoryAtPath:cacheRoot error:nil] ?: @[]) {
          if ([name isEqualToString:chainDir] || [name isEqualToString:discreteChainDir]) continue;
          if ([files removeItemAtPath:[cacheRoot stringByAppendingPathComponent:name] error:nil]) removed++;
      }
      if (removed) fprintf(stderr,"[shader-cache] removed %lu entries of earlier translation chains\n",(unsigned long)removed);
  });
  }
  ```
* **作用**：保留 `Documents/shader-cache/rt-<DMSH_CHAIN>.discrete-v1`，避免重启时被旧链清理协程删除。

---

## 2. 本地单元测试验证原始输出

执行命令：
```bash
TOTK_DISCRETE_MSL_DIR=/tmp/totk-abi-e2e/ios-metal-iphoneos-20261120 python3 -m unittest \
  upstream.tests.test_rt_cache_carry \
  upstream.tests.test_shader_seed \
  upstream.tests.test_binding_abi \
  upstream.tests.test_discrete_native_draw \
  upstream.tests.test_discrete_buffer_binding \
  upstream.tests.test_discrete_texture_sampler_binding \
  upstream.tests.test_native_main_encoders -v
```
原始输出尾部摘要（全部 48 项通过）：
```text
test_legacy_and_explicit_abi_and_invalid_argument_ids (upstream.tests.test_binding_abi.BindingABI.test_legacy_and_explicit_abi_and_invalid_argument_ids) ... ok
test_discrete_compute_ssbo_drvcb_tex_sampler_binding (upstream.tests.test_discrete_native_draw.DiscreteNativeDraw.test_discrete_compute_ssbo_drvcb_tex_sampler_binding) ... ok
test_native_draw_semantics_with_title_vertex_shader (upstream.tests.test_discrete_native_draw.DiscreteNativeDraw.test_native_draw_semantics_with_title_vertex_shader) ... ok
test_production_binder_with_title_vertex_shader (upstream.tests.test_discrete_buffer_binding.DiscreteBufferBinding.test_production_binder_with_title_vertex_shader) ... ok
test_title_texture_sampler_bindings_and_sampling (upstream.tests.test_discrete_texture_sampler_binding.DiscreteTextureSamplerBinding.test_title_texture_sampler_bindings_and_sampling) ... ok
test_production_native_clear_upload_and_alias_fences (upstream.tests.test_native_main_encoders.NativeMainEncoders.test_production_native_clear_upload_and_alias_fences) ... ok

----------------------------------------------------------------------
Ran 48 tests in 10.166s

OK
```

---

## 3. iOS Release-iphoneos 编译与符号检查

### 3.1 构建与编译日志
将更新后的 `nvn_mtl.m` 与 `ios/Sources/main.m` 同步至 `/tmp/botw-ios-step-b2/build-tree`，执行：
```bash
cd /tmp/botw-ios-step-b2/build-tree && xcodebuild -project ios/TOTKIOS.xcodeproj -scheme TOTKIOS -configuration Release -destination generic/platform=iOS -derivedDataPath build/ios-derived-b2 CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=
```
编译输出核验（`/tmp/botw-ios-step-b2/xcodebuild-b3.log`）：
```text
CompileC /tmp/botw-ios-step-b2/build-tree/build/ios-derived-b2/Build/Intermediates.noindex/TOTKIOS.build/Release-iphoneos/TOTKIOS.build/Objects-normal/arm64/main.o /tmp/botw-ios-step-b2/build-tree/ios/Sources/main.m normal arm64 objective-c com.apple.compilers.llvm.clang.1_0.compiler (in target 'TOTKIOS' from project 'TOTKIOS')
CompileC /tmp/botw-ios-step-b2/build-tree/build/ios-derived-b2/Build/Intermediates.noindex/TOTKIOS.build/Release-iphoneos/TOTKIOS.build/Objects-normal/arm64/nvn_mtl.o /tmp/botw-ios-step-b2/build-tree/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m normal arm64 objective-c com.apple.compilers.llvm.clang.1_0.compiler (in target 'TOTKIOS' from project 'TOTKIOS')
...
** BUILD SUCCEEDED **
```
* **产物 SHA-256**：
  * `TOTK.app/TOTK`：`59218861861cbec8a0b510b7d25a5c14f495f513be4cceb578c34920b2e18b83`
  * `Objects-normal/arm64/nvn_mtl.o`：`62d3a59c7245746c317fd26f634c2744ced7cd75f8d519f6bc7dbb1c0a7a3b47`

### 3.2 `udot` 指令与 ABI 符号确认
```text
$ otool -tV .../nvn_mtl.o | grep -i udot | wc -l
       0

$ nm -u .../nvn_mtl.o | grep totk_rt_set_binding_abi
_totk_rt_set_binding_abi
```
* `udot` 指令数：**`0`**；`_totk_rt_set_binding_abi` 已正式链接至 `nvn_mtl.o`。

---

## 4. 风险提示与 Step B4 准备状态
* 当前 `TOTK.app` 编译、ABI 激活（`discrete-v1`）、缓存防误删、绘制/计算原生路径与 `udot` 检查均已就绪。
* 下一步（**Step B4**）可将 `/tmp/botw-ios-step-b2/build-tree/build/ios-derived-b2/Build/Products/Release-iphoneos/TOTK.app` 签名并增量覆盖安装到实体 iPad8,9（`org.macnativegames.botw.a12z`，保留现有 `Documents/GameData` 与存档），脱离 `probe-gpu-copy-alias` 运行真实游戏启动并收集控制台日志。
