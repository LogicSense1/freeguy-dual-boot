# Freeguy Dual Boot

[English](README.md)

在小米 L75MA-SPL（freeguy）上切换原厂小米澎湃OS和本地组装的 Google TV。

**当前状态：** 已在一台电视上通过系统切换、默认系统记忆和普通重启测试。
Google 经原厂系统的启动阶段进入，两套系统保留各自数据。
独立重建的完整系统已启动并返回澎湃OS；日常运行没有一小时回滚上限。
此构建的默认系统已通过双向普通重启验证，断电冷启动仍未验证。
目前没有安装发行版。

切换应用跟随系统语言显示中文或英文。Google 侧使用圆形／自适应图标，
澎湃OS侧保留原图标。

启动器在进入 Google 前撤销临时启动参数。运行健康检查失败时回到澎湃OS，
并暂停自动进入 Google，直到用户再次主动选择。
这条回退路径已通过一次实际停止心跳的测试。

## 使用前

先阅读[设备与恢复条件](docs/equipment.zh-CN.md)。
首次安装需要兼容的 HDMI 转 UART USB 调试线、空间充足的电脑、电视之外的已校验备份、
网线、遥控器，以及稳定且可操作的电源。没有经过验证的串口恢复通路，请只做离线准备。
安装完成后的日常使用不需要连接调试线或电脑。

**没有经过验证的 HDMI 转 TTL 恢复连接，不要修改启动配置。**
早期启动故障可能需要付费维修或更换主板。
即使由 agent 执行，ADB 和另一个槽位也不能替代恢复通路。

保留原厂 B 系统、已校验的备份和可用的串口恢复连接。
早期实验曾导致循环启动，最终通过串口恢复。
型号相同不代表固件兼容。

当前 Google 环境使用 permissive SELinux 和特权调试；
备用启动环境的 USB 调试不要求授权。不保证 OTA 或 DRM 兼容。

## 源码预览

已公开切换应用、默认启动策略、启动配置与镜像文件事务、内部启动运行时、HDMI／CEC、
遥控器语音、麦克风适配及离线测试。镜像构建、辅助应用和部署工具已附
[构建路线](docs/build.zh-CN.md)。完整首次安装流程尚未经过实机验收，
目前不是经过验证的安装发行版。

- `app/`：中英文切换界面、图标和启动状态逻辑。
- `updater/src/`：[镜像文件的校验、替换与还原](docs/image-updater.zh-CN.md)。
- `tools/build-switch-app.py`：APK 构建工具，见[构建说明](docs/switch-app.md)。
- `tools/test-core.py`、`tests/`：只使用合成数据的离线测试。
- `hdmi/`、`hdmi-label/`、`voice/`：[硬件适配与构建命令](docs/hardware.zh-CN.md)。
- `runtime/`：[内部启动、回退与组件组装](docs/runtime.zh-CN.md)。
- [首次安装](docs/first-install.zh-CN.md)：安装环境构建、部署、[校验后激活](docs/activation.zh-CN.md)和日常运行配置。
- 镜像构建：[从官方输入提取并本地组装](docs/images.zh-CN.md)。
- `adb-helper/`：[澎湃OS调试连接恢复](docs/adb-helper.zh-CN.md)。
- [共用遥控器](docs/remote-pairing.zh-CN.md)：离线准备与带校验的上机安装。
- [数据与备份](docs/data.zh-CN.md)：冷备份、本地恢复准备和首次空数据构建。
- [恢复](docs/recovery.zh-CN.md)：串口撤销范围、物理启动配置回读及验证边界。

```sh
python3 tools/test-core.py --java-home /path/to/jdk-17
python3 -B tests/test-profile.py
```

部署前请阅读[事故复盘](docs/incident.zh-CN.md)。
所需版本及获取方法见[官方固件来源](docs/firmware.zh-CN.md)。
仓库不包含固件、Google 应用、设备备份、配对数据或密钥。
生成的镜像、加载器和个人配置应放在仓库之外。

## 协议

原创工具代码采用 [Apache-2.0](LICENSE)；第三方组件遵循各自协议。
本项目与小米、Google 无隶属关系。

按现状提供，不作担保。使用可能造成数据丢失、循环启动或设备永久损坏。
在适用法律允许的范围内，贡献者不承担相应损失责任；
详见[免责声明](docs/equipment.zh-CN.md#免责声明)与协议。
