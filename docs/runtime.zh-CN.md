# 内部启动

[English](runtime.md)

这里说明本机使用的实现，不是另一台电视的安装步骤。
修改启动状态前，先阅读[恢复条件](equipment.zh-CN.md)和[事故复盘](incident.zh-CN.md)。

## 启动路径

原厂 B 启动链完成硬件初始化。早期加载器重建内存文件系统，
在交给启动分发程序之前，从两份启动配置中撤销临时参数。

分发程序读取默认系统和故障状态。澎湃OS走原厂路径；
Google 使用内部存储中的本地组装镜像和独立数据区。
切换系统不会重新初始化数据文件。

加载器**会写入启动配置**，不是只读的内存测试。
分区布局、偏移和预期参数均针对本机。保留原厂 B 启动镜像；
已有故障的旧 A 槽位不是退路。

| 源码 | 用途 |
| --- | --- |
| `runtime/loader.c`、`internal-env-*.h`、`internal-env-transform.c` | 重建早期文件系统，检查、恢复并回读两份启动配置。 |
| `runtime/fileboot-entry.sh`、`mount-overlays.sh` | 挂载镜像文件，准备 Google 环境并启动其 init。 |
| `runtime/fileboot-helper.c`、`watchdog-policy.h` | 启动分发、命名空间操作、启动和运行健康检查。 |
| `runtime/runtime-policy.sh.in`、`runtime-policy-start.sh` | 启动硬件适配与策略服务。 |

日常看护程序允许启动耗时 600 秒、运行心跳中断 120 秒，
**没有一小时运行上限**。独立的限时启动保护在日常看护接管后停止。
检测到运行故障时记录故障并返回澎湃OS，暂停自动进入 Google，
直到用户再次主动选择。

这些检查不能处理所有早期故障。内核卡死、启动配置写入失败或 Bootloader
不可用时，仍可能需要串口介入或维修。备用启动环境还会开启不要求授权的
USB 调试，应控制设备物理访问并使用可信局域网。

## 构建顺序

所有生成文件、个人配置和固件输入均放在仓库之外。
每次使用新的输出目录，并保留上一版正常工作的文件。

1. 用 `tools/build-helper.py` 构建辅助程序。
2. `tools/build-bootstrap-root.py` 从严格匹配的原厂 B boot 提取 ramdisk，
   校验后修改本地 init；输入镜像哈希不同会被拒绝。
3. 构建[硬件适配组件](hardware.zh-CN.md)、本机配置和运行策略。
4. `tools/assemble-runtime-root.py` 组合启动环境、适配组件、覆盖文件、
   个人配置和镜像哈希。
5. `tools/build-loader.py` 将组装后的 ramdisk 嵌入 ARM 加载器。

`tools/assemble-runtime-components.py` 可替换已有运行环境中的组件，
完整组装工具也会调用它。

[镜像构建工具](images.zh-CN.md)已公开。设备专用运行配置准备、首次特权部署和配对数据准备，
尚未形成完整公开流程。缺少步骤时，不要使用历史试验或猜测的启动命令替代。
[首次安装构建](first-install.zh-CN.md)可以生成不要求共享状态已存在的临时安装环境。

辅助程序和最终加载器可以使用 Clang 与支持 ARM 的 LLVM 链接器构建：

```sh
python3 tools/build-helper.py \
  --clang /path/to/clang --ld-lld /path/to/ld.lld \
  --output /private/build/helper

python3 tools/build-loader.py \
  --ramdisk /private/build/runtime/ramdisk.cpio.gz \
  --ramdisk-sha256 YOUR_VERIFIED_SHA256 \
  --clang /path/to/clang --ld-lld /path/to/ld.lld \
  --output /private/build/loader
```

也可用 `--docker-linker` 指定已有且包含 `ld.lld-18` 的本地镜像；
工具不会下载镜像。生成的加载器包含固件和本机配置，不要公开。

## 离线检查

ARM 检查需要 Python 的 `unicorn` 和 `pyelftools`。
它们模拟系统调用、时钟变化与故障，不连接电视。

```sh
python3 tests/test-archive.py
python3 tests/test-bootstrap-root.py
python3 tests/test-component-integration.py
python3 tests/test-runtime-root.py
python3 tools/audit-helper.py \
  --helper /private/build/helper/helper \
  --report /private/build/helper/audit.json
python3 tools/audit-loader.py --build /private/build/loader
```

2026-10-20，从公开源码重新构建的辅助程序和加载器与已部署版本逐字节一致。
加载器通过 64 项检查，核对了 1,005 个嵌入条目；辅助程序通过 21 项检查。
这说明源码对应本机使用的构建，不代表另一台电视兼容。
断电冷启动与普通重启应分别记录验证结果。
