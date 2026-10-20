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
this migration. The public preparer has passed synthetic tests; its new CLI has
not performed a live installation. A checked device-side installer is still
needed. Do not copy the historical experimental reverse-sync scripts or another
TV's configuration as a shortcut.
