# 首次安装

[English](first-install.md)

日常运行环境要求共享偏好目录、故障标记和切换服务权限已经存在。
本机当时是在较早的一轮临时 Google 启动中配置这些条件的，
因此全新安装不能直接从日常环境开始。

本页介绍去掉这项依赖的构建步骤及带校验的文件部署。
首次启动激活和切换权限配置仍在整合，当前还不是完整安装流程。

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

## 本地文件清单

把[输入模板](../examples/first-install-inputs.example.json)复制到仓库之外，
填写路径、准确字节数和独立保留的 SHA-256。模板未填完时会拒绝执行。
其中列出全部 18 个镜像、两个外部语音依赖、安装 ramdisk 和 loader。
Loader 的 `manifest.json`、`audit.json` 应保留在它旁边。

```sh
python3 tools/plan-first-install.py \
  --inputs /private/first-install-inputs.json \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --output /private/first-install-plan-01
```

仅当先前明确记录的绑定方式是 `freeguy-android-serial-v1` 时才使用该选项值，
工具不会自动降级。工具对本地文件计算哈希，并核对安装环境中的源码脚本、挂载顺序、只读镜像清单和
外部语音哈希，再检查 loader 清单及 64 项检查记录是否匹配该 ramdisk 和当前源码。
输出的私有 `plan.json` 列出 21 个固定目标文件，全部禁止覆盖已有目标。
压缩 ramdisk 只是本地构建输入，不单独上传。
清单同时列出空 scratch 目录和待生成的 stage 标记。
若阶段文件、loader、共享状态或代理目录已存在，首次部署必须停止。

这一步不上传文件、不访问电视，不能证明数据来源属于本机，也不检查文件系统健康；
这两项证据须另外保留。检查记录代表离线执行结果，不是签名或上机证明。
清单不代表可以安装，后续部署工具仍须在写入前重新核对输入、当前电视、空间和全部目标。
不要用首次空数据替换已有安装。

## 部署新文件

先回到受支持的原厂澎湃OS B 系统，保持正常运行。此步骤使用普通 ADB shell（UID 2000），
要求身份与独立记录的设备绑定一致，并且没有已挂接的 loop 镜像。
阶段目录、loader 目录、共享状态或切换代理目录只要已存在就拒绝，包括之前未完成的上传。
它不用于修改已有 Google 安装。

先检查，使用新的私有日志目录：

```sh
python3 tools/stage-first-install.py inspect \
  --adb /path/to/adb --device TV_ADB_SERIAL_OR_ENDPOINT \
  --inputs /private/first-install-inputs.json \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --output /private/staging-inspect-01
```

核对结果后，把 `inspect` 换成 `stage`，并换一个新的输出目录。
命令会重新核对全部本地输入和设备状态。
要求剩余空间能容纳完整文件集，并额外保留 8 GiB；传输过程中持续核对剩余量。
只新建普通文件，使用独占创建，不能覆盖已有目标。

每个文件上传后回读哈希，同步文件和父目录，最后再复查完整文件集。
Loader 只有在上传哈希匹配后才增加执行权限，但**不会执行**。
Stage 标记在文件上传完后写入。不写启动参数、固件分区或已有数据文件，
也不会切换系统或重启。

出错后保留私有 `journal.json`，不要立即处理残留文件。
其中的 `pending` 标记未完成操作；上传期间能取得时会记录本机客户端和远端 shell 的 PID。
超时或终止本机客户端，不代表远端进程已经停止。
考虑清理前，必须核对当前启动与进程状态，并确认哪些文件属于这一轮。
工具不会自动重试、续传、删除文件或撤销部分上传。
仅有 stage 标记不能证明成功，必须检查日志中的 `complete: true`。

部署流程已通过合成故障注入及本机 shell 二进制传输测试。
电视只读检查验证了元数据解析，以及对当前 Google 安装的拒绝。
**尚未在全新原厂安装上完成整套实际上传。**
`fsync` 成功也不能代替物理断电测试。

## 复查已部署文件及 loader 保护

私下保留完成的部署日志，并独立记录其 SHA-256。
验证工具重新计算本地清单、核对日志，再检查同一机主设备上的全部 21 个文件及标记。
它要求当前部署工具记录的父目录身份，拒绝不完整或被改动的日志。
不要编辑失败记录把它标为完成，也不要向已有文件重新执行首次部署。

```sh
python3 tools/verify-first-install.py inspect \
  --adb /path/to/adb --device TV_ADB_SERIAL_OR_ENDPOINT \
  --inputs /private/first-install-inputs.json \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --staging-record /private/staging-01/journal.json \
  --staging-record-sha256 VERIFIED_STAGING_JOURNAL_SHA256 \
  --output /private/staged-verification-01
```

把 `inspect` 换成 `probe`，使用新输出目录，便会以普通 UID 2000 shell 执行一次已核对的 loader。
其 ARM 检查必须证明非 PID 1 路径只执行 `getpid` 和 `exit(90)`，没有环境写入。
远端脚本在执行前再次核对当前启动身份及 loader 哈希；
只接受退出码 90 且没有异常输出，之后再检查 loader 哈希。
这不代表验证了它作为 PID 1 的真实启动路径。

如果部署后曾正常重启回原厂系统，必须完整复查文件和目录，验证记录会绑定本次启动。
验证过程中重启、结果不符或超时，都会留下未完成记录，不自动重试。
保留已经记录的进程信息，先查明不确定的执行状态，再考虑重试。

