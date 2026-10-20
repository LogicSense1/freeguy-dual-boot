# 提取本机数据

[English](owner-capture.md)

OTA 不包含本机校准和身份数据。此组件在原厂 Android 启动前，读取同一台电视的
`uenv`、`uuenv`、`persist`、`tvcertificate`，供本地组装使用。
文件必须保密，不得公开，也不得写回物理分区。

参考电视已完成采集并进入澎湃OS。传输检查通过：四份文件、两份环境 CRC、
原厂启动参数、设备归属和本次启动标识均匹配。这不能证明其他主板或固件兼容。

## 构建

使用[已核验 OTA](firmware.md) 中原厂 B 的 `boot.img`，按
[英文指南](owner-capture.md#build) 的命令依次构建并检查：

1. `build-owner-capture.py` 与 `audit-owner-capture.py`：原生采集程序。
2. `build-owner-root.py`：保留原厂 init 的临时根文件系统。
3. `build-loader.py --owner-capture-build … --stock-boot …` 与
   `audit-loader.py`：带启动参数自动撤销的加载器。

所有输出放到仓库外的新私有目录。生成的根文件系统和加载器包含原厂文件，
不能发布。原有 Google 加载器的构建结果不受此模式影响。

## 执行与退路

全新安装可用 `stage-owner-capture.py inspect` 检查，再以 `stage` 上传、
`probe` 验证非 PID 1 的退出保护，完整参数见[英文部署说明](owner-capture.md#stage-a-fresh-capture)。
它复用首次安装的排他创建、哈希回读和同步逻辑，只新建加载器和标记。
发现已有 Google 数据、加载器、共享设置、代理或采集目录即拒绝。
通用部署工具通过了离线中断测试和真实构建计划检查，尚未在全新电视上上传；
参考实机测试使用的是保留已有安装的单独流程。

`plan-owner-activation.py` 将原构建、完成的部署与退出保护记录、同一设备同次启动
的原厂参数连接起来，检查完整命令行长度和精确撤销。参数见
[启动计划](owner-capture.md#execution-and-recovery)。输出可供现有串口工具只读检查，
但不会连接设备、授权写入或证明串口退路有效。

**通用启动编排工具尚未发布。上传和非 PID 1 测试不代表可修改启动参数。**
不要覆盖已有 `envdaily`，也不要猜测启动命令。

参考测试沿用已验证的原厂 B 入口和串口撤销：保留现用加载器，先选澎湃OS
为默认系统，验证启动参数在内存中修改并撤销，再启用精确匹配的临时入口。
测试没有切槽、手动调用 AVB 校验或刷写固件，结束后恢复并校验了现用加载器。

外层加载器先恢复两份物理启动环境，再执行采集。若此前失败，仍可能需要
串口撤销。因此整轮包含持久启动配置操作，不能称为只读 ADB 备份。

采集程序仅在重建的原厂 RAM 根目录中作为 PID 1 运行，检查固定分区设备号、
起始位置和尺寸。四个源设备只读打开，各读取两遍；仅把 userdata 挂为可写，
在 `/data/local/tmp/freeguy-owner-capture-v1` 新建普通文件。
已有目录一律拒绝，不删除、不覆盖、不续传。完成记录最后写入，并绑定内核的
本次启动 UUID。交回原厂系统前，将未修改的 init 恢复到其原有 RAM 路径。

电视需要至少 2 GiB 加 84 MiB 空间。输出归普通 ADB shell 所有，使用私有权限。
失败的输出保留为未完成状态。此组件不导出 userdata、遥控器配对、`misc`、
硬件启动区或 RPMB，不能替代完整恢复备份。

## 重启前取回

等待澎湃OS 启动完成，在同一次启动中执行：

```sh
python3 tools/collect-owner-capture.py \
  --adb /path/to/adb --device TV_ADB_SERIAL_OR_ENDPOINT \
  --profile /private/recovery-profile.json \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --output /private/owner-data-01
```

默认使用 CID 与 Android 序列号的组合绑定。若原厂权限限制 CID 读取，
可显式使用事先记录的较弱序列号绑定，并加 `--binding-source android-serial`。
不能把未知下载包自身的身份信息当作独立校验依据。

取回工具只使用普通澎湃OS ADB 读取；校验尺寸、元数据、传输前后哈希、
完成记录 CRC、两份环境 CRC 和设备归属，最后再次检查整套文件。
失败不会标记完成，旧启动留下的完成记录也会拒绝。

本地还需只读文件系统检查。参考采集的 `persist` 和 `tvcertificate` 通过；
两份环境文件经过[新副本整理](data.zh-CN.md)后通过，且可达数据和元数据保持一致。
没有修复或替换物理分区，也没有修改原始采集文件。

取回并恢复现用加载器后，先正常重启一次澎湃OS，再使用切换应用。
采集启动的 `/proc/cmdline` 仍含临时参数，即使已保存的参数恢复正常，
切换应用也会正确拒绝这个过渡状态。

## 释放临时入口

首次采集的目录占用了后续安装需要的路径。`remove-owner-stage.py` 仅允许在完成
取回、正常重启至澎湃OS后，核对原构建、部署日志、本地备份、设备归属和启动参数，
再删除本次部署的加载器与标记，最后移除空目录。目录中有额外文件即拒绝。
采集文件和其他数据保留，不递归删除、不自动重试，也不自动续跑部分清理。

先用 `inspect`，检查后才以新日志目录运行 `remove`，参数见
[清理命令](owner-capture.md#release-the-temporary-entry)。
工具通过离线中断及拒绝测试，尚未在全新安装上进行实机清理。

## 验证范围

原生程序通过 24 项系统调用／失败模拟；真实 ARM CRC 单独测试五组向量，
其余模拟中的长 CRC 循环由 zlib 替代。外层加载器通过 64 项检查。
取回工具测试覆盖中断、文件变化、错误设备和过期完成记录。
这些检查不模拟存储控制器或物理断电。

```sh
python3 -B tests/test-collect-owner-capture.py
python3 -B tests/test-stage-owner-capture.py
python3 -B tests/test-plan-owner-activation.py
python3 -B tests/test-remove-owner-stage.py
```
