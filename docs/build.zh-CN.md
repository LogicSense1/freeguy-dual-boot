# 构建路线

[English](../BUILD.md)

构建只在本地生成新文件，不安装到电视。
固件、个人配置、密钥、生成文件和备份均应保存在 Git 之外。
先阅读[设备条件](equipment.zh-CN.md)、[事故复盘](incident.zh-CN.md)和[依赖](dependencies.zh-CN.md)。

1. 从[官方来源](firmware.zh-CN.md)取得严格匹配的 OTA 输入。
2. [构建镜像](images.zh-CN.md)：system、system_ext、vendor、策略及板级／音频覆盖文件。
3. [构建硬件适配](hardware.zh-CN.md)：HDMI、CEC、语音和麦克风组件。
4. [构建内部启动](runtime.zh-CN.md)：辅助程序、启动环境、运行环境和加载器。
5. [构建切换应用](switch-app.zh-CN.md)：两侧分别构建，界面跟随中英文语言。
6. [构建 ADB 辅助程序](adb-helper.zh-CN.md)：用于澎湃OS调试连接自动恢复。
7. [数据与备份](data.zh-CN.md)：[提取本机输入](owner-capture.zh-CN.md)、首次空数据构建，或对已有安装进行冷备份和本地恢复准备。
8. [镜像更新器](image-updater.zh-CN.md)：为已有安装构建固定的四文件替换／回滚事务。
9. [首次安装](first-install.zh-CN.md)：临时安装环境、固定文件清单、首次上传与[激活](activation.zh-CN.md)。

VNDK 打包先于 framework 镜像，策略编译先于 vendor 镜像。
最终镜像与组件哈希写入运行配置，加载器及镜像哈希再写入切换配置。
必须使用本地输出的哈希，不能照搬另一份报告。

[首次安装指南](first-install.zh-CN.md)已连接部署、激活、切换权限、应用安装和日常文件提交。
[本机采集](owner-capture.zh-CN.md)、[配对安装](remote-pairing.zh-CN.md)和
[ADB 辅助程序配置](adb-helper.zh-CN.md)提供独立工具与记录要求。
整套全新安装流程仍待实机验收，组件测试和现有电视的验收不能替代这一项。
独立串口撤销工具的写入路径已在本机验收；复刻者仍须在自己的电视上验证，
范围见[恢复说明](recovery.zh-CN.md)。不要用猜测的启动命令跳过失败的前置检查。

本机的普通重启、双向切换、默认系统记忆和数据标记验证已通过。
当前重建版本的断电冷启动仍未验证；日常看护没有一小时回滚上限，
启动与心跳保护仍保留。
