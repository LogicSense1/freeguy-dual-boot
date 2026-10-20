# 首次启动入口

[English](activation.md)

`activate-first-entry.py` 核对备份、成功的串口撤销记录、部署记录和拒绝探针，
再设置一次临时启动入口。它支持[本机数据采集](owner-capture.zh-CN.md)和
[Google 初次设置](first-install.zh-CN.md)两种加载器，保留原厂 B，不刷镜像、不重启。

协调程序已通过离线测试，包括命令行和模拟串口下的实际写入事务；尚未用它完成
一台全新安装电视的首次启动。不要用它覆盖已有安装。

## 准备记录

文件全部保存在仓库外。以下记录必须来自同一台电视，按此顺序实际取得：

1. 独立校验并保存原始分区前缀备份、原厂启动参数。重要用户数据另行备份。
2. 用 [`serial-recovery.py restore`](recovery.zh-CN.md) 在该电视上验证准确的
   串口撤销，保留完整输出目录；随后确认正常进入 HyperOS B。
3. 部署所选加载器和文件，完成非 PID 1 拒绝探针。在同一次正常原厂启动中记录
   原厂参数、实际参数和完整内核命令行；观察与探针之间不要重启。
4. 整理以下清单并离线检查，再用已验证的串口连接停在受支持的 B 控制台。

本工具不负责建立最初的恢复通道或创建备份。文件哈希不能证明串口实际接在哪台
电视上，也不能证明采集顺序。不要编造成功记录、复制其他电视的记录或修改失败
日志。尚未实际验证串口撤销时，不得进行激活。

所有文件引用格式一致：

```json
{"path": "/private/example.bin", "bytes": 123, "sha256": "实际完整SHA256"}
```

路径可以是绝对路径，也可以相对所在清单。以下占位示例不能直接执行。
设备绑定值及其方案必须使用独立记录的原值。

**备份清单**列出 eMMC 用户区从字节零到 userdata 起点的连续分块：

```json
{
  "version": 1,
  "deviceBindingSha256": "本机绑定SHA256",
  "deviceBindingScheme": "freeguy-emmc-cid-serial-v1",
  "prefixBytes": 4750049280,
  "chunks": [
    {"offset": 0, "file": {"path": "prefix-000.img", "bytes": 67108864, "sha256": "该块SHA256"}}
  ]
}
```

必须列出全部块；上例不完整。每块按扇区对齐且不超过 128 MiB，
不得有空洞、重叠或重复文件。激活前重新计算所有文件哈希。
它不包含 userdata、eMMC 硬件启动区或 RPMB，不是全芯片备份。

**串口撤销清单**包含 `version`、相同的两个设备绑定字段，以及 `files`：
后者将以下每个名称映射到一个文件引用。

```text
result.json
handshake.bin
00.bin ... 09.bin
```

这些必须是公开的 `serial-recovery.py restore` 命令的原始输出。协调程序核对
B 身份、临时参数、准确逆变换、保存确认和恢复后的参数回读。
只做 inspect，或参数本来就是原厂值的空操作，不算撤销验证。

**预检清单**引用完整记录：

```json
{
  "version": 1,
  "deviceBindingSha256": "本机绑定SHA256",
  "deviceBindingScheme": "freeguy-emmc-cid-serial-v1",
  "staging": {"path": "stage/journal.json", "bytes": 1, "sha256": "部署记录SHA256"},
  "verification": {"path": "probe/journal.json", "bytes": 1, "sha256": "探针记录SHA256"},
  "bootObservation": {"path": "boot.json", "bytes": 1, "sha256": "启动观察SHA256"},
  "backup": {"path": "backup.json", "bytes": 1, "sha256": "备份清单SHA256"},
  "serialUndo": {"path": "undo.json", "bytes": 1, "sha256": "撤销清单SHA256"}
}
```

`bootObservation` 包含 `stockBootargs`、`observedBootargs`、`kernelCmdline`、
`deviceBindingSha256`、`deviceBindingScheme` 和 `bootId`。
启动 ID 必须与完成的探针一致，保存参数和运行参数都应是正常原厂启动。
完整参数只在本地保存。

## 检查与激活

Google 初次设置使用：

```sh
python3 tools/activate-first-entry.py inspect \
  --preflight /private/preflight.json \
  --preflight-sha256 VERIFIED_PREFLIGHT_SHA256 \
  --recovery-profile /private/recovery-profile.json \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_BINDING \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --inputs /private/first-install-inputs.json \
  --docker /path/to/docker \
  --filesystem-container sha256:REVIEWED_LOCAL_IMAGE_ID \
  --output /private/preflight-inspect-01
```

e2fsprogs 容器使用已审查的本地镜像完整 ID。每个 ext4 输入均以只读方式挂载，
执行 `e2fsck -fn`；容器无网络、无 capabilities，也不拉取镜像。
任何非零结果都会停止激活。查看本地日志，不要跳过错误或直接修复原始输入。

若要采集本机数据，将 `--inputs`、`--docker`、`--filesystem-container`
替换为构建时的 `--stock-boot`、`--capture-build`、`--loader-build`。
该加载器不依赖新构建的 ext4 镜像。

检查完成后，将 `inspect` 换成 `arm`，使用新输出目录，并添加
`--port /dev/YOUR_VERIFIED_SERIAL_PORT`。工具会重新检查全部记录，取得新的
B 控制台提示符，并再次核对原厂参数和 B 状态。随后只执行一轮已有事务：
设置临时参数、回读、保存、再回读。不发送重启，也不修改槽位、重试计数或固件。

成功后保留输出目录中的 `preflight.json`、`plan/journal.json` 和
`arm/journal.json`。后两者交给
[`boot-first-entry.py`](first-install.zh-CN.md#观察首次正常启动)。
不要用 inspect 产生的计划替代 arm 当次生成的计划。

失败时保留全部记录，检查未完成操作。丢失响应不代表没有写入；不要盲目重试或
重启，必要时使用已验证的串口撤销。保存确认和 RAM 回读仍不能代替物理存储
回读及随后启动的验证。

```sh
python3 -B tests/test-activate-first-entry.py
python3 -B tests/test-first-activation-console.py
python3 -B tests/test-boot-first-entry.py
```
