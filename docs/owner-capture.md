# Capture this TV's own data

[简体中文](owner-capture.zh-CN.md)

The OTA packages do not contain the owner's calibration and identity data.
This component captures `uenv`, `uuenv`, `persist` and `tvcertificate` from the
same TV before stock Android starts. The files are private inputs for local
assembly. Never publish them or write them back to physical partitions.

The reference TV completed this capture and returned to HyperOS. Transfer
verified all four files, both environment CRCs, stock boot arguments, the owner
binding and the current boot identifier. This does not establish compatibility
with another board or firmware.

## Build

Use the exact original B `boot.img` from the [verified OTA](firmware.md).
Build in new private directories, using the [local dependencies](../DEPENDENCIES.md).

```sh
python3 tools/build-owner-capture.py \
  --clang /path/to/clang --ld-lld /path/to/ld.lld \
  --output /private/owner-capture-01
python3 tools/audit-owner-capture.py --build /private/owner-capture-01

python3 tools/build-owner-root.py \
  --stock-boot /private/stock/boot.img \
  --capture-build /private/owner-capture-01 \
  --output /private/owner-root-01
```

Use the `ramdiskSha256` from the root manifest:

```sh
python3 tools/build-loader.py \
  --ramdisk /private/owner-root-01/ramdisk.cpio.gz \
  --ramdisk-sha256 VERIFIED_OWNER_ROOT_SHA256 \
  --owner-capture-build /private/owner-capture-01 \
  --stock-boot /private/stock/boot.img \
  --clang /path/to/clang --ld-lld /path/to/ld.lld \
  --output /private/owner-loader-01
python3 tools/audit-loader.py --build /private/owner-loader-01
```

The generated root and loader contain OEM files. They must stay outside Git.
The ordinary Google loader build remains unchanged.

## Stage a fresh capture

This path is for a fresh stock installation only. It refuses an existing Google
data directory, loader, shared preference, proxy or capture directory. Do not
use it to replace an installed loader.

```sh
python3 tools/stage-owner-capture.py inspect \
  --adb /path/to/adb --device TV_ADB_SERIAL_OR_ENDPOINT \
  --stock-boot /private/stock/boot.img \
  --capture-build /private/owner-capture-01 \
  --loader-build /private/owner-loader-01 \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --output /private/owner-stage-inspect-01
```

After inspection, use `stage` with a new output directory. It creates only
`/data/local/tmp/rd-root/envdaily` and `owner-capture-stage-v1` in a new directory.
The uploader uses exclusive creation, checks metadata and hashes, and syncs
files and parent directories. It does not execute the loader.

Use `probe` with the same build/owner arguments, a new output directory, and
`--staging-record /private/owner-stage-01/journal.json
--staging-record-sha256 VERIFIED_STAGING_JOURNAL_SHA256`.
The shared probe protocol rechecks the files and runs the audited non-PID-1
path, which must exit 90 without environment writes.

The generalized staging/probe path has synthetic interruption tests and a
verified local plan built from the live-tested capture. It has not uploaded
to a fresh TV; the reference live test preserved an existing daily installation.

## Execution and recovery

`plan-owner-activation.py` joins the audited build, completed stage and refusal
probe with a same-device, same-boot observation. The private observation contains
`stockBootargs`, `observedBootargs`, `kernelCmdline`, `deviceBindingSha256`,
`deviceBindingScheme` and `bootId`. Read the original values from the TV and the
verified serial record; do not substitute a previous device's values.

```sh
python3 tools/plan-owner-activation.py \
  --stock-boot /private/stock/boot.img \
  --capture-build /private/owner-capture-01 \
  --loader-build /private/owner-loader-01 \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --staging-record /private/owner-stage-01/journal.json \
  --staging-record-sha256 VERIFIED_STAGING_JOURNAL_SHA256 \
  --verification-record /private/owner-probe-01/journal.json \
  --verification-record-sha256 VERIFIED_PROBE_JOURNAL_SHA256 \
  --boot-observation /private/owner-boot-observation.json \
  --boot-observation-sha256 VERIFIED_OBSERVATION_SHA256 \
  --recovery-profile /private/recovery-profile.json \
  --output /private/owner-activation-plan-01
```

