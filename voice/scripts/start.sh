#!/system/bin/sh
# SPDX-License-Identifier: Apache-2.0
# One lifecycle owner per RAM boot, using existing bonds only.
set -eu
S=/dev/codex-autovoice
[ "$(id -u)" = 0 ]
echo "STARTUP_PID=$$"
stamp() { read STAMP_UP STAMP_REST < /proc/uptime; echo "STARTUP_TIMING phase=$1 uptimeSeconds=$STAMP_UP"; }
stamp begin
read UP REST < /proc/uptime
DEADLINE=$(( ${UP%%.*} + 480 ))
while [ "$(getprop sys.boot_completed)" != 1 ] || ! pidof audioserver >/dev/null; do
 read UP REST < /proc/uptime
 [ "${UP%%.*}" -lt "$DEADLINE" ] || exit 3
 sleep 2
done
[ "$(getprop ro.codex.fileboot)" = 1 ]
[ "$(getprop ro.build.version.release)" = 14 ]
# The bootstrap observer keeps /linkerconfig/bootstrap. Join init's default
# runtime mount namespace, exactly as normal Android services do, before Java.
case "$#:$*" in 0:|1:runtime) ;; *) exit 2;; esac
if [ "$(readlink /proc/self/ns/mnt)" != "$(readlink /proc/1/ns/mnt)" ]; then
 [ "$#" = 0 ]
 exec nsenter --mount=/proc/1/ns/mnt /system/bin/sh "$S/start.sh" runtime
fi
echo 'ANDROID_RUNTIME_NAMESPACE_READY'
# The early observer also predates init's exported Java environment. Read only
# these known data fields; never source or evaluate init configuration as shell.
for ENVFILE in /init.environ.rc /data/system/environ/classpath; do
 [ -r "$ENVFILE" ]
 while read -r VERB KEY VALUE; do
  [ "$VERB" = export ] || continue
  case "$KEY" in
   ANDROID_BOOTLOGO|ANDROID_ROOT|ANDROID_ASSETS|ANDROID_DATA|ANDROID_STORAGE|ANDROID_ART_ROOT|ANDROID_I18N_ROOT|ANDROID_TZDATA_ROOT|EXTERNAL_STORAGE|ASEC_MOUNTPOINT|BOOTCLASSPATH|DEX2OATBOOTCLASSPATH|SYSTEMSERVERCLASSPATH|STANDALONE_SYSTEMSERVER_JARS)
    case "$VALUE" in /*|1) export "$KEY=$VALUE";; *) exit 5;; esac
    ;;
  esac
 done < "$ENVFILE"
done
[ -n "${BOOTCLASSPATH:-}" ] && [ "${ANDROID_DATA:-}" = /data ]
echo 'ANDROID_RUNTIME_ENVIRONMENT_READY'
stamp android-ready
# This lock is in tmpfs and disappears at reboot. Never steal an existing lock.
mkdir /dev/codex-autovoice-started || exit 2
read UP REST < /proc/uptime
[ "$(cat /dev/codex-daily-v1)" = shared-default-v1 ]
cd "$S"
sha256sum -c payload.sha256
# Refuse to overlap a manually launched adapter.
if ps -A -o ARGS | grep '[a]pp_process .*RemoteVoiceCaptureProbe google-' >/dev/null; then
 echo 'EXISTING_ADAPTER_REFUSED'; exit 4
fi
D=$(mktemp -d /data/local/tmp/codex-voice-device-boot-XXXXXX)
chmod 755 "$D"
for F in oem.apk libmibluetooth_jni.so classes.dex supervisor.sh reconnect.dex lifecycle.sh wake.dex register-wake.sh; do
 cp "$S/$F" "$D/$F"
 chmod 644 "$D/$F"
done
echo "RUNTIME_PATH=$D"
# Wake registration depends on the existing bond, NOT on an awake HID peer.
# Register before waiting: a sleeping remote must not disable power-key wake.
while :; do
 read UP REST < /proc/uptime
 [ "$(cat /dev/codex-daily-v1)" = shared-default-v1 ] || exit 3
 [ ! -e "$D/stop-request" ] || exit 0
 WAKE_RC=0
 /system/bin/sh "$D/register-wake.sh" "$D" || WAKE_RC=$?
 [ "$WAKE_RC" != 0 ] || break
 [ "$WAKE_RC" = 4 ] || exit "$WAKE_RC"
 echo 'WAITING_FOR_FIRST_REMOTE_BOND retry=true'
 sleep 5
done
stamp wake-registered
# A bonded remote can sleep through the first 45-second observation. Retain
# one owner and retry while awake, under the daily runtime watchdog.
# Never pair, remove a bond, steal the lock, or alter the system watchdog.
ATTEMPT=0
while :; do
 [ ! -e "$D/stop-request" ] || exit 0
 read UP REST < /proc/uptime
 [ "$(cat /dev/codex-daily-v1)" = shared-default-v1 ] || exit 3
 if ! dumpsys power | grep -q 'mWakefulness=Awake'; then sleep 3; continue; fi
 ATTEMPT=$((ATTEMPT+1))
 echo "REMOTE_CONNECT_ATTEMPT=$ATTEMPT"
 CONNECT_RC=0
 CLASSPATH="$D/reconnect.dex" timeout 55 app_process /system/bin RemoteReconnectProbe ready-exit > "$D/reconnect.log" 2>&1 || CONNECT_RC=$?
 cat "$D/reconnect.log"
 if [ "$CONNECT_RC" = 0 ] &&
    grep -q '^existingBond=true$' "$D/reconnect.log" &&
    grep -q '^hidAfter=2$' "$D/reconnect.log" &&
    grep -q '^bondStateAfter=12$' "$D/reconnect.log"; then break; fi
 echo "REMOTE_NOT_READY retry=true exitCode=$CONNECT_RC"
 sleep 5
done
echo 'EXISTING_REMOTE_CONNECTED'
stamp remote-ready
stamp voice-start
exec /system/bin/sh "$D/lifecycle.sh" "$D"
