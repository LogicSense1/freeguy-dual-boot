#!/system/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Apply component configuration before reporting this boot healthy.
set -eu
umask 077
[ "$(id -u)" = 0 ]
[ "$(getprop ro.codex.fileboot)" = 1 ]
[ "$(getprop ro.build.version.release)" = 14 ]
[ "$(cat /dev/codex-daily-v1)" = shared-default-v1 ]
rm -f /dev/codex-runtime-policy-ready /dev/codex-runtime-policy-ready.new
for N in $(seq 1 240); do
    [ "$(getprop sys.boot_completed)" != 1 ] || break
    sleep 1
done
[ "$(getprop sys.boot_completed)" = 1 ]
timeout -s TERM 90 /system/bin/sh /dev/codex-runtime-policy.sh
printf 'ready\n' > /dev/codex-runtime-policy-ready.new
chmod 644 /dev/codex-runtime-policy-ready.new
mv /dev/codex-runtime-policy-ready.new /dev/codex-runtime-policy-ready
echo RUNTIME_POLICY_READY
