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
python3 -B tests/test-runtime-policy-start.py
```
