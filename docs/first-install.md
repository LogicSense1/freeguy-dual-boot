# First installation

[简体中文](first-install.zh-CN.md)

The daily runtime requires an existing shared preference directory, fault file
and switch-service permissions. On the reference TV these were prepared during
an earlier temporary Google boot. A fresh installation cannot start directly
with the daily runtime.

This guide covers that build and checked file staging. First-boot activation
and switch-access provisioning are still being integrated.
It is not yet an end-to-end installation procedure.

## Inputs

- Match the board, original B boot chain and exact [firmware inputs](firmware.md).
- Establish and test [serial recovery](../RECOVERY.md). Keep independently
  verified configuration and firmware backups outside the TV.
- Build the system files, adapters and complete daily ramdisk with the
  [build map](../BUILD.md). Retain exact hashes and the local signing key.
- Prepare fresh data from this TV's own inputs using the [data guide](data.md).
  Never replace an existing installation's data with newly initialized files.
- If the two file-backed environment images fail filesystem checks, use the
  [local copy preparation tool](data.md#prepare-clean-environment-copies).
  Retain the originals and use the verified new hashes in the input inventory.

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
The plan does not authorize installation. The staging step must recheck
local inputs, the current TV, free space and every destination before writing.
Do not use fresh data files to replace an existing installation.

## Stage new files

Return to the supported stock HyperOS B system and keep it running. This step
requires its ordinary ADB shell (UID 2000), the same independently recorded device
binding, and no attached loop images. It refuses existing staging, loader,
shared-state or switch-proxy directories, including incomplete earlier uploads.
It does not modify an installed Google environment.

First inspect, using a new private journal directory:

```sh
python3 tools/stage-first-install.py inspect \
  --adb /path/to/adb --device TV_ADB_SERIAL_OR_ENDPOINT \
  --inputs /private/first-install-inputs.json \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --output /private/staging-inspect-01
```

After reviewing the result, use `stage` instead of `inspect`, with a different
new output directory. All local inputs and device checks run again. The command
requires free space for the complete set plus an 8 GiB reserve and checks the
remaining reserve during transfer. It creates ordinary files only, using
exclusive creation rather than overwriting targets.

Each file is hashed after transfer and synced with its parent directory; the
complete set is checked again at the end. The loader becomes executable only
after its uploaded bytes match, but is **not executed**. The stage marker is
written after the payloads. No boot arguments, firmware partitions or existing
data files are written. The command never switches systems or reboots.

On error, preserve the private `journal.json` and leave the files alone.
`pending` identifies the unfinished operation; an upload records its host-client
and remote-shell PIDs when available. A timeout or killed host client does not
prove that the remote process stopped. Establish the current boot/process state
and which files belong to that run before considering cleanup. There is no
automatic retry, resume, deletion or rollback of a partial upload.
A marker alone does not prove successful staging: require `complete: true`.

The staging orchestration passed synthetic interruption tests and local-shell
binary-transfer tests. Current-device read-only checks confirmed the metadata
parser and refusal of the active Google installation. **A full upload to a fresh
stock installation has not been tested on hardware.** Successful `fsync` calls
are not a physical power-loss test.

## Verify staged files and the loader guard

Keep the completed staging journal private and record its SHA-256 independently.
The verification command recomputes the local inventory, checks that journal,
and rechecks all 21 uploaded files plus the marker on the same owner device.
It requires the parent-directory identities recorded by the current staging tool.
It refuses incomplete or altered journals; do not edit a failed record to mark
it complete or rerun staging over the files.

```sh
python3 tools/verify-first-install.py inspect \
  --adb /path/to/adb --device TV_ADB_SERIAL_OR_ENDPOINT \
  --inputs /private/first-install-inputs.json \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --staging-record /private/staging-01/journal.json \
  --staging-record-sha256 VERIFIED_STAGING_JOURNAL_SHA256 \
  --output /private/staged-verification-01
```

Use `probe` instead of `inspect`, with a new output directory, to execute the
checked loader once as the ordinary UID 2000 shell. Its ARM audit must show only
`getpid` and `exit(90)` on the non-PID-1 path, with zero environment writes.
The remote wrapper rechecks the current boot and loader hash immediately before
execution. Only exit code 90 with no unexpected output passes; the loader is
hashed again afterward. This does not exercise its PID-1 startup path.

A later stock boot is allowed only after the full file and directory recheck;
the verification records that new boot identity. A reboot during verification,
wrong result or timeout leaves an incomplete report. The probe is never retried
automatically. Keep any recorded process identifiers and resolve uncertain
completion before running it again.

The command does not change boot parameters or authorize activation.
Filesystem consistency, compatible startup arguments, verified serial undo
and actual first boot remain separate checks. Synthetic tests cover the
verification sequence and local-shell probe protocol; the generalized probe
has not been run on a newly staged TV installation.

## Prepare the first boot entry

`tools/plan-first-activation.py` joins the staging journal and completed refusal
probe with a private boot observation. It calculates the temporary arguments and
requires an exact inverse through the published serial recovery code.

The observation JSON contains `stockBootargs`, `observedBootargs`,
`kernelCmdline`, `deviceBindingSha256` and `deviceBindingScheme`. Retain the
complete original values locally. Match them to the owner's stock profile and
record their source; a file hash alone does not establish how they were obtained.
The full kernel line must contain every observed argument exactly once. Order
can differ because mboot moves some arguments. Additional arguments must not
override the root or init configuration.

```sh
python3 tools/plan-first-activation.py \
  --inputs /private/first-install-inputs.json \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --staging-record /private/staging-01/journal.json \
  --staging-record-sha256 VERIFIED_STAGING_JOURNAL_SHA256 \
  --verification-record /private/staged-probe-01/journal.json \
  --verification-record-sha256 VERIFIED_PROBE_JOURNAL_SHA256 \
  --boot-observation /private/boot-observation.json \
  --boot-observation-sha256 VERIFIED_OBSERVATION_SHA256 \
  --recovery-profile /private/serial-recovery-profile.json \
  --output /private/first-activation-plan-01
```

Both the measured command-line growth and a minimum 626-byte allowance for
bootloader additions must leave 64 bytes below the 2,047-byte limit. Escaped
console commands must also fit. Incomplete probes, changed inventories and
unknown stock configurations are rejected.

This is offline preparation: it sends no commands and does not authorize
activation. Current console state, independently verified backups, demonstrated
serial undo, filesystem health and the actual first boot still need verification.
The reference TV's current daily command line passed the transform and length
checks; that does not validate the new setup runtime.

## Inspect the stopped serial console

With the original B bootloader already stopped, inspect the prepared entry:

```sh
python3 tools/first-activation-console.py inspect \
  --port /dev/YOUR_VERIFIED_SERIAL_PORT \
  --activation-plan /private/first-activation-plan-01/journal.json \
  --activation-plan-sha256 VERIFIED_ACTIVATION_PLAN_SHA256 \
  --recovery-profile /private/serial-recovery-profile.json \
  --output /private/first-console-inspection-01
```

The command requires a fresh `freeguy#` prompt, the supported B bootloader,
unchanged slot/retry state, stock initialization flags and exact stock arguments.
It writes only private host-side logs. It does not stop a running TV or authorize
activation.

The module also contains the write transaction for the future installer. It
records intent before each command, checks the temporary entry in RAM before
`saveenv`, and checks B state before and after saving. It never sends reset,
boot, AVB, slot or retry-counter writes. A failed response or checkpoint stops
the transaction without retry or automatic undo. `saveenv` acknowledgement and
RAM readback do not prove persistent storage or a successful boot.

The standalone CLI deliberately exposes only inspection. Backups, same-device
serial undo, filesystem health and fresh file verification still need to be
joined by the installation coordinator before it can call the write component.
Synthetic failure tests cover every command/checkpoint boundary; a local PTY
test exercises the actual serial transport. This new transaction has not been
used to activate a first installation on the TV.

## Switch access in temporary Google

`tools/prepare-first-switch-access.py` checks the bounded first-install runtime,
the owner's device binding, the running helper and entry-script hashes, and the
staged files. It refuses an installed daily runtime or existing proxy/shared
state. Use the same local inputs and independently recorded binding as staging:

```sh
python3 tools/prepare-first-switch-access.py inspect \
  --adb /path/to/adb --device TV_ADB_SERIAL \
  --inputs /private/first-install-inputs.json \
  --known-device-binding YOUR_RECORDED_DEVICE_BINDING \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --output /private/new-switch-access-inspection
```

The `prepare` action, with a new output directory, changes access metadata on
three directories and fourteen immutable files. It creates a proxy directory,
two fixed symlinks and a 64 KiB scratch file. Symlink ownership and labels are
changed without following their targets. It does not write environment contents,
firmware partitions or the seven data images, install apps, or reboot.

An interrupted run keeps its journal and partial files; do not retry blindly.
Completion means access preparation passed readback, not that the stock service
or the switch apps have been verified. The new tool has synthetic interruption
coverage and has refused the existing daily TV runtime in a read-only check.
Its preparation action has not been run on a first-install TV.

Do not create the shared default directory yet. The temporary runtime refuses
that directory on its next start. Shared state must be committed with the
transition to the daily loader; creating it early can prevent resuming setup
after an interruption. That transition transaction remains to be implemented.

## Remaining device sequence

Each step must be established on the recipient's own TV:

1. Stage exact system/data files, external voice dependencies, loader and scratch
   directory without overwriting an existing installation.
2. Check uploaded hashes, free storage, permissions and the loader's non-PID-1
   refusal before changing startup state.
3. Activate the exact one-time entry from the checked B console. Observe startup
   and independently verify both environment copies restored to HyperOS values.
4. In temporary Google, prepare switch-service access, install
   both switch apps and preserve this TV's remote pairing.
5. Commit shared state and replace the setup loader with the separately built
   daily loader through a reviewed file transaction. Retain the predecessor
   and matching app profiles.
6. Verify both switches, remembered defaults, retained data and serial recovery.
   Physical cold-power startup is a separate acceptance item.

The staging and verification tools cover steps 1–2; their live first-install
validation is outstanding. Complete checked commands for steps 3–5 are not yet
provided. Do not fill these gaps with old AVB experiments, donor flashing,
hand-edited environments or guessed service calls.

## Evidence

The earlier temporary entry reached Google on the reference TV. This new setup
root combines that bounded-watchdog approach with the current hardware policy.
It has built successfully; its loader passed 64 ARM syscall-model cases and
checked 1,005 embedded entries. **This combination has not been booted on a TV.**

```sh
python3 -B tests/test-first-install-root.py
python3 -B tests/test-first-install-plan.py
python3 -B tests/test-first-install-stage.py
python3 -B tests/test-first-install-verify.py
python3 -B tests/test-first-activation-plan.py
python3 -B tests/test-first-activation-console.py
python3 -B tests/test-first-switch-access.py
python3 -B tests/test-runtime-policy-start.py
```
