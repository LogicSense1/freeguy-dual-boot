# 依赖与源码边界

[English / 工具上游链接](../DEPENDENCIES.md)

本仓库提供项目源码；固件和构建输出在本地获取或生成，不作为公开附件。

## 工具

- JDK 17、Android SDK 的 AAPT2／D8／apksigner：编译和签名应用。
- Clang／LLD：编译 ARM 辅助程序、加载器和音频适配库。
- erofs-utils：读取、重建和检查 framework 镜像。
- e2fsprogs：创建、修改并检查本地 ext4 镜像。
- secilc：编译兼容策略。
- avbtool、OpenSSL：本地 VNDK APEX 签名和验证。
- dissect.extfs：读取 ext4；unicorn、pyelftools：离线 ARM 检查。

已检查环境使用 erofs-utils 1.9.4、JDK 17、e2fsprogs 1.47.0、
secilc 3.5 和 LLD 18.1.3。新构建应记录工具版本和镜像摘要；
不能据此保证不同工具版本输出完全相同的二进制。

指定 Docker 工具镜像的构建会使用 `--pull=never`、`--network=none` 和只读容器根目录，
只挂载新的输出目录。需要自行准备含对应工具的本地镜像。

## 协议

Apache-2.0 适用于本项目原创代码，不会改变 Python 依赖、外部工具、固件、
Google 服务、厂商库或商标的条款。
Python 构建工具导入的 **dissect.extfs 使用 AGPL-3.0**。
组合分发或提供服务时，应满足相应依赖条款；
不能因为依赖单独安装，就把整个环境称为只受 Apache 约束。

HDMI 编译桩仅描述接口，不进入 APK。
语音构建从本地 OEM APK 读取十二个声明，不复制方法体，生成声明也不进入 DEX。
音频 ABI 偏移和固件变换来自对指定版本的分析，不宣称 clean-room 实现或全面法律免责。
EROFS 读取器描述 Linux v5.4 磁盘格式，未附带内核头文件。

## 必须保留在本地的内容

OTA、提取的镜像、OEM APK／库／模块、生成的策略和覆盖文件、ramdisk、加载器、
签名材料、个人配置、校准与设备标识、配对记录、应用数据、设备备份和原始日志。
ADB 公钥的备注也可能暴露使用者身份。

VNDK 使用使用者本地密钥签名，不赋予签署小米固件的能力。
bootstrap 工具只接受特定原厂 B boot，在本地 init 副本上执行两处已检查修改。
面板模块保持原样。未将这些输出上传 Git，不代表获得在其他地方再分发的权利。
