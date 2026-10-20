# First-entry activation

[简体中文](activation.zh-CN.md)

`activate-first-entry.py` checks retained backups, a successful serial undo,
staging and a refusal probe before arming one temporary entry. It supports the
[owner capture](owner-capture.md) and [Google setup](first-install.md) loaders.
It preserves original B, does not flash images, and does not reboot.

This coordinator has offline tests, including its CLI and the real transaction
with a simulated console. It has not activated a fresh installation on hardware.
Do not use it to replace an existing installation.

## Evidence

Keep all files outside this repository. Use records actually collected on the
same TV, in this order:

1. Retain an independently verified partition-prefix backup and the original
   stock boot arguments. The [prefix exporter](boot-backup.md) produces the
   required backup manifest. Keep separate backups of data that matter.
2. For the first owner capture, stage and probe its loader, then use the
   [RAM-only undo exercise](../RECOVERY.md#establish-the-undo-before-first-activation).
   Retain the restore outputs and verify a subsequent normal HyperOS B boot.
   Google setup can reuse this demonstrated undo on the same TV and stock profile.
3. Stage the chosen loader/files, or reverify the already-staged capture loader,
   and complete their non-PID-1 refusal probe.
   Record the stock boot arguments and full kernel command line during that
   same normal stock boot. Do not reboot between this observation and the probe.
4. Prepare the manifests below, check them offline, then stop the supported B
   console using the already verified serial connection.

The coordinator does not establish the initial serial connection or create
the backups. A manifest binds recorded files; it cannot prove which TV the
adapter was physically attached to or when the files were collected. Do not
invent successful results, copy another owner's records or alter failed journals.
If the serial undo has not actually been demonstrated, stop before activation.

A file record always has this form:

```json
{"path": "/private/example.bin", "bytes": 123, "sha256": "FULL_RECORDED_SHA256"}
```

Paths can be absolute or relative to the containing manifest. The placeholders
below intentionally fail validation. Use the independently recorded device
binding and scheme throughout.

The **backup manifest** lists consecutive chunks of the original eMMC user-area
prefix, from byte zero through the start of userdata:

```json
{
  "version": 1,
  "deviceBindingSha256": "OWNER_BINDING_SHA256",
  "deviceBindingScheme": "freeguy-emmc-cid-serial-v1",
  "prefixBytes": 4750049280,
  "chunks": [
    {"offset": 0, "file": {"path": "prefix-000.img", "bytes": 67108864, "sha256": "CHUNK_SHA256"}}
  ]
}
```

List every chunk; the example is incomplete. Chunks must be sector aligned,
no larger than 128 MiB each, with no gaps, overlaps or repeated files.
Every byte is hashed before activation. This is not a full-chip backup:
userdata, hardware boot areas and RPMB are excluded.

The **serial undo manifest** contains `version`, the same two device-binding
fields, and `files`, a mapping from each of these names to a file record:

```text
result.json
handshake.bin
00.bin ... 09.bin
```

These are the unedited outputs of the public `serial-recovery.py restore`
command. The coordinator checks the B identity, temporary arguments, exact
inverse, save acknowledgement and restored argument readbacks. An inspection
or a no-op restore of an already-stock configuration is insufficient.

The **preflight manifest** references the completed evidence:

```json
{
  "version": 1,
  "deviceBindingSha256": "OWNER_BINDING_SHA256",
  "deviceBindingScheme": "freeguy-emmc-cid-serial-v1",
  "staging": {"path": "stage/journal.json", "bytes": 1, "sha256": "STAGING_SHA256"},
  "verification": {"path": "probe/journal.json", "bytes": 1, "sha256": "PROBE_SHA256"},
  "bootObservation": {"path": "boot.json", "bytes": 1, "sha256": "BOOT_SHA256"},
  "backup": {"path": "backup.json", "bytes": 1, "sha256": "BACKUP_MANIFEST_SHA256"},
  "serialUndo": {"path": "undo.json", "bytes": 1, "sha256": "UNDO_MANIFEST_SHA256"}
}
```

`bootObservation` contains `stockBootargs`, `observedBootargs`, `kernelCmdline`,
`deviceBindingSha256`, `deviceBindingScheme` and `bootId`. Its boot ID must match
the completed probe. Both saved and running arguments must describe the normal
stock boot. Keep the complete values private.

## Inspect, then arm

For Google setup:

```sh
python3 tools/activate-first-entry.py inspect \
  --preflight /private/preflight.json \
  --preflight-sha256 VERIFIED_PREFLIGHT_SHA256 \
  --recovery-profile /private/recovery-profile.json \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_BINDING \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --inputs /private/first-install-inputs.json \
  --docker /path/to/docker \
  --filesystem-container sha256:REVIEWED_LOCAL_IMAGE_ID \
  --output /private/preflight-inspect-01
```

Use a locally reviewed e2fsprogs container by its full image ID. Each ext4 input
is checked with `e2fsck -fn`, mounted read-only in a container with no network,
no capabilities and no image pull. Any nonzero result stops activation; inspect
the private log rather than suppressing it or repairing the original input.

For owner capture, replace `--inputs`, `--docker` and `--filesystem-container`
with `--stock-boot`, `--capture-build` and `--loader-build`, using the paths from
the owner-capture build. That loader does not use constructed ext4 inputs.

After reviewing inspection, use `arm` instead of `inspect`, a new output
directory and `--port /dev/YOUR_VERIFIED_SERIAL_PORT`. The tool repeats the
evidence checks, obtains a fresh stopped B prompt, and checks the exact stock
arguments and B state again before writing. It calls the existing transaction
once: set the checked temporary arguments, read them back, save, and read back
again. It never sends reset or changes slots, retry counters or firmware images.

On success, retain `preflight.json`, `plan/journal.json` and `arm/journal.json`
from that output directory. Use the latter two with
[`boot-first-entry.py`](first-install.md#observe-the-first-normal-boot).
Do not substitute the inspection plan for the plan generated by the arm run.

On failure, preserve all output and inspect the pending transaction. A lost
reply may follow a completed write. Do not retry or reboot blindly; use the
verified serial undo when needed. Save acknowledgement and RAM readback still
require independent physical-storage and subsequent-boot checks.

```sh
python3 -B tests/test-activate-first-entry.py
python3 -B tests/test-first-activation-console.py
python3 -B tests/test-boot-first-entry.py
```
