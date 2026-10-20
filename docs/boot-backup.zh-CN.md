# 启动链备份

[English](boot-backup.md)

`backup-boot-prefix.py` 从已截停的原厂 B 控制台读取 eMMC 用户区前缀，
向 FAT U 盘导出 71 个文件，共 4,750,049,280 字节。
不写电视内部存储、不改启动参数、不重启。

范围包括 userdata 之前的分区表、原厂固件和设备配置，
不含 userdata、eMMC 硬件启动区或 RPMB。文件含本机私有材料，不能公开。
Bootloader 原始读取和 Android 逻辑分区读取可能不同，不能仅因跨路径哈希不同
就判断损坏。

## 准备

- 建立可收发的 [HDMI 串口连接](equipment.zh-CN.md)，停在受支持的
  2025 年 2 月原厂 B Bootloader 的空命令提示符。
- 独立记录本机绑定值和原厂启动参数配置。工具会比较配置，但不能仅凭串口适配器
  证明实际连接的是哪台电视。
- 准备至少有 5 GiB 空闲的 FAT U 盘，Bootloader 应能以 `usb 0:1` 访问它。
  不要格式化有重要文件的盘。
- 在电脑上给 U 盘新建空目录：`freeguy-backup-` 后接 8–32 个小写字母或数字，
  名称应唯一。电脑另留至少 6 GiB 用于独立副本。
- 从电脑弹出 U 盘后接电视。参考备份使用电视 USB 3.0，串口仍接 HDMI 1。

工具不改分区表、不初始化磁盘、不自动尝试其他 USB 设备。
目录非空、分区布局不匹配、B 状态异常或命令失败都会停止。

## 导出

关闭其他串口读取程序，将占位目录换成实际名称：

```sh
python3 tools/backup-boot-prefix.py capture \
  --port /dev/YOUR_VERIFIED_SERIAL_PORT \
  --usb-directory /freeguy-backup-YOUR_UNIQUE_LOWERCASE_SUFFIX \
  --recovery-profile /private/recovery-profile.json \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_BINDING \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --output /private/prefix-export-01
```

固定计划核对全部 42 个分区的布局，只用已测试的 64 MiB RAM 窗口，止于 userdata。
每块从 eMMC 读取、在 RAM 计算 CRC，再写入 U 盘的固定文件名。
此原厂版本的 `mmc read` 成功后仅回显命令和提示符；出现意外文字即拒绝继续。
每条命令发送前均记录到本地日志。

必须确认 `capture.json` 中 `complete: true`。USB 写入确认不算独立备份校验。
中断后保留部分文件和日志；不覆盖、不自动重试、不续传。
不能据不完整导出修改启动配置。

## 校验独立副本

导出完成、控制台空闲后，把 U 盘接回电脑，电视配置保持不变。
记录原始 `capture.json` 的 SHA-256，再执行：

```sh
python3 tools/backup-boot-prefix.py verify \
  --capture-record /private/prefix-export-01/capture.json \
  --capture-record-sha256 VERIFIED_CAPTURE_RECORD_SHA256 \
  --source /Volumes/YOUR_USB/freeguy-backup-YOUR_UNIQUE_LOWERCASE_SUFFIX \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_BINDING \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --output /private/prefix-copy-01
```

此步骤只在本地核对大小和 RAM CRC，复制到新的私有目录，刷入存储、逐份回读，
并记录 SHA-256，不访问电视。只有完整副本才生成可供
[激活协调程序](activation.zh-CN.md)读取的 `backup.json`。两份都应保留。

通用工具通过了合成导出、中断与副本校验测试；布局及响应解析也已对照本机旧记录，
匹配全部 71 块。新 CLI 尚未重新上机导出。
有备份不等于有恢复通路，激活前仍须完成
[串口撤销验证](recovery.zh-CN.md#首次激活前验证撤销)。
