# Recovery

[简体中文](docs/recovery.zh-CN.md)

The original B system is the fallback. A is not a rescue system on the tested TV:
it has a known verity failure. Keep the B boot chain and partition layout intact.

The public recovery CLI has passed offline tests and a local pseudo-terminal
test. Its underlying console procedure was used during device recovery, but
the standalone CLI's `inspect` and `restore` modes have not completed a live
console test. Its separate `verify-persisted` mode has read the physical
environment on the running reference TV and matched both files to the owner's
stock profile. That mode does not test serial writes or a subsequent boot.

## What is saved

Keep these outside Git, on another machine:

- Original boot configuration, independently read back and CRC checked.
- A verified partition backup covering the original B boot chain and super.
- Cold backups of all seven Google data images, with a device binding.
- The accepted loader and immutable image hashes.
- The local build profile, signing key and UART recovery record.

The tested prefix backup excludes userdata, eMMC hardware boot areas and RPMB.
It is not a full-chip backup. Recovery menu wipes do not repair the old A image
and erase data; do not use them as a routine response to a failed Google boot.

## Google fails after startup

The health watchdog restores service by returning to HyperOS and latching a
fault. Automatic Google startup then remains paused. Inspect the saved startup
logs before explicitly selecting Google again.

The image updater runs only under stock B with Google images detached and
HyperOS selected as the default. It retains the old four immutable files and
can reverse a partially completed replacement. It does not restore user data
or write physical partitions.

## Startup fails before Android

Use the already verified HDMI serial connection and stop the B bootloader.
The tested board uses HDMI 1 and 115200 baud, 8 data bits, no parity, one stop
bit. This is a dedicated TV debug adapter, not a normal HDMI video cable.
Do not infer the electrical wiring from the connector shape.

Close other serial readers and leave a clean, empty command prompt before
opening the recovery tool. Do not leave a partially typed command. Its handshake
sends only a newline and requires the exact `freeguy#` prompt before sending
commands. Use the owner's private switch profile:

```sh
python3 tools/serial-recovery.py inspect \
  --port /dev/cu.YOUR_SERIAL_ADAPTER \
  --profile /private/local-profile.json \
  --output /private/serial-inspect-01
```

`inspect` checks the supported mboot revision, B selection, retry state,
initialization flags and the complete normalized boot configuration.
An unexpected value stops the procedure.

```sh
python3 tools/serial-recovery.py restore \
  --port /dev/cu.YOUR_SERIAL_ADAPTER \
  --profile /private/local-profile.json \
  --output /private/serial-restore-01
```

`restore` only removes the exact `envdaily` startup substitutions. It preserves
the current boot reason and brightness fields, verifies the resulting stock
configuration against the owner's profile, reads back the RAM change, requests
`saveenv`, checks its completion message, and reads the RAM environment again.
That last read does not independently read persistent storage. The result keeps
`persistentReadbackVerified` and `subsequentBootVerified` false; a successful save
message alone is not proof of a recoverable next boot. An already-stock
configuration causes no write.

The output directory must be new and outside this checkout. The tool does not
reboot. Inspect its result before issuing a normal bootloader
`reset` from a console that has passed the checks. Observe the next boot and
interrupt further resets if the first attempt fails.

Do not substitute `avb verify`, `bootm`, slot changes or retry-counter changes
for the normal boot path. Those operations are outside this recovery tool.
It cannot repair damaged firmware, an unusable console, or a hardware fault.

## Independent persistent readback

This additional read-only check requires the accepted Android 14 Google runtime,
an authorized ADB connection, the original B boot image, and read-only physical
block devices. It does not run from the Recovery menu or a stopped bootloader;
it is not an alternative recovery channel.

```sh
python3 tools/serial-recovery.py verify-persisted \
  --adb /path/to/adb \
  --device TV_ADB_SERIAL_OR_ENDPOINT \
  --profile /private/local-profile.json \
  --output /private/persisted-check-01
```

It reads the physical 16 MiB `uenv` partition, not Google's mounted copy.
Both `uboot.env` and `uboot.env.bak` must have valid CRCs and match the known
stock boot-argument hashes. The tool reads the partition twice and checks the
boot identity and B image hash again. It refuses a loop-device substitute,
unexpected device size, writable block device, stale temporary arguments or
an observed change during verification.

The result reports `persistentReadbackVerified`, while `subsequentBootVerified`
and `coldPowerTested` remain false. Successful physical readback is not proof
that the next boot will work. Reboot behavior and the owner's physical screen
still require separate evidence. This check also does not validate every other
environment setting or provide a full-partition backup.

`inspect` and `restore` use Python's standard library on macOS/Linux.
`verify-persisted` additionally imports `dissect.extfs`; see its license and
setup boundary in [Dependencies](DEPENDENCIES.md). Keep raw console logs private.

```sh
python3 -B tests/test-serial-recovery.py
```

## Data restore

[Data recovery preparation](docs/data.md) verifies a bound cold backup and expands it to
new local files. Device installation is not implemented. Never write these
file-backed `uenv.img` or `uuenv.img` images to physical partitions.
Hash equality confirms a copy, not filesystem consistency; check both before
planning a data restore.
