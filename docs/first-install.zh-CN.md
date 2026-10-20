# 首次安装

[English](first-install.md)

日常运行环境要求共享偏好目录、故障标记和切换服务权限已经存在。
本机当时是在较早的一轮临时 Google 启动中配置这些条件的，
因此全新安装不能直接从日常环境开始。

本页介绍去掉这项依赖的构建步骤。带校验的文件部署、首次启动激活和切换权限配置
仍在整合，当前还不是完整安装流程。

## 输入

- 核对主板、原厂 B 启动链及准确的[固件输入](firmware.zh-CN.md)。
- 建立并验证[串口恢复](recovery.zh-CN.md)，在电视之外保留独立校验过的配置和固件备份。
- 按[构建步骤](build.zh-CN.md)生成系统文件、硬件适配和完整日常 ramdisk，
  保留精确哈希及本地签名密钥。
- 首次空数据应按[数据说明](data.zh-CN.md)，使用这台电视自己的输入构建。
  不得用新初始化文件覆盖已有安装的数据。

临时安装环境发现 `/data/local/codex-system-default-v1` 已存在时会拒绝启动，
包括目标不存在的符号链接。它不能代替已安装的日常环境。

## 安装环境

输入完整组装、包含当前硬件配置的日常 ramdisk，所有输出放在仓库之外的新目录：

```sh
python3 tools/build-first-install-root.py \
  --daily-ramdisk /private/daily-root/ramdisk.cpio.gz \
  --daily-sha256 VERIFIED_DAILY_RAMDISK_SHA256 \
  --output /private/first-install-root-01
```

工具只改变两套根目录中的 `codex/entry.sh` 和 `codex/runtime-policy-start.sh`。
修改前核对脚本与当前日常源码完全一致，保留权限及所有者，并拒绝重复修改过的输入。

临时环境：

- 使用先前的限时看护：启动上限 600 秒，健康心跳期限 120 秒，安装会话上限 3,600 秒。
- 不要求、不创建、不挂接或修改共享偏好与故障文件。
- 保留镜像校验、固件设备只读保护、机主 ADB 公钥和当前硬件配置步骤。
- 不启动日常遥控器语音。进入日常版本后再启用、验收；安装环境不代表最终功能验收。

日常运行环境保持不变，仍然**没有一小时运行上限**。
临时看护的期限和心跳均使用 uptime，与早期临时版本一致；
日常环境保留独立的单调时钟策略。

## Loader

使用 `first-install-root-01/manifest.json` 中的输出哈希：

```sh
python3 tools/build-loader.py \
  --ramdisk /private/first-install-root-01/ramdisk.cpio.gz \
  --ramdisk-sha256 VERIFIED_FIRST_INSTALL_RAMDISK_SHA256 \
  --clang /path/to/clang --ld-lld /path/to/ld.lld \
  --output /private/first-install-loader-01

python3 tools/audit-loader.py --build /private/first-install-loader-01
```

Loader 沿用 `envdaily` 入口名称和精确撤销逻辑，
**不表示可以覆盖已有安装的 `envdaily` 文件**。
临时与日常 loader 应分别保存在不同本地目录；它们含原厂文件和私有构建输入，不应发布。

Loader 在交接前写入并校验两份启动环境文件。
这一步完成前失败，仍可能需要串口撤销。使用 RAM 文件系统不等于启动过程只读。

## 后续设备步骤

接收方必须在自己的电视上逐项完成：

1. 部署准确的系统、数据文件、外部语音依赖、loader 和 scratch 目录，不覆盖已有安装。
2. 修改启动状态前，核对上传哈希、剩余空间、权限及 loader 对非 PID 1 运行的拒绝。
3. 从已检查的 B 控制台激活精确的一次性入口，观察启动，
   再独立确认两份环境文件已经恢复为澎湃OS参数。
4. 在临时 Google 中配置切换服务权限和共享状态，安装两侧切换应用，保留本机遥控器配对。
5. 通过已审查的文件事务换成单独构建的日常 loader，保留旧版本及匹配的应用配置。
6. 验证双向切换、默认启动、数据保留和串口恢复。实际断电开机需单独验收。

仓库尚未提供第 1–5 步完整、带校验的命令流程。
不要用旧 AVB 实验、海外固件直刷、手工修改环境文件或猜测的服务调用填补缺口。

## 证据

早期临时入口曾在本机进入 Google。新的安装环境沿用其限时看护方式，
并结合当前硬件配置，已成功构建；Loader 通过 64 项 ARM 系统调用模拟检查，
核对了 1,005 个归档条目。**这个组装版本尚未在电视上启动。**

```sh
python3 -B tests/test-first-install-root.py
python3 -B tests/test-runtime-policy-start.py
```
