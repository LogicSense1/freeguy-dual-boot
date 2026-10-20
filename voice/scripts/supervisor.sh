#!/system/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Daily physical-key operation. Never evaluates output as commands.
set -eu
D="$1"
case "$D" in /data/local/tmp/codex-voice-device-*) ;; *) exit 2;; esac
[ "$(id -u)" = 0 ]
[ "$(getprop ro.codex.fileboot)" = 1 ]
[ "$(getprop ro.build.version.release)" = 14 ]
[ "$(getprop sys.boot_completed)" = 1 ]
[ "$(cat /dev/codex-daily-v1)" = shared-default-v1 ]
export CLASSPATH="$D/oem.apk:$D/classes.dex"
echo "ON_DEVICE_SUPERVISOR_READY dailyWatchdog=true uid=$(id -u)"
app_process -Djava.library.path="$D" /system/bin com.mi.bluetooth.services.voice.mitvvoice.RemoteVoiceCaptureProbe google-daily 2>&1 |
while IFS= read -r LINE; do
 printf '%s\n' "$LINE"
 case "$LINE" in
  "PHYSICAL_VOICE_DOWN utterance="*)
   ID=${LINE#*utterance=}; ID=${ID%% *}
   case "$ID" in ''|*[!0-9]*) exit 2;; esac
   # Retain the warm assistant and original input device. Starting the public
   # ASSIST intent owns the new conversation; do not invalidate AudioRecord.
   ;;
  GOOGLE_ASSIST_REQUEST)
   echo 'ON_DEVICE_ASSIST_LAUNCH'
   if /system/bin/am start --user 0 -W -a android.intent.action.ASSIST -p com.google.android.katniss; then
    printf '%s\n' "$ID" >"$D/assistant-launched"
    chmod 644 "$D/assistant-launched"
   else echo 'ON_DEVICE_ASSIST_FAILED'; fi
   ;;
 esac
done
echo 'ON_DEVICE_SUPERVISOR_FINISHED'
