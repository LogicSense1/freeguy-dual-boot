# 恢复

[English](../RECOVERY.md)

原厂 B 系统是回退目标。本机旧 A 系统存在已知 verity 错误，不能当作救援系统。
保留 B 启动链和原有分区布局。

公开恢复工具已通过离线测试及本地伪终端测试。其底层串口操作流程曾用于恢复本机，
但独立工具的 `inspect`、`restore` 模式尚未完成实机串口验证。
另外的 `verify-persisted` 模式已实际读取本机物理环境分区，
确认两份文件符合使用者的原厂启动配置；这不代表串口写入或后续启动已通过测试。

## 保留的材料

放在电视以外、Git 之外：

- 独立回读并通过 CRC 校验的原厂启动配置。
- 覆盖原厂 B 启动链和 super 的已校验分区备份。
- Google 七个数据文件的冷备份及设备绑定记录。
- 已接受的加载器、不可变镜像哈希。
- 本地构建配置、签名密钥和串口撤销记录。

本机的前缀分区备份不包含 userdata、eMMC 硬件启动区及 RPMB，不是全芯片备份。
Recovery 清除数据不能修复旧 A 镜像，还会丢失数据，不应作为 Google 启动失败的常规处理。

## Google 启动后异常

健康看护会返回澎湃OS并记录故障，暂停自动进入 Google。
检查启动日志后再决定是否主动重试。

镜像更新事务只允许在原厂 B、Google 镜像未挂接、默认目标为澎湃OS时运行，
保留四个旧不可变文件以便撤销未完成的替换。
它不恢复用户数据，也不写物理分区；完整安装流程仍待补齐。

## Android 启动前异常

使用事先验证过的 HDMI 串口连接，截停 B Bootloader。
本机为 HDMI 1、115200 波特率、8N1。必须使用兼容的电视专用调试线；
普通 HDMI 视频线不能代替，也不能凭接口外形猜测针脚或电平。

关闭其他串口读取程序，停在没有未提交命令的空提示符。
工具握手会发一个换行，收到准确的 `freeguy#` 提示符后才发命令。
使用本人电视的私有切换配置：

```sh
python3 tools/serial-recovery.py inspect \
  --port /dev/cu.YOUR_SERIAL_ADAPTER \
  --profile /private/local-profile.json \
  --output /private/serial-inspect-01
```

检查包括指定 mboot 版本、B 选择状态、重试状态、初始化标记和完整的归一化启动参数。
不符合预期就停止。

```sh
python3 tools/serial-recovery.py restore \
  --port /dev/cu.YOUR_SERIAL_ADAPTER \
  --profile /private/local-profile.json \
  --output /private/serial-restore-01
```

`restore` 只撤销本项目准确匹配的 `envdaily` 启动参数，保留当前启动原因和亮度字段。
它核对原厂配置哈希，回读内存修改，调用 `saveenv`，检查完成提示，再读一次内存环境。
最后一次读仍然不是独立读取持久存储，因此报告中的 `persistentReadbackVerified`、
`subsequentBootVerified` 保持 false。原本已经是原厂参数时不会写入。

输出目录必须是仓库之外的新目录。工具不会重启电视。
检查结果后，才可在通过检查的控制台执行正常的 `reset` 并观察后续启动；
若首次失败，应截停后续循环，不继续盲目重试。
不要改用 `avb verify`、`bootm`、切槽位或修改重试计数。
工具不能修复损坏的固件、不可用的控制台或硬件故障。

## 独立回读持久配置

此项只读检查要求已接受的 Android 14 Google 环境、已授权的 ADB 连接、
匹配的原厂 B boot 镜像，以及只读物理块设备。
它不能在 Recovery 菜单或停住的 Bootloader 中执行，也不是新的恢复通道。

```sh
python3 tools/serial-recovery.py verify-persisted \
  --adb /path/to/adb \
  --device TV_ADB_SERIAL_OR_ENDPOINT \
  --profile /private/local-profile.json \
  --output /private/persisted-check-01
```

工具读取物理 16 MiB 的 `uenv` 分区，不读取 Google 挂载的副本。
`uboot.env` 和 `uboot.env.bak` 都必须通过 CRC，并符合已知原厂启动参数哈希。
它会再次读取整个分区、核对启动标识及 B 镜像哈希；
遇到 loop 设备替代、尺寸不符、可写块设备、残留临时参数或读取期间变化会拒绝。

结果只将 `persistentReadbackVerified` 标为通过；
`subsequentBootVerified`、`coldPowerTested` 仍为 false。
物理回读成功不能证明下次启动成功，重启及实体屏幕还要单独验证。
它也不核对所有其他环境配置，不能替代完整分区备份。

`inspect`、`restore` 在 macOS/Linux 使用 Python 标准库。
`verify-persisted` 还会导入 `dissect.extfs`，其协议及使用边界见
[依赖说明](dependencies.zh-CN.md)。原始串口日志保持私有。

```sh
python3 -B tests/test-serial-recovery.py
```

## 数据恢复

[数据恢复准备](data.zh-CN.md)只校验有绑定的冷备份，并展开为新的本地文件，
尚不负责上机安装。不要将文件式 `uenv.img` 或 `uuenv.img` 写入物理分区。
哈希相同只能证明复制一致，恢复前还必须单独检查文件系统一致性。
