# 数据镜像与备份

[English](data.md)

Google 的七个可写文件位于 `/data/local/tmp/codex-gtv-fileboot-v1/`。
其中包含设备和用户的私有数据，应保存在 Git 之外，不作为发行附件。
即使文件名与分区相同，它们也**不是物理分区的原始备份**。

| 文件 | 内容 |
| --- | --- |
| `uenv.img`、`uuenv.img` | Google 环境使用的文件式副本 |
| `persist.img`、`tvcertificate.img` | 本机设置及身份材料 |
| `userdata.img`、`metadata.img`、`cache.img` | Google 独立数据文件 |

不要把这些文件写入 `/dev/block/by-name/*`，也不要用于 Fastboot flash。
不得替换为另一台电视的设备数据。七个文件的完整首次提取和上机部署流程尚未公开齐全。

## 备份已有安装

先通过正常入口返回澎湃OS。备份工具要求指定版本的原厂 B 系统、Android 11
启动完成，并且没有挂接任何 loop 镜像。Google 正在运行时会拒绝备份；
工具不会替你切换系统。整个备份期间保持在澎湃OS。

```sh
python3 tools/backup-data.py capture \
  --adb /path/to/adb \
  --device TV_ADB_SERIAL_OR_ENDPOINT \
  --destination /private/tv-backup-01
```

此命令只发出只读 ADB 操作。每个文件复制前后都检查启动标识、文件元数据和哈希，
全部传输后再复查先前文件。压缩文件会在本地解压计算哈希，失败时不标记备份完成。

默认将 eMMC CID 与 Android 序列号一起计算为设备绑定哈希。
若原厂系统拒绝读取 CID，先查明原因；可以显式指定
`--binding-source android-serial`，使用较弱且独立标记的序列号绑定方式。
工具不会自动降级。绑定校验不等于加密，也不能证明设备身份未被伪造。

虽然使用 gzip 传输，仍要求剩余空间大于全部展开尺寸加 2 GiB。
目标必须是仓库之外的新目录，通过父目录符号链接指向仓库也会拒绝。
输出使用私有权限，但没有加密。

不连接电视也能检查已有备份：

```sh
python3 tools/backup-data.py verify-local /private/tv-backup-01
```

检查包括七个文件的压缩／展开哈希、尺寸及完成记录，不检查当前电视、
文件系统一致性或应用功能。旧版无绑定清单可以校验复制完整性，
但不能用于下面的恢复准备工具。

## 从备份准备恢复文件

使用核对电视时另外记录的设备绑定值及方式。直接照抄未知备份内的绑定值，
不能作为独立身份检查。将下面的 `KNOWN_DEVICE_BINDING_SHA256`
替换为已知的 64 位十六进制哈希。

```sh
python3 tools/prepare-data-restore.py \
  --backup /private/tv-backup-01 \
  --expected-binding KNOWN_DEVICE_BINDING_SHA256 \
  --expected-binding-scheme freeguy-emmc-cid-serial-v1 \
  --output /private/tv-recovery-01
```

只有原备份使用序列号绑定时才选 `freeguy-android-serial-v1`。
此命令只展开并回读新的本地文件，不连接电视、不覆盖已有镜像、不安装恢复文件。

哈希一致不代表可以直接恢复。本机一份保留备份通过了复制校验，
但其中两个环境副本未通过只读文件系统检查；后来修复的本地副本通过了检查，
并未安装到电视。保留原始备份，另外检查文件系统一致性，上机前准备已验证的撤销路径。
不能把离线展开成功当作设备已恢复。

## 仅为首次安装创建空数据

`create-data-images.py` 创建三个空 ext4 文件：6144 MiB 的 `userdata.img`、
64 MiB 的 `metadata.img`、64 MiB 的 `cache.img`，并将使用者的 ADB 公钥写入
新 userdata，检查所有权、权限和 SELinux 标签。其他四个本机文件不由它生成。

```sh
python3 tools/create-data-images.py \
  --adb-public-key /private/owner-adbkey.pub \
  --adb-public-key-sha256 PUBLIC_KEY_FILE_SHA256 \
  --docker /path/to/docker \
  --container local/e2fsprogs \
  --output /private/fresh-google-data-01
```

准备含 e2fsprogs 的本地容器，以及[依赖说明](dependencies.zh-CN.md)中的 Python
读取库。容器不会自动拉取、不联网，只挂载新输出目录。剩余空间须大于 8.125 GiB。
构建会检查 ext4 特性、运行只读 `e2fsck`，再独立读取元数据及 ADB 公钥。
已有输出目录会被拒绝。

这些是空白初始化镜像，不能用于更新或恢复已有安装；
替换现有数据会丢失 Google 账号和设置。正常切换、更新系统镜像时不应运行它。

## 检查

```sh
python3 -B tests/test-backup-data.py
python3 -B tests/test-prepare-data-restore.py
python3 -B tests/test-create-data-images.py
```

测试覆盖设备身份、镜像占用、源文件变化、损坏／未完成备份、输出隔离和原文件保留。
模拟备份会核对只读设备命令的允许列表；不能替代实际备份、文件系统检查或上机恢复。
