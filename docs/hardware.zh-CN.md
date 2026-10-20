# HDMI、CEC 与遥控器语音

[English](hardware.md)

这些组件把已验证的 freeguy 原厂接口接入本地 Google TV 环境，并非通用替代驱动。
内核、面板初始化、电视中间件、音频 HAL 和蓝牙解码器仍依赖用户自行取得的原厂文件。

## HDMI 与 CEC

`hdmi/` 通过 Android TV Input Framework 和原厂 TV HAL 注册三个实体输入口。
进入时申请视频输出并切换原厂信号源模式，最后一个会话关闭时恢复应用模式。

高刷新率修复会等待输入时序稳定，再通过原厂 `UPDATE_ONLY` 操作重新应用当前背光值。
不替换面板参数表、不设置新的电气参数，也不改变保存的亮度。
这解决了实际观察到的 144 Hz 黑屏。

只有当前可见、已选中的 CEC 输入会转发方向键和媒体键。
返回键交给外接设备；主页、电源、音量和语音键留给电视。
设备名直接读取 CEC；读不到时显示 `HDMI 1`、`HDMI 2` 或 `HDMI 3`。
没有第四个实体接口，也没有另加信号源页面；`hdmi-label/` 只调整原生菜单的名称显示。

使用自己的 Android 构建工具和签名密钥构建：

```sh
python3 tools/build-hdmi-apps.py \
  --android-jar /path/to/android.jar \
  --aapt2 /path/to/aapt2 \
  --d8-jar /path/to/r8.jar \
  --apksigner-jar /path/to/apksigner.jar \
  --java-home /path/to/jdk-17 \
  --keystore /private/signing.p12 \
  --keystore-password-file /private/signing-password.txt \
  --output /private/new-hdmi-build
```

构建器运行按键归属、释放和时序切换测试，验证 APK 签名，并确认编译占位类没有混入 DEX。
仅安装 APK 不会自动补齐特权权限、原厂服务和运行时配置。

## 遥控器与语音

`voice/` 只重连已经配对的小米遥控器，不新建或删除配对。
两套系统还需要一致的本机配对信息；这部分部署流程尚未整理进公开源码。
不能使用另一台电视的配对数据。

适配程序接收实体语音键报告，调用本地原厂解码器，再通过原厂音频 HAL 的 socket
提供 PCM。内存缓冲保留助手启动前的开口语音，并保留助手进程，避免每次请求
都长时间显示“Gemini is starting”。

说话时一直按住语音键。本机遥控器松键后停止发送音频，不支持松手后再说话。
适配程序记录时序和信号统计，不保存录音；使用 Google 模式时，官方助手会按账号
和服务设置处理语音。唤醒地址取自现有配对，仅注册到易失状态。

```sh
python3 tools/build-voice.py \
  --oem-apk /private/inputs/decoder.apk \
  --oem-sha256 YOUR_VERIFIED_APK_SHA256 \
  --oem-jni /private/inputs/libmibluetooth_jni.so \
  --jni-sha256 YOUR_VERIFIED_JNI_SHA256 \
  --android-jar /path/to/android.jar \
  --d8-jar /path/to/r8.jar \
  --java-home /path/to/jdk-17 \
  --output /private/new-voice-build
```

构建器读取本地 APK 中所需的十二项声明并核对 ABI，生成的编译占位类不会进入 DEX。
输出包含用户提供的原厂 APK／JNI，整个构建目录必须保持私有。
编译通过不能代替另一只遥控器上的收音、识别和重连验证。

## 内置麦克风与组件配置

`runtime/voc-primary-compat.c` 把 Android 内置麦克风的设备和通道标识转换为
原厂四通道 VOC 输入，声音输出和其他输入仍交给原厂 HAL。
枚举或打开输入不启动收音，实际读取才启动；适配层检查静音、句柄归属和清理，
不接管其他服务持有的 VOC 句柄。

```sh
python3 tools/build-audio-adapter.py \
  --clang /path/to/clang \
  --ld-lld /path/to/ld.lld \
  --output /private/new-microphone-build
```

Clang 需支持 ARM Android 目标和主机端 AddressSanitizer／UBSan。
构建器执行十六个合成场景及路由字符串测试。
适配层依赖对应原厂 HAL 的准确布局和原厂 VAD 启动，
不能只因 CPU 或导出符号名相同就加载到其他固件。

`tools/render-runtime-policy.py` 固定本地 HDMI APK 和音频适配层的哈希，
生成 Google 侧组件配置：禁用不兼容的海外声音／画质 Provider、重复 HDMI 服务
及相关服务，启用原生名称覆盖，并回读确认。启动包装器只在配置成功后报告就绪。

```sh
python3 tools/render-runtime-policy.py \
  --hdmi-apk /private/new-hdmi-build/OriginalHdmi.apk \
  --label-apk /private/new-hdmi-build/NativeHdmiLabel.apk \
  --audio-adapter /private/new-microphone-build/builtin-mic-primary.so \
  --output /private/runtime-policy.sh
```

生成脚本不会安装或执行它。这些组件需要纳入完整运行环境，
不能把文件复制到正在运行的电视就视为完成安装。

## 已验证与限制

本机用户已确认：

- HDMI 1、2 画面和声音正常；HDMI 3 支持 3840×2160、60 Hz 和 144 Hz。
- HDMI 3 返回桌面、再次进入，以及 60↔144 Hz 切换正常。
- PS5 画面、声音、CEC 方向键与返回键正常；拔插后原生菜单显示 `PlayStation 5`。
- 两套系统沿用同一次配对；开机和唤醒后的按住说话识别正常。

内置麦克风的短时检查已取得音频采样，包括普通应用单声道和双声道请求；
检查只统计音量，没有保存录音。之后遥控器语音和声音播放仍正常。

部分唤醒需要按第二次电源键，用户接受了这一表现，但原因尚未确认。
松键后说话不可用，多数画质选项尚未完成。
USB 存储、蓝牙音频和 ARC/eARC 未做功能验证。
以上结果不代表已获得 Netflix 认证或实现受保护的 4K 播放。

独立重建的运行环境已经加载这些组件启动。公开构建器的检查与实体功能验收分别记录；
后续修改需要对受影响的路径重新验证。
