# Step B13 · A12Z 离线着色器与管线预热

## 启动基线

目标为 iPad8,9，UDID `<YOUR_IPAD_UDID>`，App 为 `org.macnativegames.botw.a12z`。推送前从同一 App 容器读回的 `Documents/GameData/romfs.sha256` 内容为 `826b373b80cea5c581749a09dfbd9bb21e8d7a9cd3ed161c4d5315b9ae429dad`。这是设备端标记文件的内容。

B4 构建预留别名区的下限为 4 GiB，B5 将下限改为 1 GiB。B12 的普通启动日志显示成功分配了 1 GiB。B13 在推送前复现了 B12 的测试入口，令 `TOTK_START_SCREEN=0`、`TOTK_DEBUG_LOG=1`，沿用设备原有画面设置。日志记录了 1 GiB 别名区、Apple5 的离散 ABI、1,671 行设备管线清单。现有存档命中 965 条渲染管线和 8 条计算管线。首帧耗时 4.647 秒，第 300 帧的 footprint 为 1,121 MiB。[复现日志](/tmp/botw-ios-step-b13/preflight-device.log)

`nvn_mtl.m` 的 `mtl_init` 在 Apple5 路径调用 `totk_rt_set_binding_abi("discrete-v1")`。运行时在首次打开缓存时固定 ABI 并选择带 `.discrete-v1` 后缀的目录。推送前设备顶层有 4,237 个普通文件和一个管线存档子目录，其中 706 个为着色器反射文件。设备普通文件的名称和大小与本地备份逐一相符。

## 离线产物

种子成功生成 15,252 对反射和 Metal 源码，另有 6 对来自 Mac 的原始着色器转译。最终种子目录已经包含这 6 对。生成记录还列有 150 个失败变体，其中 138 个达到采样器槽位上限，10 个发生 IndexOutOfRangeException，2 个发生 ArgumentOutOfRangeException。这些项不参与本次离线部署。
