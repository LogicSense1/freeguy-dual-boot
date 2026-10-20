#!/system/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Daily reconnect with a consecutive-failure bound after a transport disconnect. No pairing,
# firmware writes, power locks, or Google process resets.
set -eu
D="$1"
case "$D" in /data/local/tmp/codex-voice-device-*) ;; *) exit 2;; esac
[ "$(id -u)" = 0 ]
[ "$(getprop ro.codex.fileboot)" = 1 ]
[ "$(getprop ro.build.version.release)" = 14 ]
mkdir "$D/lifecycle-lock" || exit 2
trap 'rmdir "$D/lifecycle-lock"' EXIT
N=0
FAST_FAILURES=0
while [ "$FAST_FAILURES" -lt 8 ]; do
 while :; do
  [ ! -e "$D/stop-request" ] || exit 0
  read UP REST < /proc/uptime
  [ "$(cat /dev/codex-daily-v1)" = shared-default-v1 ] || exit 3
  if dumpsys power | grep 'mWakefulness=Awake' >/dev/null; then break; fi
  sleep 3
 done
 if ps -A -o ARGS | grep '[a]pp_process .*RemoteVoiceCaptureProbe google-' >/dev/null; then
  echo 'EXISTING_ADAPTER_REFUSED'; exit 4
 fi
 N=$((N+1))
 SLOT=$((N % 4))
 START=$(/dev/codex-helper-daily monotonic-seconds)
 echo "VOICE_LIFECYCLE_SESSION=$N"
 /system/bin/sh "$D/supervisor.sh" "$D" >"$D/lifecycle-session-$SLOT.log" 2>&1
 cat "$D/lifecycle-session-$SLOT.log"
 [ ! -e "$D/stop-request" ] || exit 0
 # Only a Bluetooth transport loss authorizes another session. All other
 # failures remain visible and stop; no generic crash/restart loop.
 grep -q '^ENDING reason=connection-error$' "$D/lifecycle-session-$SLOT.log" || exit 0
 END=$(/dev/codex-helper-daily monotonic-seconds)
 if [ "$((END-START))" -ge 300 ]; then FAST_FAILURES=0; else FAST_FAILURES=$((FAST_FAILURES+1)); fi
 echo "VOICE_TRANSPORT_LOST waitingForAwake=true consecutiveShortFailures=$FAST_FAILURES"
 sleep 10
done
echo 'VOICE_RECONNECT_LIMIT_REACHED'
