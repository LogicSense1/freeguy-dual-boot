#!/system/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Reproduce the original service's volatile MT7921 wake registration only.
# rclist_store was verified to update an in-memory buffer, not EMMC/firmware.
set -eu
umask 077
D="$1"
case "$D" in /data/local/tmp/codex-voice-device-*) ;; *) exit 2;; esac
[ "$(id -u)" = 0 ]
[ "$(getprop ro.codex.fileboot)" = 1 ]
[ "$(getprop ro.build.version.release)" = 14 ]
NODE=/sys/class/stpbtfwlog/stpbtfwlog/rclist
[ -r "$NODE" ] && [ -w "$NODE" ]
WAKE_RC=0
CLASSPATH="$D/wake.dex" timeout 15 app_process /system/bin RemoteWakeAddress >"$D/wake-address-private" 2>"$D/wake-error-private" || WAKE_RC=$?
if [ "$WAKE_RC" != 0 ]; then
 # A new data image legitimately has no bond until setup finishes. Distinguish
 # that one condition from malformed/multiple bonds or a broken Java runtime.
 if grep -qxF 'java.lang.IllegalStateException: Existing bond required' "$D/wake-error-private"; then
  rm -f "$D/wake-address-private" "$D/wake-error-private"
  exit 4
 fi
 exit "$WAKE_RC"
fi
ADDRESS=$(cat "$D/wake-address-private")
printf '%s\n' "$ADDRESS" | grep -Eq '^([0-9A-F]{2}:){5}[0-9A-F]{2}$'
[ "${#ADDRESS}" = 17 ]
CURRENT=$(cat "$NODE")
# Preserve unexpected/multiple existing registrations rather than replacing them.
case "$CURRENT" in ''|"$ADDRESS;") ;; *) echo 'WAKE_LIST_CONFLICT'; exit 3;; esac
# A shell printf may issue separate writes for the address and semicolon;
# sysfs treats EACH write as a complete new value. Stage a fixed 18-byte
# payload, then use one dd block, matching the original FileOutputStream write.
printf '%s;' "$ADDRESS" >"$D/wake-payload-private"
[ "$(wc -c <"$D/wake-payload-private")" = 18 ]
dd if="$D/wake-payload-private" of="$NODE" bs=18 count=1 2>"$D/wake-write-private"
[ "$(cat "$NODE")" = "$ADDRESS;" ]
rm -f "$D/wake-address-private" "$D/wake-error-private" "$D/wake-payload-private" "$D/wake-write-private"
echo 'VOLATILE_REMOTE_WAKE_REGISTERED existingBond=true persistentWrite=false'
