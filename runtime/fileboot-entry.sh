#!/system/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Executed as PID 1 after the original init SELinux setup, only in RAM.
export PATH=/system/bin:/system/xbin
umask 077
G=/fileboot-root
H=/fileboot-host
I=/images
UEVENT_PID=
WATCHDOG_PID=
MOUNTS=
LOOPS=
fail() {
    trap - EXIT
    set +e
    echo "CODEX_FILEBOOT_ABORT: $*" > /dev/kmsg
    echo "CODEX_FILEBOOT_ABORT: $*"
    test -z "$UEVENT_PID" || kill "$UEVENT_PID" 2>/dev/null
    for p in $MOUNTS; do umount "$p" 2>/dev/null; done
    for p in $LOOPS; do losetup -d "$p" 2>/dev/null; done
    sync
    # The original backup recovery has USB ADB and a bounded stock return.
    exec /system/bin/stock-init second_stage
}
trap 'fail unexpected-exit' EXIT
set -e
test "$$" = 1
echo '<6>CODEX_FILEBOOT: bootstrap started' > /dev/kmsg
echo on > /proc/sys/kernel/printk_devkmsg || true
/system/bin/ueventd &
UEVENT_PID=$!
for t in $(seq 1 20); do test -b /dev/block/mmcblk0p42 && break; sleep 1; done
test "$(cat /sys/block/mmcblk0/mmcblk0p42/start)" = 9277440
test "$(cat /sys/block/mmcblk0/mmcblk0p42/size)" = 112865240
mkdir -p "$H" "$I" "$G"
mount -t f2fs -o rw,noatime,nosuid,nodev /dev/block/mmcblk0p42 "$H"
MOUNTS="$H $MOUNTS"
STAGE="$H/local/tmp/codex-gtv-fileboot-v1"
test -d "$STAGE"
test "$(cat "$STAGE/marker")" = codex-freeguy-fileboot-v1
mount --bind "$STAGE" "$I"
MOUNTS="$I $MOUNTS"
LOG=$(mktemp -d "$I/log-XXXXXX")
chown 0:2000 "$LOG"
chmod 2750 "$LOG"
umask 027
exec >> "$LOG/bootstrap.txt" 2>&1
set -x
/codex/trial-guard watchdog > /dev/null 2> "$LOG/transition-guard.txt" &
TRANSITION_GUARD_PID=$!
kill -0 "$TRANSITION_GUARD_PID"
/codex/helper watchdog-daily > /dev/null 2> "$LOG/watchdog.txt" &
WATCHDOG_PID=$!
echo "$WATCHDOG_PID" > /dev/codex-watchdog.pid
for n in $(seq 1 10); do test -f /dev/codex-daily-watchdog-ready && break; kill -0 "$WATCHDOG_PID"; sleep 1; done
test "$(cat /dev/codex-daily-watchdog-ready)" = ready
kill -0 "$WATCHDOG_PID"
kill "$TRANSITION_GUARD_PID"
wait "$TRANSITION_GUARD_PID" || true
/codex/helper kernel-log > "$LOG/kernel.txt" 2> "$LOG/kernel-logger.txt" &
# Keep each firmware partition read-only. F2FS sometimes submits userdata
# in-place writes against the parent disk, so marking that parent read-only
# causes failed backing-file writes (observed in full19/20). Both exposed raw
# disk and userdata nodes are masked before Android starts, below.
test "$(blockdev --getro /dev/block/mmcblk0)" = 0
for node in /dev/block/mmcblk0p* /dev/block/mmcblk0boot*; do
    test -b "$node" || continue
    test "$node" != /dev/block/mmcblk0p42 || continue
    blockdev --setro "$node"
    test "$(blockdev --getro "$node")" = 1
done
test "$(blockdev --getro /dev/block/mmcblk0p42)" = 0
echo codex-fileboot-write-check > "$LOG/io-check"
fsync "$LOG/io-check"
test "$(cat "$LOG/io-check")" = codex-fileboot-write-check
cd "$I"
sha256sum -c /codex/immutable.sha256
# Only the helper and mount map embedded in the RAM image are executable here.
test "$(cat /sys/module/loop/parameters/max_part)" = 7
for n in $(seq 0 63); do
    test -b /dev/block/loop$n || mknod /dev/block/loop$n b 7 $((n * 8))
