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

An ordinary ADB shell or root invocation is not a substitute for the required
UID and service context. Do not bypass a refused check.

## Prepare access from the accepted Google runtime

`tools/prepare-image-update-access.py` prepares an existing installation for the
stock-side updater. It requires root ADB in the already validated Google runtime,
the accepted helper's SHA-256, a healthy default state and the original f2fs
userdata mount. It does not obtain that initial privileged access or transfer
system images. Stage the four candidate files through the reviewed deployment
path first; the tool verifies all eight old/new hashes.

Do not switch systems, install another update or run a second preparation while
this operation is in progress.

Start with a read-only inspection and a new private output directory:

```sh
python3 tools/prepare-image-update-access.py \
  --serial TV_ADB_SERIAL \
  --helper-sha256 ACCEPTED_RUNTIME_HELPER_SHA256 \
  inspect \
  --build /private/image-update-01 \
  --old-profile /private/installed-profile.json \
  --new-profile /private/candidate-profile.json \
  --output /private/update-access-inspection-01
```

Review the private journal. To perform preparation, use the same inputs with
`prepare` instead of `inspect` and a different output directory. A probe build
also needs `--probe`; it creates only the fixed disposable fixture contents.

Preparation can change ownership, mode and label on the three image directories,
and labels on the eight old/new immutable files. It preserves the original
metadata in the journal. It creates the updater directory when absent, then
copies the content-addressed DEX and optional probe files. An existing file must
already match its expected bytes and metadata; it is never overwritten. No
physical partition, boot argument or personal-data image is written.

Each operation is recorded before dispatch. Readback checks all eight image
hashes, access settings, artifact hashes and the seven personal-data files'
metadata. Those data images remain in use by Google: this is **not** a cold
data backup or a claim that their contents remain static.

After a completed preparation, while still in the same Google boot and before
applying the image update, the previous access settings can be restored:

```sh
python3 tools/prepare-image-update-access.py \
  --serial TV_ADB_SERIAL \
  --helper-sha256 ACCEPTED_RUNTIME_HELPER_SHA256 \
  restore-metadata --run /private/update-access-preparation-01
```

Restoration checks board binding, boot, watchdog, file identities and original
hashes. It retains newly created DEX/probe files and directories; it deletes
nothing. Once images have been replaced or the TV has rebooted, this automatic
restoration is refused.

An interrupted preparation or restoration is not automatically retried. Keep its
journal, establish that the previous command has stopped, and review actual
metadata before an explicit recovery. Partial new files are retained and
rejected on the next preflight. Do not remove evidence or repeat `prepare` until
the interrupted operation has been resolved.

The new tool passed synthetic preparation/restoration tests and 44 before/after
write-interruption cases. Its runtime guard and six fixed-path metadata reads
were checked on the TV without writes. The new preparation and restoration
commands have **not** been exercised on the TV. The earlier device-specific
script's successful preparation does not establish that this new wrapper is
fully validated.

## Start and observe a provisioned updater

`tools/run-image-updater.py` checks the two profiles, fixed plan and local DEX
hash. It expects that exact DEX to have already been provisioned on the TV as
`/data/local/codex-image-update-v1/<sha256>.dex`. It does not upload the DEX,
images or personal data, or grant permissions. This wrapper has offline tests;
the new wrapper has not yet been run on the TV.

Start with `inspect` once the required layout and service access exist:

```sh
python3 tools/run-image-updater.py start \
  --serial TV_ADB_SERIAL \
  --build /private/image-update-01 \
  --old-profile /private/installed-profile.json \
  --new-profile /private/candidate-profile.json \
  --action inspect \
  --output /private/update-inspection-01
```

The explicit serial selects one ADB device. A probe build also requires `--probe`.
`apply` and `rollback` use the same interface, with a new evidence directory for
each deliberate invocation. Their effects are described below.

The tool creates one uniquely named helper script through the original TV
service and launches it once. A shell checks its UID, boot identity and DEX
hash before starting Java. Logs are correlated by a unique run token, shell PID,
Java PID and plan. Success also requires the same stock boot after completion.
Run records and this component's logs remain in the private output directory.

If the connection fails or the observation expires, use the existing run:

```sh
python3 tools/run-image-updater.py observe \
  --run /private/update-inspection-01 --seconds 900
```

`observe` only reads identity and logs; it never recreates or relaunches the
helper. A missing start record or lost log buffer leaves the run unresolved.
Check the saved dispatch phase and running process before another invocation.
The Java transaction lock prevents simultaneous image transactions, but does
not make a blind retry a valid recovery plan. No helper scripts are deleted
automatically.

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
python3 -B tests/test-run-image-updater.py
python3 -B tests/test-prepare-image-update-access.py
python3 tools/test-core.py --java-home /path/to/jdk-17
```

The image transaction suite covers 56 cases, including stops before and after
each rename, resumption, rollback and unknown-file rejection. It does not prove
atomicity across arbitrary power loss or replace device-specific validation.
