# Dependencies and source boundaries

[简体中文](docs/dependencies.zh-CN.md)

This repository contains project code. Firmware and build outputs are obtained
or generated locally and are not release attachments.

## Tools

| Tool | Source | Use |
| --- | --- | --- |
| JDK 17 | A JDK distribution supplied by its publisher | Java compilation and Android signing tools |
| Android SDK, AAPT2, D8, apksigner | [Android command-line tools](https://developer.android.com/tools) | App resources, DEX and signatures |
| Clang and LLD | [LLVM releases](https://releases.llvm.org/) | ARM helper, loader and audio adapter |
| erofs-utils | [EROFS project](https://erofs.docs.kernel.org/en/latest/mkfs.html) | Read, rebuild and check framework images |
| e2fsprogs | [Upstream](https://github.com/tytso/e2fsprogs) | Local ext4 image creation and readback |
| secilc | [SELinux project](https://github.com/SELinuxProject/selinux) | Compile local compatibility policy |
| avbtool | [AOSP AVB](https://android.googlesource.com/platform/external/avb/) | Sign and verify the locally built VNDK APEX |
| OpenSSL | Its publisher or OS distribution | Owner-generated local signing key and certificate |
| dissect.extfs | [Upstream](https://github.com/fox-it/dissect.extfs) | Read ext4 image content and metadata |
| unicorn, pyelftools | Their upstream Python packages | Offline ARM execution audits |

The checked host used erofs-utils 1.9.4, JDK 17, e2fsprogs
1.47.0-2.4~exp1ubuntu4.1, secilc 3.5-1 and lld-18 18.1.3.
Record actual tool versions and image digests for a new build; this list is not
a promise that another tool version produces the same binary hash.

Builders accepting a Docker image expect it to exist locally. They run with
`--pull=never`, `--network=none` and a read-only container root, mounting only the
new output directory. Supply an image with the named tools; no container image
or package mirror is provided here.

## Licensing

Apache-2.0 covers the project's original code. It does not relicense imported
Python dependencies, external executables, firmware, Google services, vendor
libraries or trademarks. In particular, the Python builders import
**dissect.extfs, licensed AGPL-3.0**. Combined distributions or services need to
satisfy the applicable dependency terms; do not describe the entire environment
as Apache-only merely because a dependency is installed separately.

No third-party implementation is intentionally bundled. The HDMI compile stubs
are interface declarations and are excluded from the APK. Voice compilation
reads twelve declarations from the local OEM APK, without copying method bodies;
the generated declarations are also excluded from the DEX.

The audio adapter's ABI offsets and the firmware transformations come from
analysis of the supported releases. This is not a clean-room claim or a blanket
legal clearance. The EROFS reader describes the Linux v5.4 on-disk format; kernel
headers are not included. Third-party source additions need their own provenance
and license review.

## Private outputs

The following stay outside the repository:

- OTA archives, extracted images and OEM APKs/libraries/modules.
- Generated policy, board overlays, ramdisks, loaders and assembled images.
- Signing keys, certificates, generated PKCS#8 files and local profiles.
- Calibration, device identities, pairing records and application data.
- Device backups and raw logs.

VNDK packaging uses the owner's local key, not an OEM signing key. It signs a
built-in APEX for this local environment and grants no ability to sign Xiaomi
firmware. Keep the entire packaging output private: it includes a PKCS#8 key.

The bootstrap builder accepts a specific original B boot hash and applies two
checked instruction changes to a local init copy. The panel module is retained
unchanged. These local outputs contain third-party material; excluding them from
Git does not grant permission to redistribute them elsewhere.

The runtime profile contains image hashes, local paths and an owner's ADB public
key. Even a public-key comment can identify its owner. Generated outputs must
not be attached to a public release without a separate contents and rights review.
