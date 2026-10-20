# 两套系统共用遥控器

[English](remote-pairing.md)

本机使用同一次澎湃OS配对。先在澎湃OS完成配对，再将这台电视的蓝牙身份和该遥控器记录
同步到独立的 Google 数据中。两边分别重新配对，可能替换遥控器保存的密钥，
导致另一边失效。

`tools/prepare-remote-bond.py` 公开了这个流程的离线合并部分。
它要求两份配置的适配器地址一致、原系统恰好有一个受支持的小米语音遥控器，
且本地密钥、加密记录和 HID 描述完整；Google 存在其他设备配对时会拒绝。
Google 的其他非设备设置保留。

```sh
python3 tools/prepare-remote-bond.py \
  --stock-config /private/pairing/hyperos-bt_config.conf \
  --google-config /private/pairing/google-bt_config.conf \
  --google-backup /private/pairing/google-bt_config.bak \
  --output /private/new-pairing-preparation

python3 tests/test-remote-bond.py
```

输入必须来自使用者自己的同一台电视。地址相等只是数据一致性检查，不是硬件身份认证。
输出含配对密钥，必须保密。工具不修改输入、不连接电视，
保留 Google 两份原配置，并对两份准备文件回读、计算哈希。

## 上机流程

1. 在澎湃OS确认配对正常，保存原配置。
2. 进入 Google，停止语音与重连辅助程序，关闭蓝牙并确认蓝牙进程退出，再读取最终配置。
3. 用原厂配置准备 Google 的 `bt_config.conf` 和 `bt_config.bak`。
4. 核对设备及启动实例未变、源配置未变，只安装 Google 这两份文件，
   保留所有者、权限和 SELinux 标签，同步并回读。
5. 重启蓝牙和辅助程序，检查配对记录、实体方向键和按住说话；
   切回澎湃OS，不重新配对，检查方向键。

全程保留独立的网络／串口连接，停止蓝牙会断开遥控器。
失败时先再次关闭蓝牙，恢复 Google 的原配置，再启用蓝牙。
离线合并不保证写入中断或断电后的恢复。

本机此前已通过两边方向键和 Google 语音验证。
不要照搬历史反向同步实验或另一台电视的配置。

## 首次安装工具

`tools/install-first-remote.py` 在[限时安装环境](first-install.zh-CN.md)中执行文件安装。
它拒绝日常系统、正在运行的语音／重连程序、其他设备、即将超时的安装会话，
以及上次操作留下的文件。全程保留网络 ADB 和串口，停止蓝牙时遥控器会断开。

按[完整命令](remote-pairing.md#first-install-device-tool)先运行 `inspect`，
核对后用新日志目录执行 `install`：

1. 关闭蓝牙，等待 OFF，并停止已确认身份的蓝牙进程。
2. 从原厂数据挂载读取澎湃OS 配对，只读不写；将 Google 原件和准备文件保存在本机私有目录。
3. 排他上传两份新配置，保留所有者、权限和 SELinux 标签，全部校验后才替换。
4. 原文件分别保留为 `bt_config.conf.freeguy-before-v1` 和
   `bt_config.bak.freeguy-before-v1`，然后替换目标并同步。
5. 重新启用蓝牙，检查导入的遥控器是否出现在配对列表。

不改固件、启动参数、默认系统或澎湃OS 配对。列表里有遥控器并不代表按键和语音
已经通过；进入日常环境后仍需实际检查。

失败后保留私有日志目录，不自动重试、删除或在半套配置上重新启用蓝牙。
同一次安装启动中，可使用 `restore`、相同构建和设备参数、新输出目录，
另加 `--install-record` 和 `--install-record-sha256` 指定失败安装日志。

撤销会核对本机备份、原文件 inode 和当前配置。配对已变化、启动实例不同或状态不明时
拒绝覆盖。它只处理未完成的事务，不用于撤销成功后又发生蓝牙活动的安装。
临时文件和被撤下的新文件保留；撤销自身若中断，必须先检查对应日志，不能盲目续跑。

安装及撤销通过了合成写入中断、日志失败测试；参考电视的只读检查确认会拒绝已安装的
日常环境。**新工具尚未用于全新电视。** 测试不模拟物理断电，也不证明实体按键响应。

```sh
python3 -B tests/test-install-first-remote.py
```
