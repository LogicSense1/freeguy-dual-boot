# Build the local images

[简体中文](images.zh-CN.md)

These tools operate on local files. Outputs are backing files for the runtime,
not OTA packages or images to flash to physical partitions.

Start with the exact [official OTA inputs](firmware.md), extract their partitions
locally, and retain the originals. Use [external build tools](../DEPENDENCIES.md).
The scripts reject unsupported image hashes; replacing a hash constant does not
adapt a different release.

## Working directories

The commands below use these owner-selected locations:

```sh
TV_INPUTS=/path/to/private-inputs
TV_BUILD=/path/to/new-private-build
TV_TOOLS=/path/to/tools
TV_KEYS=/path/to/private-keys
TV_EXT4_IMAGE=your-local-ext4-tool-image
TV_POLICY_IMAGE=your-local-policy-tool-image
```

`TV_INPUTS/hyperos/` contains the original `system.img` and `vendor.img`.
`TV_INPUTS/google/` contains donor `system.img`, `system_ext.img`, `product.img`
and `vendor.img`. These commands expect extracted raw images, not Android sparse
containers. Each tool requires a fresh output directory.

For the example commands, `TV_TOOLS` holds the supplied tools named below.
Activate a private Python environment with `dissect.extfs` first. Use a local
e2fsprogs container with `mke2fs`, `debugfs` and `e2fsck`, and a local policy
container with `secilc`. The builders never pull those images.

Hash arguments shown as `HASH_FROM_...` must be replaced with the full SHA-256
from the indicated local manifest. They are not passwords. Do not use this
example as an unattended shell script.

## 1. Extract and compile the compatibility policy

```sh
python3 tools/extract-policy-inputs.py \
  --stock-vendor "$TV_INPUTS/hyperos/vendor.img" \
  --system "$TV_INPUTS/google/system.img" \
  --system-ext "$TV_INPUTS/google/system_ext.img" \
  --product "$TV_INPUTS/google/product.img" \
  --vendor "$TV_INPUTS/google/vendor.img" \
  --dump-erofs "$TV_TOOLS/dump.erofs" \
  --output "$TV_BUILD/policy-inputs"

python3 tools/build-sepolicy.py \
  --profile "$TV_BUILD/policy-inputs/profile.json" \
  --docker-compiler "$TV_POLICY_IMAGE" \
  --output "$TV_BUILD/policy"
```

The extractor reads 29 fixed files from five hash-pinned images and creates a
relative-path profile. No manual copy of a previous installation's policy is
needed. Compilation reconciles the checked API 30 mappings and three context
declarations. This reproduces a **permissive** compatibility environment with
neverallow checks disabled. It is not an enforcing security policy.

## 2. Build the vendor backing image

Use `vendorCilSha256` from `policy/manifest.json`:

```sh
python3 tools/build-vendor-image.py \
  --stock-vendor "$TV_INPUTS/hyperos/vendor.img" \
  --vendor-cil "$TV_BUILD/policy/vendor-fileboot.cil" \
  --vendor-cil-sha256 HASH_FROM_POLICY_MANIFEST \
  --docker-image "$TV_EXT4_IMAGE" \
  --output "$TV_BUILD/vendor"
```

This modifies a new copy, disabling original formatting, recovery-flashing and
slot-management services in that copy. It checks every reachable file's content,
mode, owner, xattrs and symlink target. The original input is not changed.

## 3. Package the original VNDK 30 libraries

```sh
python3 tools/extract-vndk30.py \
  --stock-system "$TV_INPUTS/hyperos/system.img" \
  --output "$TV_BUILD/vndk-inputs"
```

Supply your own private RSA-4096 key and matching X.509 certificate for this local
APEX. Retain them for subsequent builds. The builder verifies that their public
keys match; neither a project key nor an OEM key is supplied.

```sh
python3 tools/build-vndk-apex.py \
  --input-manifest "$TV_BUILD/vndk-inputs/manifest.json" \
  --input-manifest-sha256 HASH_OF_INPUT_MANIFEST_FILE \
  --input-directory "$TV_BUILD/vndk-inputs/files" \
  --avb-key "$TV_KEYS/apex.pem" --certificate "$TV_KEYS/apex-cert.pem" \
  --avbtool "$TV_TOOLS/avbtool.py" --aapt2 "$TV_TOOLS/aapt2" \
  --android-jar "$TV_TOOLS/android.jar" \
  --apksigner-jar "$TV_TOOLS/apksigner.jar" --java "$TV_TOOLS/java" \
  --docker-image "$TV_EXT4_IMAGE" --output "$TV_BUILD/vndk-apex"
```

It verifies all 294 source files, payload metadata, page alignment and both
signatures. The entire output remains private, including the generated PKCS#8
key. This does not produce an OEM-signed APEX or a flashable TV package.

