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

## Execution and recovery

**The general staging/activation coordinator for this capture is not published
yet. These build commands do not install or activate it.** Do not copy it over
an existing `envdaily` file or improvise a bootloader command.

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

## Evidence limits

The native audit covers 24 syscall/failure scenarios. Five vectors separately
execute the real ARM CRC routine; the syscall model substitutes zlib for long
CRC loops. The outer loader passes its 64-case audit. The host collector tests
cover interruptions, changing files, wrong owners and stale completion records.
These tests do not model a storage controller or physical power loss.

```sh
python3 -B tests/test-collect-owner-capture.py
```