done
NEXT_LOOP=0
image_mount() {
    file=$1;type=$2;target=$3;mode=$4
    test -f "$I/$file" && test ! -L "$I/$file"
    loop=/dev/block/loop$NEXT_LOOP
    NEXT_LOOP=$((NEXT_LOOP+1))
    LOOPS="$loop $LOOPS"
    if test "$mode" = ro; then losetup -r "$loop" "$I/$file"; else losetup "$loop" "$I/$file"; fi
    mkdir -p "$target"
    options=$mode,noatime
    if test "$type:$mode" = ext4:ro; then options=$options,noload; fi
    mount -t "$type" -o "$options" "$loop" "$target"
    MOUNTS="$target $MOUNTS"
}
image_mount system.img erofs "$G" ro
mount -t tmpfs -o mode=0755 tmpfs "$G/mnt";MOUNTS="$G/mnt $MOUNTS"
mount -t tmpfs -o mode=0755 tmpfs "$G/perm";MOUNTS="$G/perm $MOUNTS"
mount -t tmpfs -o mode=0755 tmpfs "$G/apex";MOUNTS="$G/apex $MOUNTS"
mount -t tmpfs -o mode=0755 tmpfs "$G/linkerconfig";MOUNTS="$G/linkerconfig $MOUNTS"
# The generated map contains only fixed paths from the checked bundle manifest.
. /codex/mount-images.sh
SHARED="$H/local/codex-system-default-v1"
test ! -L "$SHARED" && test -d "$SHARED"
test "$(stat -c '%u %g %a' "$SHARED")" = '1000 1000 700'
for F in state fault; do test ! -L "$SHARED/$F" && test -f "$SHARED/$F"; done
test "$(cat "$SHARED/fault")" = OK
test ! -L "$G/data/local" && test ! -L "$G/data/local/codex-system-default-v1"
mkdir -p "$G/data/local/codex-system-default-v1"
mount --bind "$SHARED" "$G/data/local/codex-system-default-v1"
MOUNTS="$G/data/local/codex-system-default-v1 $MOUNTS"
echo shared-default-v1 > /dev/codex-daily-v1
chmod 644 /dev/codex-daily-v1
cp /codex/helper /dev/codex-helper-daily
chmod 755 /dev/codex-helper-daily
# Restore only the owner's authorized ADB public key; keep other keys.
test ! -L "$G/data/misc/adb"
mkdir -p "$G/data/misc/adb"
test ! -L "$G/data/misc/adb/adb_keys"
touch "$G/data/misc/adb/adb_keys"
if ! grep -qxF -f /codex/host-adb-key "$G/data/misc/adb/adb_keys"; then
    if test -s "$G/data/misc/adb/adb_keys" && test -n "$(tail -c 1 "$G/data/misc/adb/adb_keys")"; then
        echo >> "$G/data/misc/adb/adb_keys"
    fi
    cat /codex/host-adb-key >> "$G/data/misc/adb/adb_keys"
fi
chown 1000:2000 "$G/data/misc/adb" "$G/data/misc/adb/adb_keys"
chmod 2750 "$G/data/misc/adb"
chmod 640 "$G/data/misc/adb/adb_keys"
fsync "$G/data/misc/adb/adb_keys"
. /codex/mount-overlays.sh
# Board vendor drivers are retained, but its domestic UI APKs carry a different
# platform signature and cannot share Android 14's system UID.
for p in app priv-app; do
    mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$G/vendor/$p"
    MOUNTS="$G/vendor/$p $MOUNTS"
done
# The synthetic parent is not an image root and inherited the log umask.
# Stock media/system HALs must be able to traverse it to see DTV readiness.
chmod 755 "$G/mnt/vendor"
# Donor-specific services require absent board libraries/tuners and crash-loop.
# Hide only their APK directories in RAM; retain Google setup and launcher.
for p in /system/app/overseaService /system_ext/priv-app/Atsc3TvInputService; do
    test -d "$G$p"
    mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs "$G$p"
    MOUNTS="$G$p $MOUNTS"
done
# Hide raw access to the whole disk and original userdata behind an already
# read-only loop node. Firmware partition devices themselves remain read-only.
# The original filesystem retains its open device, serving only backing files.
test "$(blockdev --getro /dev/block/loop1)" = 1
for node in /dev/block/mmcblk0 /dev/block/mmcblk0p42; do
    /codex/helper bind-readonly /dev/block/loop1 "$node"
    MOUNTS="$node $MOUNTS"
    test "$(blockdev --getro "$node")" = 1
done
for p in dev proc sys; do
    /codex/helper bind-recursive /$p "$G/$p"
    MOUNTS="$G/$p $MOUNTS"
done
# The stock mount utility silently omitted nested mounts with -o bind,rec.
# Native libselinux needs selinuxfs, and linkerconfig's logwrapper needs devpts.
test -f "$G/sys/fs/selinux/enforce"
test -f "$G/sys/fs/selinux/policy"
test -c "$G/dev/pts/ptmx"
grep -q " $G/sys/fs/selinux selinuxfs " /proc/mounts
grep -q " $G/dev/pts devpts " /proc/mounts
# Configfs will be mounted by native Android init; preserve any existing instance.
if grep -q ' /config ' /proc/mounts; then
    /codex/helper bind-recursive /config "$G/config";MOUNTS="$G/config $MOUNTS"
