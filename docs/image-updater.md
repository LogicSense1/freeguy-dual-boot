# Updating the four system files

[简体中文](image-updater.zh-CN.md)

This updater replaces four inactive files in an existing installation:

- `/data/local/tmp/codex-gtv-fileboot-v1/system.img`
- `/data/local/tmp/codex-gtv-fileboot-v1/vendor.img`
- `/data/local/tmp/codex-gtv-fileboot-v1/system_ext.img`
- `/data/local/tmp/rd-root/envdaily`

It does not write physical partitions, initialize Google data, select a default
system or reboot. It is not a first-installation tool or an OTA updater.
Keep the old files, matching switch-app builds and a verified
[serial recovery route](../RECOVERY.md).

## Build

Use two private [switch profiles](switch-app.md), describing the installed and
proposed complete runtime. The model, bootloader and normalized stock boot
arguments must match. Exactly the four files above must have changed hashes;
changes to other dependencies are rejected.

```sh
python3 tools/build-image-updater.py \
  --old-profile /private/installed-profile.json \
  --new-profile /private/candidate-profile.json \
  --java-home /path/to/jdk-17 \
  --android-jar /path/to/android.jar \
  --d8-jar /path/to/r8.jar \
  --output /private/image-update-01
```

The builder creates `dex/classes.dex`, generated pins and `plan.json` in a new
directory outside the checkout. The plan fixes all source, destination and
backup paths and both versions' SHA-256 values. Candidate files are expected
in `/data/local/tmp/codex-gtv-independent-candidate-01/`; the loader is named
`loader` there. Old files are retained as `<active>.previous-<plan-id>`.
The build neither uploads nor executes anything.

`--probe` creates a different plan targeting `.update-probe-*` files in the same
three directories. Its old and new inputs are disposable 4096-byte fixtures.
It cannot stand in for a real firmware compatibility test. Run interruption
experiments on those disposable files, not on active system images.

## Required device state

The Java runner requires all of the following:

- Original `freeguy` Android 11 B, bootloader `01.01.250225`, completed startup
  and green verified-boot state.
- Execution as the provisioned system service UID 1000.
- HyperOS selected as default, with preference phase `IDLE`.
- No mounted or attached loop images; the updater's `/data` mount must be f2fs.
- The switch lock is held for this invocation, preventing a concurrent switch.
- Both physical environment files pass CRC and a reversible stock-argument check
  through the OEM read service.

The runner does not grant itself access. Shared-state directory metadata must
already be correct: UID/GID 1000, mode 0700, `system_data_file` context. State and
lock files must be regular, singly linked UID/GID 1000 files with mode 0600.
The three image directories require UID/GID 1000, mode 0750 and
`system_data_file` context.

Image files must be regular, singly linked, owned by UID 0, 1000 or 2000 with
GID 1000, mode 0640 or 0750, the same context, and size from 4096 bytes to 2 GiB.
These checks describe this implementation's provisioned layout. They are not
instructions to recursively change ownership or labels on `/data`.
Do not change the seven personal-data images to satisfy an updater check.

The checked host-side provisioning and execution wrapper is not yet included.
An ordinary ADB shell or root invocation is not a substitute for the required
UID and service context. Do not bypass a refused check.

## Transaction and result

The entry class is `local.codex.tvswitchprobe.ImageUpdateRunner`; its actions are
`inspect`, `apply` and `rollback`. Each row is recognized from actual file hashes:

| State | Active path | Candidate path | Backup path |
| --- | --- | --- | --- |
| ORIGINAL | old | new | absent |
| SAVED | absent | new | old |
| INSTALLED | new | absent | old |

An unknown combination stops the transaction. Before each rename the runner
checks the device and file identities again; after renaming it fsyncs both
parent directories. `apply` can resume a recognized partial application.
`rollback` walks the rows in reverse and restores the original files.
It does not delete the candidate or silently overwrite an occupied backup.

The final inspection rehashes bytes rather than relying on the invocation's
hash cache. The runner logs errors instead of propagating a nonzero process
exit code. **Exit status alone does not prove success.** Correlate a fresh run
with its plan and boot identity, require its `VERIFIED` and `COMPLETE` records,
and reject any `STOPPED` result. An observer timeout must not trigger another
updater while the first invocation might still be running.

An optional second argument from 1 to 8 stops after that durable rename, for
controlled interruption tests. A requested stop is not a successful update.
The caller must inspect and resolve the remaining file states before trying
to boot Google.

The switch app also pins image hashes. Keep matching old and new builds and a
reviewed trial/rollback sequence; replacing four files does not update those
pins automatically. Startup and physical hardware acceptance remain separate.

## Evidence and limits

The reference TV passed disposable-file interruptions after renames 1 and 4,
their rollbacks, and a full real four-file apply/rollback cycle without leaving
the original HyperOS boot. The old files were retained. These were controlled
process interruptions, not power cuts.

A fresh public-source build reproduced that tested plan. Its 12 classes match
the tested runner's disassembly after accounting for the diagnostic label
change from Xiaomi to HyperOS. That fresh DEX has not been deployed.

```sh
python3 -B tests/test-image-updater.py
python3 tools/test-core.py --java-home /path/to/jdk-17
```

The image transaction suite covers 56 cases, including stops before and after
each rename, resumption, rollback and unknown-file rejection. It does not prove
atomicity across arbitrary power loss or replace device-specific validation.
