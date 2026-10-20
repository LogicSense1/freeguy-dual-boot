# Boot-chain backup

[简体中文](boot-backup.zh-CN.md)

`backup-boot-prefix.py` reads the tested board's eMMC user-area prefix through
the stopped B console. It exports 71 files, totalling 4,750,049,280 bytes, to
a FAT USB drive. It never writes internal storage, changes startup arguments
or reboots.

The prefix contains the partition table, original firmware and device-specific
configuration before userdata. It excludes userdata, eMMC hardware boot areas
and RPMB. It contains private device material; do not publish it.
Raw bootloader reads and Android logical-partition reads can differ. Do not
interpret unequal hashes across those paths as corruption.

## Prepare

- Establish a readable, interactive [HDMI serial connection](equipment.md).
  Stop the supported February 2025 original B bootloader at a clean prompt.
- Independently record the owner's device binding and original stock argument
  profile. The serial tool compares the profile but cannot identify the physical
  TV from the adapter alone.
- Use a FAT-formatted USB drive with at least 5 GiB free. Do not format a drive
  containing needed files. The supported bootloader must expose it as `usb 0:1`.
- On the computer, create a new empty directory on that drive, named
  `freeguy-backup-` followed by 8–32 lowercase letters or digits. Use a unique
  name. Keep at least 6 GiB free on the computer for an independent copy.
- Eject the drive from the computer and connect it to the TV. The reference
  backup used the TV's USB 3.0 port; the UART adapter remains on HDMI 1.

The tool does not alter a partition table, initialize a disk or choose another
USB device automatically. An occupied directory, different board geometry,
unexpected B state or failed command stops the export.

## Export

Close other serial readers. Substitute the actual directory name:

```sh
python3 tools/backup-boot-prefix.py capture \
  --port /dev/YOUR_VERIFIED_SERIAL_PORT \
  --usb-directory /freeguy-backup-YOUR_UNIQUE_LOWERCASE_SUFFIX \
  --recovery-profile /private/recovery-profile.json \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_BINDING \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --output /private/prefix-export-01
```

The fixed plan checks the complete 42-partition geometry, uses only the tested
64 MiB RAM window and stops before userdata. Each chunk is read from eMMC,
checksummed in RAM and written to its fixed USB filename. This OEM build returns
only the command echo and prompt for a successful `mmc read`; unexpected text
is rejected. The private journal records each pending command before dispatch.

Require `complete: true` in `capture.json`. USB write acknowledgement is not
independent backup verification. If interrupted, retain the partial files and
journal; there is no overwrite, automatic retry or resume. Do not change boot
configuration based on an incomplete export.

## Verify an independent copy

After export finishes and the console is idle, connect the USB drive to the
computer. Keep the TV's configuration unchanged. Record the SHA-256 of the
unaltered `capture.json`, then run:

```sh
python3 tools/backup-boot-prefix.py verify \
  --capture-record /private/prefix-export-01/capture.json \
  --capture-record-sha256 VERIFIED_CAPTURE_RECORD_SHA256 \
  --source /Volumes/YOUR_USB/freeguy-backup-YOUR_UNIQUE_LOWERCASE_SUFFIX \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_BINDING \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --output /private/prefix-copy-01
```

This local step checks sizes and RAM CRCs, copies into a new private directory,
flushes and rereads every copy, and records SHA-256 values. It never accesses
the TV. Only the complete copy receives `backup.json`, in the format accepted
by the [activation coordinator](activation.md). Keep both copies.

The generalized tool has synthetic export/interruption and copy-validation
tests. Its geometry and response parsers also matched all 71 chunks of the
reference device's retained capture. The new CLI has not performed a new
hardware export. A verified backup does not itself provide a restore route;
demonstrate [serial undo](../RECOVERY.md#establish-the-undo-before-first-activation)
before activation.
