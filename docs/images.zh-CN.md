# 本地镜像构建

[English / 完整命令](images.md)

这些工具只处理本地文件，输出供运行时挂载，不是 OTA 包，也不能刷入物理分区。
从[官方固件来源](firmware.zh-CN.md)取得指定版本，在本地提取原始分区镜像并保留原件。
修改哈希常量不能使另一版固件兼容。

## 构建顺序

| 步骤 | 工具 | 输入与结果 |
| --- | --- | --- |
| 1 | `extract-policy-inputs.py` → `build-sepolicy.py` | 从五个指定 OTA 镜像提取 29 个策略文件，生成本地配置并编译兼容策略。 |
| 2 | `build-vendor-image.py` | 在原厂 vendor 的新副本中禁用格式化、恢复刷写和槽位服务，保留原件。 |
| 3 | `extract-vndk30.py` → `build-vndk-apex.py` | 从原厂 system 提取 294 个文件，用使用者自己的密钥打包 VNDK 30 APEX。 |
| 4 | `build-framework-images.py` → `build-system-image.py` | 将本地 APEX 加入海外 system，适配 system_ext，再修改文件挂载启动流程。 |
| 5 | `extract-board-inputs.py` → `render-board-overlays.py` | 提取本机板级配置并生成覆盖文件，面板模块保持不变。 |
| 6 | `extract-audio-libraries.py` | 从原厂和海外镜像提取六个音频依赖库，放到七个覆盖路径。 |

依次使用[英文页中的命令](images.md#working-directories)。脚本名称与参数不随语言变化。
先把路径改为自己的私有目录；`HASH_FROM_...` 和 `VERIFIED_...` 必须换成相应
本地输入或构建报告里的完整 SHA-256，不要直接执行占位命令。
每个工具都要求新的输出目录。

最终三份主要镜像：

- `system/system-fileboot.erofs` → 运行时的 `system.img`。
- `framework/system_ext-vndk30.erofs` → `system_ext.img`。
- `vendor/vendor-fileboot.img` → `vendor.img`。

随后继续构建[硬件适配](hardware.zh-CN.md)和[内部启动环境](runtime.zh-CN.md)。
还需要其他只读镜像与同一台设备的数据；仅这三份文件不能启动完整系统。

## 依赖与边界

工具来源见[依赖说明](dependencies.zh-CN.md)。涉及 Docker 的构建使用已准备好的本地
工具镜像，运行时不拉取镜像、不联网，只挂载新的输出目录。
输入须为提取后的原始文件系统镜像，不能直接使用 Android sparse 容器。

SELinux 构建复现当前 permissive 兼容环境，关闭 neverallow 检查，
不是已经验证的 enforcing 安全策略。

APEX 使用本地 RSA-4096 密钥及匹配的 X.509 证书签名，
不是小米或 Google 签名。整个输出目录必须保密，其中包含生成的 PKCS#8 私钥。
生成的固件、库、覆盖文件和策略均不属于本项目可再分发的源码。

system 构建只在本地复制原厂固件中的迁移脚本，不读取设备数据或提取 DRM 密钥。
校准、配对和个人数据不能从另一台电视复制。

## 验证范围

镜像构建会全量回读文件内容、权限、所有者、扩展属性和符号链接；
不比较时间戳和 inode 编号，也不能证明另一台电视上的实际表现。
文件系统 UUID、其他元数据和本地签名密钥可能使整包哈希改变，
所以运行配置必须使用自己本轮构建的哈希。

2026-10-20，公开工具重建的 system、system_ext、vendor 均通过上述回读。
新策略提取工具复现了全部 29 个输入哈希，编译出的策略也逐字节一致。
本轮新镜像没有部署到电视，保留了正在使用的版本。

[离线测试命令](images.md#evidence-and-limits)只使用合成数据，不连接电视。