The output uses the shared first-activation format and can be checked by
[`first-activation-console.py inspect`](first-install.md#inspect-the-stopped-serial-console).
It checks the full command-line length and exact inverse. It does not contact
the TV, authorize a write or demonstrate serial recovery.
After an actual checked arm transaction, the shared
[normal-boot observer](first-install.md#observe-the-first-normal-boot) accepts
the corresponding owner-capture plan and arm journal.

Use the [activation coordinator](activation.md) to check retained backup and
serial-undo evidence together with staging and probing before arming the entry.
Staging and probing alone do not authorize startup changes. The coordinator's
fresh-install hardware validation remains outstanding.
For a new TV, obtain the [prefix backup](boot-backup.md), stage and probe this
loader, then perform the [RAM-only undo exercise](../RECOVERY.md#establish-the-undo-before-first-activation).
After the subsequent normal stock boot, rerun the probe and observation before
activation.

The reference test used the already verified original-B entry and serial undo.
It retained the installed daily loader, selected HyperOS as the default,
verified a RAM-only parameter round trip, then armed the exact self-clearing
entry. It did not change a slot, run manual AVB verification or flash firmware.
The original loader was restored and checked afterward.

The outer loader restores both physical startup-environment files before
executing the collector. Failure before that point can still require serial
undo. This is a startup test with persistent configuration changes, not a
read-only ADB backup.

The collector:

- Runs only as PID 1 in a reconstructed stock RAM root.
- Checks the exact block-device numbers, partition offsets and sizes.
- Opens the four source partition devices read-only and reads each twice.
- Mounts only userdata writable and creates a new
  `/data/local/tmp/freeguy-owner-capture-v1` directory.
- Refuses existing output; never deletes, overwrites or resumes a partial set.
- Writes the completion record last, bound to the current kernel boot UUID.
- Restores the unmodified stock init at its original RAM path before executing it.

Reserve at least 2 GiB plus 84 MiB on the TV. The files belong to the ordinary
ADB shell with private permissions. A failed capture remains incomplete.
The collector does not export userdata, pairing records, `misc`, hardware boot
areas or RPMB. It is not a full-device recovery backup.

## Collect before rebooting

Wait for HyperOS to finish starting. Keep this same boot running until collection
finishes; a completion record from another boot is rejected.

```sh
python3 tools/collect-owner-capture.py \
  --adb /path/to/adb --device TV_ADB_SERIAL_OR_ENDPOINT \
  --profile /private/recovery-profile.json \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --output /private/owner-data-01
```

CID-plus-serial binding is the default. If stock permissions prevent CID access,
the explicitly recorded Android-serial binding can be used with
`--binding-source android-serial`; it is a weaker identity check.
Do not derive the expected binding from an unknown downloaded backup.

Collection uses ordinary HyperOS ADB and only reads. It checks exact sizes and
metadata, hashes before and after transfer, completion CRCs, both environment
CRCs and the independently recorded owner. It then rechecks the entire set.
Output remains private and incomplete on failure.

Run read-only filesystem checks on the local files. On the reference capture,
`persist` and `tvcertificate` passed; the environment files required the existing
[new-copy preparation](data.md#prepare-clean-environment-copies). Those prepared
copies passed while preserving reachable data and metadata. Neither the physical
partitions nor the original captures were repaired or replaced.

After collection and restoration of the installed loader, reboot normally into
HyperOS before using the switch app. The capture boot still has temporary
arguments in `/proc/cmdline`, even though the saved configuration was restored.
The switch app correctly rejects that transitional command line.

## Release the temporary entry

A fresh capture's staging directory occupies the name needed by first
installation. `remove-owner-stage.py` can release it only after a completed
capture and a later, normal HyperOS boot. It requires the original source
build, staging journal, retained local data and recovery profile.

```sh
python3 tools/remove-owner-stage.py inspect \
  --adb /path/to/adb --device TV_ADB_SERIAL_OR_ENDPOINT \
  --stock-boot /private/stock/boot.img \
  --capture-build /private/owner-capture-01 \
  --loader-build /private/owner-loader-01 \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --staging-record /private/owner-stage-01/journal.json \
  --staging-record-sha256 VERIFIED_STAGING_JOURNAL_SHA256 \
  --captured-data /private/owner-data-01 \
  --capture-manifest-sha256 VERIFIED_CAPTURE_MANIFEST_SHA256 \
  --recovery-profile /private/recovery-profile.json \
  --output /private/owner-remove-inspect-01
```

After review, use `remove` with a new journal directory. It rechecks owner
binding, stock command line, both staged file identities/hashes and the
independent capture. Any extra directory entry causes refusal.
It unlinks only the loader and marker, then removes the empty directory.
Captured files and other data remain intact.

There is no recursive delete, automatic retry or partial-cleanup resume.
An interrupted removal leaves its pending journal; establish the actual state
before continuing. The removal tool has offline interruption/refusal tests,
not a fresh-install hardware test.

## Evidence limits

The native audit covers 24 syscall/failure scenarios. Five vectors separately
execute the real ARM CRC routine; the syscall model substitutes zlib for long
CRC loops. The outer loader passes its 64-case audit. The host collector tests
cover interruptions, changing files, wrong owners and stale completion records.
These tests do not model a storage controller or physical power loss.

```sh
python3 -B tests/test-collect-owner-capture.py
python3 -B tests/test-stage-owner-capture.py
python3 -B tests/test-plan-owner-activation.py
python3 -B tests/test-remove-owner-stage.py
```