此命令不改启动参数，也不授权激活启动。
文件系统健康、启动参数兼容、已验证的串口撤销以及真正的首次启动，仍须分别检查。
当前已测试合成验证流程和本机 shell 探测协议，
通用探测命令尚未在新部署的电视安装上实际执行。

## 准备首次启动参数

`tools/plan-first-activation.py` 将部署日志、已完成的退出码 90 探测和私有启动记录关联，
计算临时启动参数，并要求公开串口恢复代码能逐字恢复原参数。

启动记录 JSON 包含 `stockBootargs`、`observedBootargs`、`kernelCmdline`、
`deviceBindingSha256` 和 `deviceBindingScheme`。完整原值只保存在本地，
核对机主自己的原厂配置并记录来源；文件哈希不能证明采集来源。
完整内核命令行必须恰好包含每个被观察的参数一次，允许 mboot 调整顺序；
新增参数不得覆盖 root 或 init 配置。

```sh
python3 tools/plan-first-activation.py \
  --inputs /private/first-install-inputs.json \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --staging-record /private/staging-01/journal.json \
  --staging-record-sha256 VERIFIED_STAGING_JOURNAL_SHA256 \
  --verification-record /private/staged-probe-01/journal.json \
  --verification-record-sha256 VERIFIED_PROBE_JOURNAL_SHA256 \
  --boot-observation /private/boot-observation.json \
  --boot-observation-sha256 VERIFIED_OBSERVATION_SHA256 \
  --recovery-profile /private/serial-recovery-profile.json \
  --output /private/first-activation-plan-01
```

按实际新增参数计算长度，同时至少为 Bootloader 追加参数预留 626 字节，
两种计算都必须在 2,047 字节上限内另留 64 字节余量；转义后的串口命令也必须能容纳。
探测未完成、清单变化或原厂配置不匹配时拒绝生成。

这一步不连接电视，也不授权写入。仍需核对当前串口状态、独立备份、实际验证过的串口撤销、
文件系统健康状况和首次启动结果。本机当前日常系统的命令行已通过转换及长度核对，
不能据此认为新的安装环境已通过上机验证。

## 检查已截停的串口

原厂 B Bootloader 已经停在控制台后，检查准备好的入口：

```sh
python3 tools/first-activation-console.py inspect \
  --port /dev/YOUR_VERIFIED_SERIAL_PORT \
  --activation-plan /private/first-activation-plan-01/journal.json \
  --activation-plan-sha256 VERIFIED_ACTIVATION_PLAN_SHA256 \
  --recovery-profile /private/serial-recovery-profile.json \
  --output /private/first-console-inspection-01
```

命令要求新收到的 `freeguy#` 提示符、匹配的 B Bootloader、未改变的槽位和重试状态、
原厂初始化标记及逐字一致的原厂参数。它只保存电脑端的私有日志，不会截停运行中的电视，
也不授权启动配置写入。

模块内包含供后续安装程序调用的写入事务：每条命令发送前保存意图，
临时参数在 RAM 中回读匹配后才执行 `saveenv`，保存前后都核对 B 状态。
不发送重启、启动、AVB、槽位或重试计数写入命令。
响应或日志写入失败即停止，不重试，也不自动撤销。
`saveenv` 确认和 RAM 回读不等于已验证持久存储或实际启动。

独立命令目前只开放检查。安装协调程序仍需接入备份、同机串口撤销、
文件系统健康和最新文件验证，才能调用写入组件。
合成测试覆盖每条命令与日志写入的中断边界，本地伪终端测试覆盖实际串口传输代码；
新事务尚未用于电视的首次安装激活。

## 后续设备步骤

接收方必须在自己的电视上逐项完成：

1. 部署准确的系统、数据文件、外部语音依赖、loader 和 scratch 目录，不覆盖已有安装。
2. 修改启动状态前，核对上传哈希、剩余空间、权限及 loader 对非 PID 1 运行的拒绝。
3. 从已检查的 B 控制台激活精确的一次性入口，观察启动，
   再独立确认两份环境文件已经恢复为澎湃OS参数。
4. 在临时 Google 中配置切换服务权限和共享状态，安装两侧切换应用，保留本机遥控器配对。
5. 通过已审查的文件事务换成单独构建的日常 loader，保留旧版本及匹配的应用配置。
6. 验证双向切换、默认启动、数据保留和串口恢复。实际断电开机需单独验收。

部署及验证工具覆盖第 1–2 步，尚待首次安装的实机验证；第 3–5 步的完整、带校验命令仍未提供。
不要用旧 AVB 实验、海外固件直刷、手工修改环境文件或猜测的服务调用填补缺口。

## 证据

早期临时入口曾在本机进入 Google。新的安装环境沿用其限时看护方式，
并结合当前硬件配置，已成功构建；Loader 通过 64 项 ARM 系统调用模拟检查，
核对了 1,005 个归档条目。**这个组装版本尚未在电视上启动。**

```sh
python3 -B tests/test-first-install-root.py
python3 -B tests/test-first-install-plan.py
python3 -B tests/test-first-install-stage.py
python3 -B tests/test-first-install-verify.py
python3 -B tests/test-first-activation-plan.py
python3 -B tests/test-first-activation-console.py
python3 -B tests/test-runtime-policy-start.py
```
