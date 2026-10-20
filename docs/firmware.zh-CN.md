# 从官方获取固件

[English](firmware.md)

这里使用两份确定版本的 A/B OTA 包，**都不是 USB 强刷包**。
不要改名为 `MiTVUpgrade_*.bin`，也不要把海外包直接刷入实体槽位。
本方案从海外小米固件提取 Google TV 用户层，再在本地组装，
并不存在适用于这块国行主板的通用 Google TV 安装包。

| 输入 | 版本 | 官方分发地址 |
| --- | --- | --- |
| 原厂小米澎湃OS | `freeguy`、Android 11、`OS2.0.5.0.RFTMATV` | [小米 OTA CDN](https://ota-mitv.cdn.pandora.xiaomi.com/OS2.0.5.0.RFTMATV/freeguy-ota_full-OS2.0.5.0.RFTMATV-user-11.0-6fc20cb6f5.zip) |
| 海外 Google TV 供体 | `ladybird`、Android 14、构建 `490` | [Google OTA 分发](https://android.googleapis.com/packages/ota-api/package/904596d74ece1960947b81849914c8e79d0dadc1.zip) |

2026-10-20 重新检查时，两处地址均返回正确的 ZIP 文件头和总大小。
本地完整包的 SHA-256、大小和构建标识记录在
[`firmware-inputs.json`](../tools/firmware-inputs.json)。
Google 地址可能重定向至 `gvt1.com`，临时重定向链接不适合作为固定下载地址。

```sh
mkdir -p /private/tv-inputs
python3 tools/firmware-inputs.py download hyperos \
  --file /private/tv-inputs/hyperos.zip
python3 tools/firmware-inputs.py download google-tv-donor \
  --file /private/tv-inputs/google-tv-donor.zip
```

已有文件时，把 `download` 改为 `verify`。工具检查完整哈希、大小、机型、
构建号和 OTA 类型，不负责建立签名证书信任，也不连接或刷写电视。

链接失效时，通过对应设备的官方更新器或小米获取同一版本。
已研究的国行更新器使用
`https://whippet.bsp.xiaomi.com/mi/ota/updater/v1/checkUpdate`；
该客户端接口不是稳定公开的兼容性承诺。
不要伪造设备身份、借用别人的凭据、公开客户端密钥，或拿相近型号代替。
新版本必须重新适配，修改脚本里的预期哈希不算适配完成。

在本地用单独获取并检查过的 A/B payload 提取工具处理 `payload.bin`，
按 payload 元数据检查分区哈希；后续构建器还会核对固定的输入镜像哈希。
文件完整、证书可信、目标兼容和实体运行正常是不同的检查。
[Google A/B OTA 文档](https://source.android.com/docs/core/ota/ab)说明更新机制，
不代表本项目与任意 OTA 兼容。

校准、配对、设备证书等数据只能来自拥有者自己的电视。
OTA 包和其他电视的备份不能替代它们。下载包、提取镜像及构建结果留在本地，
仓库不授予这些第三方内容的再分发权。