fi
mkdir -p "$G/data/local/tmp/codex-fileboot-logs"
mount --bind "$LOG" "$G/data/local/tmp/codex-fileboot-logs"
MOUNTS="$G/data/local/tmp/codex-fileboot-logs $MOUNTS"
# Validate init itself using the pre-APEX bootstrap library search paths.
chroot "$G" /system/bin/bootstrap/linker --list /system/bin/init > "$LOG/init-libraries.txt" 2>&1
fsync "$LOG/init-libraries.txt"
# Load the already compiled cross-version policy without enforcing it yet.
/codex/helper policy /codex/candidate-policy
test "$(cat /sys/fs/selinux/enforce)" = 0
# Original mboot initializes the panel; obsolete PWM experiment disabled.
mkdir -p /dev/codex-autovoice
chmod 755 /dev/codex-autovoice
cp /codex/autovoice/* /dev/codex-autovoice/
test -f "$I/voice-assets-v1/oem.apk" && test ! -L "$I/voice-assets-v1/oem.apk"
cp "$I/voice-assets-v1/oem.apk" /dev/codex-autovoice/oem.apk
test -f "$I/voice-assets-v1/libmibluetooth_jni.so" && test ! -L "$I/voice-assets-v1/libmibluetooth_jni.so"
cp "$I/voice-assets-v1/libmibluetooth_jni.so" /dev/codex-autovoice/libmibluetooth_jni.so
chmod 644 /dev/codex-autovoice/*
(cd /dev/codex-autovoice && sha256sum -c payload.sha256)
/codex/helper observe "$G" /system/bin/sh -c '
    /system/bin/sh /dev/codex-autovoice/start.sh > /data/local/tmp/codex-fileboot-logs/remote-voice-startup.txt 2>&1 &
    (
        while true; do
            if [ "$(getprop sys.boot_completed)" = 1 ] &&
               pidof system_server >/dev/null && pidof surfaceflinger >/dev/null &&
               pidof audioserver >/dev/null && pidof app_man >/dev/null &&
               timeout 8 dumpsys window windows > /dev/codex-window-health &&
               grep -q WINDOW /dev/codex-window-health &&
               timeout 8 dumpsys media.audio_policy > /dev/codex-audio-health &&
               grep -q "Primary Output I/O handle: [1-9]" /dev/codex-audio-health; then
                /dev/codex-helper-daily monotonic-seconds > /dev/codex-health-heartbeat.tmp
                chmod 644 /dev/codex-health-heartbeat.tmp
                mv /dev/codex-health-heartbeat.tmp /dev/codex-health-heartbeat
            fi
            sleep 15
        done
    ) &
    echo $! > /dev/codex-health.pid
    logcat -b all -v threadtime -f /data/local/tmp/codex-fileboot-logs/logcat.txt -r 16384 -n 3 > /dev/null 2>&1 &
    getprop > /data/local/tmp/codex-fileboot-logs/properties.txt
    ls -ld /dev/__properties__ /dev/__properties__/properties_serial /dev/__properties__/property_info
    for SNAPSHOT_N in $(seq 1 10); do
        echo SNAPSHOT; cat /proc/uptime
        for prop in ro.property_service.version servicemanager.ready hwservicemanager.ready apexd.status sys.boot_completed init.svc.vold init.svc.keystore2 init.svc.adbd; do
            echo "$prop=$(getprop "$prop")"
        done
        ps -A
        for task in /proc/[0-9]*; do
            name=$(cat "$task/comm" 2>/dev/null)
            case "$name" in vold|vdc|keystore2|apexd)
                echo "$task $name"
                for thread in "$task"/task/*; do
                    echo "$thread"; cat "$thread/wchan" "$thread/stack" 2>/dev/null
                done;;
            esac
        done
        sleep 30
    done
' > "$LOG/observer.txt" 2>&1 &
kill "$UEVENT_PID"
wait "$UEVENT_PID" || true
UEVENT_PID=
cat /proc/mounts > "$LOG/mounts-before-handoff.txt"
echo '<6>CODEX_FILEBOOT: handing PID 1 to Android 14 init' > /dev/kmsg
echo HANDOFF_READY >> "$LOG/bootstrap.txt"
fsync "$LOG/bootstrap.txt"
sync
trap - EXIT
# Android normally clears umask in first-stage init. We enter second-stage
# directly; retaining the logging umask would hide property areas from services.
umask 000
exec /codex/helper handoff "$G"
