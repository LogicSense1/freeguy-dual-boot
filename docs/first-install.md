# First installation

[简体中文](first-install.zh-CN.md)

The daily runtime requires an existing shared preference directory, fault file
and switch-service permissions. On the reference TV these were prepared during
an earlier temporary Google boot. A fresh installation cannot start directly
with the daily runtime.

This guide covers the build that removes that dependency. Checked staging,
first-boot activation and switch-access provisioning are still being integrated.
It is not yet an end-to-end installation procedure.

## Inputs

- Match the board, original B boot chain and exact [firmware inputs](firmware.md).
- Establish and test [serial recovery](../RECOVERY.md). Keep independently
  verified configuration and firmware backups outside the TV.
- Build the system files, adapters and complete daily ramdisk with the
  [build map](../BUILD.md). Retain exact hashes and the local signing key.
- Prepare fresh data from this TV's own inputs using the [data guide](data.md).
  Never replace an existing installation's data with newly initialized files.

The setup runtime refuses to start if
`/data/local/codex-system-default-v1` already exists, including a dangling
symlink. It must not substitute for an installed daily runtime.

## Setup root

Use the fully assembled daily ramdisk, including the current hardware policy.
Outputs belong in new directories outside the checkout:

```sh
python3 tools/build-first-install-root.py \
  --daily-ramdisk /private/daily-root/ramdisk.cpio.gz \
  --daily-sha256 VERIFIED_DAILY_RAMDISK_SHA256 \
  --output /private/first-install-root-01
```

Only `codex/entry.sh` and `codex/runtime-policy-start.sh` in both ramdisk roots
change. The tool checks their exact daily source, preserves modes and owners,
and refuses an already-patched input.

The setup root:

- Uses the earlier bounded watchdog: 600 seconds to start, a 120-second health
  lease, and a 3,600-second setup-session limit.
- Does not require, create, bind or modify shared preference/fault files.
- Retains image checks, firmware-device read-only protection, the owner's ADB
  key and current hardware-policy startup.
- Does not start daily remote voice. Enable and validate it after installing the
  daily runtime; setup is not the final functional acceptance.

The daily runtime is unchanged and still has **no one-hour operating limit**.
Setup uses uptime for its watchdog and heartbeat, matching the earlier temporary
runtime. Daily operation retains its separate monotonic-clock policy.

## Loader

Use the output hash from `first-install-root-01/manifest.json`:

```sh
python3 tools/build-loader.py \
  --ramdisk /private/first-install-root-01/ramdisk.cpio.gz \
  --ramdisk-sha256 VERIFIED_FIRST_INSTALL_RAMDISK_SHA256 \
  --clang /path/to/clang --ld-lld /path/to/ld.lld \
  --output /private/first-install-loader-01

python3 tools/audit-loader.py --build /private/first-install-loader-01
```

The loader retains the `envdaily` entry name and exact self-removal logic.
This is **not permission to overwrite an installed `envdaily` file**. Keep
setup and daily loaders in separate local directories. Both contain OEM files
and private build inputs and must not be published.

The loader writes and verifies both startup-environment files before handoff.
Earlier failure can still require serial undo. A RAM filesystem does not make
this a read-only boot.

## Local file inventory

Copy the [input template](../examples/first-install-inputs.example.json) outside
the repository. Fill in the paths, exact byte counts and independently retained
SHA-256 values. The template intentionally fails validation until completed.
It lists all 18 backing images, two external voice dependencies and the setup
root and loader. Keep the loader's `manifest.json` and `audit.json` beside it.

```sh
python3 tools/plan-first-install.py \
  --inputs /private/first-install-inputs.json \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --output /private/first-install-plan-01
```

Use `freeguy-android-serial-v1` instead only when that was the explicitly
recorded binding scheme; the tool does not downgrade automatically.
The tool hashes the local files and checks the setup root's source scripts,
mount order, immutable-image list and external voice hashes. It then matches
the loader manifest and 64-case audit to that root and current loader source.
Its private `plan.json` has 21 fixed file destinations and marks every existing
destination as non-overwritable. The compressed root is a local build input,
not a separate upload. The plan also lists the empty scratch directory and
generated stage marker. Existing stage, loader, shared-state or proxy directories
must cause initial staging to stop.

This performs no upload or device access. It neither establishes same-device
provenance nor checks filesystem health; retain those records separately.
An audit record describes an offline run, not a signature or a live test.
The plan does not authorize installation. The future staging step must recheck
local inputs, the current TV, free space and every destination before writing.
Do not use fresh data files to replace an existing installation.

## Remaining device sequence

Each step must be established on the recipient's own TV:

1. Stage exact system/data files, external voice dependencies, loader and scratch
   directory without overwriting an existing installation.
2. Check uploaded hashes, free storage, permissions and the loader's non-PID-1
   refusal before changing startup state.
3. Activate the exact one-time entry from the checked B console. Observe startup
   and independently verify both environment copies restored to HyperOS values.
4. In temporary Google, prepare switch-service access and shared state, install
   both switch apps and preserve this TV's remote pairing.
5. Replace the setup loader with the separately built daily loader through a
   reviewed file transaction. Retain the predecessor and matching app profiles.
6. Verify both switches, remembered defaults, retained data and serial recovery.
   Physical cold-power startup is a separate acceptance item.

Complete checked commands for steps 1–5 are not yet provided. Do not fill these
gaps with old AVB experiments, donor flashing, hand-edited environments or
guessed service calls.

## Evidence

The earlier temporary entry reached Google on the reference TV. This new setup
root combines that bounded-watchdog approach with the current hardware policy.
It has built successfully; its loader passed 64 ARM syscall-model cases and
checked 1,005 embedded entries. **This combination has not been booted on a TV.**

```sh
python3 -B tests/test-first-install-root.py
python3 -B tests/test-first-install-plan.py
python3 -B tests/test-runtime-policy-start.py
```
