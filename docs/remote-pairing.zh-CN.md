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
公开准备工具已通过合成数据测试，但新 CLI 尚未执行实际上机安装，
还需要带校验的设备端安装工具。不要照搬历史反向同步实验或另一台电视的配置。