## 4. Build framework and final system images

Use `outputSha256` from `vndk-apex/manifest.json`:

```sh
python3 tools/build-framework-images.py \
  --system "$TV_INPUTS/google/system.img" \
  --system-ext "$TV_INPUTS/google/system_ext.img" \
  --vndk-apex "$TV_BUILD/vndk-apex/com.android.vndk.v30.apex" \
  --vndk-apex-sha256 HASH_FROM_APEX_MANIFEST \
  --dump-erofs "$TV_TOOLS/dump.erofs" \
  --mkfs-erofs "$TV_TOOLS/mkfs.erofs" \
  --fsck-erofs "$TV_TOOLS/fsck.erofs" \
  --output "$TV_BUILD/framework"

python3 tools/build-system-image.py \
  --framework-system "$TV_BUILD/framework/system-vndk30.erofs" \
  --framework-system-sha256 HASH_FROM_FRAMEWORK_SYSTEM_MANIFEST \
  --stock-system "$TV_INPUTS/hyperos/system.img" \
  --vndk-apex-sha256 HASH_FROM_APEX_MANIFEST \
  --dump-erofs "$TV_TOOLS/dump.erofs" \
  --mkfs-erofs "$TV_TOOLS/mkfs.erofs" \
  --fsck-erofs "$TV_TOOLS/fsck.erofs" \
  --output "$TV_BUILD/system"
```

The framework system hash is `images.system.outputSha256` in
`framework/manifest.json`. The final system builder removes incompatible
startup/OTA actions and copies the original firmware's migration script locally.
It does not read device data or extract DRM keys.

Use these three outputs in the later runtime profile:

| Runtime file | Build output |
| --- | --- |
| `system.img` | `system/system-fileboot.erofs` |
| `system_ext.img` | `framework/system_ext-vndk30.erofs` |
| `vendor.img` | `vendor/vendor-fileboot.img` |

Additional read-only backing images, runtime overlays and same-device data are
still required. These three files alone cannot boot the complete system.

## 5. Extract board and audio overlays

Use the original image hashes checked by the builders: the stock vendor hash is
in `tools/build-vendor-image.py`, stock system in `tools/extract-vndk30.py`, and
donor system in `tools/build-framework-images.py`.

```sh
python3 tools/extract-board-inputs.py \
  --stock-vendor "$TV_INPUTS/hyperos/vendor.img" \
  --stock-vendor-sha256 VERIFIED_STOCK_VENDOR_HASH \
  --google-system "$TV_INPUTS/google/system.img" \
  --google-system-sha256 VERIFIED_DONOR_SYSTEM_HASH \
  --dump-erofs "$TV_TOOLS/dump.erofs" --output "$TV_BUILD/board-inputs"

python3 tools/render-board-overlays.py \
  --profile "$TV_BUILD/board-inputs/overlay-profile.json" \
  --output "$TV_BUILD/board-overlays"

python3 tools/extract-audio-libraries.py \
  --stock-system "$TV_INPUTS/hyperos/system.img" \
  --stock-system-sha256 VERIFIED_STOCK_SYSTEM_HASH \
  --stock-vendor "$TV_INPUTS/hyperos/vendor.img" \
  --stock-vendor-sha256 VERIFIED_STOCK_VENDOR_HASH \
  --google-system "$TV_INPUTS/google/system.img" \
  --google-system-sha256 VERIFIED_DONOR_SYSTEM_HASH \
  --dump-erofs "$TV_TOOLS/dump.erofs" --output "$TV_BUILD/audio-inputs"
```

The panel module stays unchanged. Board overlays configure file-backed mounts,
audio channels and remote behavior. Audio extraction supplies six unique
libraries at seven overlay paths. These are third-party files, not public source.
Continue with the [hardware](hardware.md) and [runtime](runtime.md) guides.

## Evidence and limits

Each image builder verifies its own output and writes a local manifest.
Do not reuse another build's hashes: filesystem UUIDs, metadata and local signing
keys can change the container hash even when the file contents match.
The readback checks do not compare timestamps or inode numbers and do not prove
hardware behavior.

On 2026-10-20, the public tools rebuilt system, system_ext and vendor and passed
their complete content/metadata checks. Fresh policy extraction reproduced all
29 prior input hashes and the identical compiled policy. Those fresh image files
were not deployed in that check; the already running installation was retained.

Offline tests use synthetic data:

```sh
python3 tests/test-policy-inputs.py
python3 tests/test-sepolicy.py
python3 tests/test-vendor-image.py
python3 tests/test-vndk-apex.py
python3 tests/test-framework-images.py
python3 tests/test-system-image.py
python3 tests/test-board-overlays.py
```
