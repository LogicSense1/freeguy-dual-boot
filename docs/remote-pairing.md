# Share the remote between systems

[简体中文](remote-pairing.zh-CN.md)

The reference TV uses one HyperOS pairing in both environments. Pair the remote
in HyperOS first, then migrate that TV's adapter identity and remote bond into
its isolated Google Bluetooth configuration. Re-pairing independently in each
system can replace the remote's key and break the other system's connection.

`tools/prepare-remote-bond.py` publishes the offline merge used by that workflow.
It requires matching adapter addresses, exactly one supported Xiaomi voice
remote, valid local keys and a HID descriptor. It refuses unrelated Google bonds.
The existing Google non-peer settings are retained.

```sh
python3 tools/prepare-remote-bond.py \
  --stock-config /private/pairing/hyperos-bt_config.conf \
  --google-config /private/pairing/google-bt_config.conf \
  --google-backup /private/pairing/google-bt_config.bak \
  --output /private/new-pairing-preparation

python3 tests/test-remote-bond.py
```

Inputs must come from the **same owner's TV**. Matching an address is a consistency
check, not hardware identity attestation. The outputs contain Bluetooth pairing
keys and must stay private. The CLI never writes the input files or contacts a TV.
It retains before-copies of both Google files and emits both prepared files with
local readback and hashes.

## Device procedure

The original device sequence was:

1. Confirm working HyperOS pairing and record its original configuration.
2. In Google, stop the remote voice/reconnect helpers, disable Bluetooth and
   confirm that its process has stopped before taking final configuration copies.
3. Prepare the two Google files using the original HyperOS configuration.
4. Verify the same boot/device identity and unchanged source, install only the
   two Google files while preserving their owner, mode and SELinux label, sync,
   then read them back.
5. Restart Bluetooth and the helpers. Check the listed bond, physical direction
   keys and held-key voice. Switch back to HyperOS and check direction keys
   without another pairing.

Keep independent network/serial access throughout; stopping Bluetooth disconnects
the remote. On failure, stop Bluetooth again and restore the backed-up Google
files before restarting it. A partial write or power failure is not covered by
the offline merge.

The reference TV passed direction keys in both systems and Google voice after
this migration. Do not copy historical reverse-sync experiments or another TV's
configuration.

## First-install device tool

`tools/install-first-remote.py` implements the file transaction in the
[bounded setup runtime](first-install.md). It refuses the daily runtime, active
voice/reconnect helpers, another device, an expired setup window, or leftover
files from an earlier pairing operation. Keep Ethernet/ADB and serial recovery
available; the remote disconnects while Bluetooth is stopped.

```sh
python3 tools/install-first-remote.py inspect \
  --adb /path/to/adb --device TV_SETUP_ADB_SERIAL \
  --inputs /private/first-install-inputs.json \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --output /private/remote-inspect-01
```

Use `install` and a new output directory after inspection. It:

1. Disables Bluetooth, waits for OFF and stops the identified Bluetooth process.
2. Reads HyperOS's configuration through the original-data mount. That source
   is never written. Both Google originals and prepared files are saved privately.
3. Uploads both replacements exclusively, preserves owner/mode/SELinux labels,
   and checks their bytes before moving either original.
4. Retains each original inode at `bt_config.conf.freeguy-before-v1` or
   `bt_config.bak.freeguy-before-v1`, then commits the replacement and syncs.
5. Re-enables Bluetooth and checks that the imported remote is listed.

No firmware, boot configuration, default-system preference or HyperOS pairing
is changed. Direction keys and voice still need physical acceptance after
transitioning to the daily runtime; a listed bond alone is not that evidence.

On failure, keep the private output directory. The tool does not automatically
retry, delete files or restart Bluetooth over an incomplete pair. In the same
setup boot, use `restore` with the same build/device arguments, a new output
directory and these additional arguments:

```sh
--install-record /private/remote-install-01/journal.json \
--install-record-sha256 VERIFIED_FAILED_INSTALL_JOURNAL_SHA256
```

Restoration requires the original local snapshots and verifies every retained
inode and affected primary file before replacing anything. A changed bond,
different boot or uncertain prior operation causes refusal. The rollback is
for incomplete transactions, not for undoing a successful installation after
later Bluetooth activity. New and rejected files remain available for inspection;
there is no recursive deletion or automatic resume. If restoration itself is
interrupted, inspect that journal before another action.

The new installer and recovery path have synthetic mutation/checkpoint failure
tests. A read-only check on the reference TV confirmed that the setup guard
rejects its installed daily runtime. **The installer has not been applied to a
fresh TV.** These tests do not model physical power loss or prove remote reception.

```sh
python3 -B tests/test-install-first-remote.py
```
