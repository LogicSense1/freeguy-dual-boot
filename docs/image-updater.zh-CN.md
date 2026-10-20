# 更新四个系统文件

[English](image-updater.md)

更新器只替换已有安装中未运行的四个文件：

- `/data/local/tmp/codex-gtv-fileboot-v1/system.img`
- `/data/local/tmp/codex-gtv-fileboot-v1/vendor.img`
- `/data/local/tmp/codex-gtv-fileboot-v1/system_ext.img`
- `/data/local/tmp/rd-root/envdaily`

它不写物理分区、不初始化 Google 数据、不选择默认系统，也不重启。
它不是首次安装或 OTA 工具。保留旧文件、匹配的切换应用和已验证的
[串口恢复通路](recovery.zh-CN.md)。

## 构建

提供两份私有[切换配置](switch-app.md)，分别记录已安装及候选版本的完整依赖。
型号、Bootloader 和归一化原厂启动参数必须相同；
必须恰好只有上述四个文件的哈希变化，其他依赖变化会被拒绝。

```sh
python3 tools/build-image-updater.py \
  --old-profile /private/installed-profile.json \
  --new-profile /private/candidate-profile.json \
  --java-home /path/to/jdk-17 \
  --android-jar /path/to/android.jar \
  --d8-jar /path/to/r8.jar \
  --output /private/image-update-01
```

构建在仓库之外的新目录生成 `dex/classes.dex`、固定配置及 `plan.json`。
计划固定来源、目标、备份路径和新旧 SHA-256。
候选文件目录为 `/data/local/tmp/codex-gtv-independent-candidate-01/`，
其中加载器名为 `loader`。旧文件保留为 `<active>.previous-<plan-id>`。
构建不会上传或运行设备命令。

`--probe` 生成独立测试计划，只操作相同三个目录中的 `.update-probe-*` 文件；
新旧输入是 4096 字节的一次性合成数据，不能替代真实固件兼容性检查。
中断实验应使用这些测试文件，不要拿活动系统镜像做实验。

## 设备运行条件

Java 执行程序要求：

- 原厂 `freeguy` Android 11 B，Bootloader 为 `01.01.250225`，
  启动完成且 verified boot 为 green。
- 以已配置的系统服务 UID 1000 运行。
- 默认系统为澎湃OS，偏好状态为 `IDLE`。
- 没有挂载或挂接的 loop 镜像，执行程序所在命名空间的 `/data` 为 f2fs。
- 本次持有切换锁，防止同时切换系统。
- 通过原厂只读服务检查两份物理环境文件的 CRC 和原厂参数可逆性。

程序不会自行获取权限。共享状态目录必须预先配置为 UID/GID 1000、0700、
`system_data_file` 上下文；状态和锁文件必须是 UID/GID 1000、0600、
仅有一个硬链接的普通文件。三个镜像目录要求 UID/GID 1000、0750 及同样的上下文。

镜像要求普通文件、一个硬链接，UID 为 0、1000 或 2000，GID 为 1000，
模式为 0640 或 0750，上下文相同，尺寸在 4096 字节至 2 GiB 之间。
这些是当前实现的目录约定，不是让你递归修改 `/data` 的所有权或标签。
不要为了满足检查而修改七个个人数据镜像。

带校验的主机侧权限配置及执行封装尚未公开齐全。
普通 ADB shell 或 root 运行不能代替要求的 UID 和服务上下文；不要绕过拒绝条件。

## 事务与结果

入口类为 `local.codex.tvswitchprobe.ImageUpdateRunner`，
支持 `inspect`、`apply`、`rollback`。每行根据实际文件哈希识别状态：

| 状态 | 活动路径 | 候选路径 | 备份路径 |
| --- | --- | --- | --- |
| ORIGINAL | 旧文件 | 新文件 | 不存在 |
| SAVED | 不存在 | 新文件 | 旧文件 |
| INSTALLED | 新文件 | 不存在 | 旧文件 |

未知组合会停止。每次 rename 前复查设备及文件身份，之后 fsync 两个父目录。
`apply` 可以续做能识别的部分应用状态，`rollback` 逆序恢复原文件。
它不删除候选文件，也不覆盖已有备份。

最终检查会重新计算文件内容哈希，不只依赖本次缓存。
程序将错误写日志，不保证以非零退出码结束，**退出码为零不能证明成功**。
必须关联本次计划、启动标识及新运行记录，要求本轮的 `VERIFIED`、`COMPLETE`，
拒绝 `STOPPED`。观察超时不代表执行结束，不能直接再次启动更新器。

可选的第二个参数为 1 至 8，表示在对应次持久化 rename 后停止，仅用于受控中断测试。
主动停止不算更新成功；尝试启动 Google 之前，必须检查并处理剩余文件状态。

切换应用同样固定镜像哈希。保留匹配的新旧构建和已审查的试运行／回滚顺序；
替换四个文件不会自动更新应用中的固定值。启动和实体硬件还需另外验收。

## 证据及边界

本机通过了测试文件在第 1、4 次 rename 后的中断和回滚，
以及原厂澎湃OS同一次启动内的真实四文件应用／回滚往返；旧文件保留。
这些是受控进程中断，不是断电测试。

从公开源码独立构建得到的计划与实机测试相同。12 个类的反汇编与已测试版本一致，
唯一差别是提示文字由 Xiaomi 改为 HyperOS；新 DEX 尚未部署。

```sh
python3 -B tests/test-image-updater.py
python3 tools/test-core.py --java-home /path/to/jdk-17
```

镜像事务包含 56 个用例，覆盖每次 rename 前后中断、续做、回滚及未知文件拒绝。
这些测试不能证明任意断电下的原子性，也不能替代另一台电视的实机验证。
