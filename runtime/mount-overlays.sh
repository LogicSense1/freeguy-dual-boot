#!/system/bin/sh
# SPDX-License-Identifier: Apache-2.0
/codex/helper bind-file /codex/overlays/mik-logo-detection.ko "$G/vendor/lib/modules/mik.ko"
MOUNTS="$G/vendor/lib/modules/mik.ko $MOUNTS"
/codex/helper bind-file /codex/overlays/vendor-build.prop "$G/vendor/build.prop"
MOUNTS="$G/vendor/build.prop $MOUNTS"
/codex/helper bind-file /codex/overlays/dtv_svc.sh "$G/vendor/bin/dtv_svc.sh"
MOUNTS="$G/vendor/bin/dtv_svc.sh $MOUNTS"
/codex/helper bind-file /codex/overlays/audio_policy_configuration.xml "$G/vendor/etc/audio_policy_configuration.xml"
MOUNTS="$G/vendor/etc/audio_policy_configuration.xml $MOUNTS"
/codex/helper bind-file /codex/overlays/boot-control.xml "$G/vendor/etc/vintf/manifest/android.hardware.boot@1.1.xml"
MOUNTS="$G/vendor/etc/vintf/manifest/android.hardware.boot@1.1.xml $MOUNTS"
/codex/helper bind-file /codex/overlays/init.mt9653.rc "$G/vendor/etc/init/hw/init.mt9653.rc"
MOUNTS="$G/vendor/etc/init/hw/init.mt9653.rc $MOUNTS"
/codex/helper bind-file /codex/overlays/boringssl_self_test.rc "$G/vendor/etc/init/boringssl_self_test.rc"
MOUNTS="$G/vendor/etc/init/boringssl_self_test.rc $MOUNTS"
/codex/helper bind-file /codex/overlays/fstab.mt9653 "$G/vendor/etc/fstab.mt9653"
MOUNTS="$G/vendor/etc/fstab.mt9653 $MOUNTS"
/codex/helper bind-file /codex/overlays/native-init.rc "$G/system/etc/init/hw/init.rc"
MOUNTS="$G/system/etc/init/hw/init.rc $MOUNTS"
/codex/helper bind-file /codex/overlays/CaptureRecorderServer.rc "$G/system_ext/etc/init/CaptureRecorderServer.rc"
MOUNTS="$G/system_ext/etc/init/CaptureRecorderServer.rc $MOUNTS"
/codex/helper bind-file /codex/overlays/remote-32b8.kl "$G/vendor/usr/keylayout/Vendor_2717_Product_32b8.kl"
MOUNTS="$G/vendor/usr/keylayout/Vendor_2717_Product_32b8.kl $MOUNTS"

# Restore original remote audio HAL; retain every existing system HAL.
/codex/helper bind-file /codex/overlays/remote-audio-hw "$G/system/lib/hw" || exit 1
MOUNTS="$G/system/lib/hw $MOUNTS"

# Shadow only package/config directories in RAM, retaining every original entry.
# Recursive binds preserve any earlier file overlays inside these directories.
codex_track_mount_tree() {
    for mounted_path in $(awk -v base="$1" '$2 == base || index($2, base "/") == 1 {print $2}' /proc/mounts | sort); do
        MOUNTS="$mounted_path $MOUNTS"
    done
}
codex_shadow_directory() {
    origin="$1"
    shadow="$2"
    test -d "$origin"
    test -d "$shadow"
    for entry in "$origin"/*; do
        test -e "$entry" || test -L "$entry" || continue
        name=${entry##*/}
        test ! -e "$shadow/$name" && test ! -L "$shadow/$name"
        if test -L "$entry"; then
            cp -a "$entry" "$shadow/$name"
        elif test -d "$entry"; then
            mkdir "$shadow/$name"
            /codex/helper bind-recursive "$entry" "$shadow/$name"
            codex_track_mount_tree "$shadow/$name"
        elif test -f "$entry"; then
            touch "$shadow/$name"
            /codex/helper bind-file "$entry" "$shadow/$name"
            MOUNTS="$shadow/$name $MOUNTS"
        else
            echo "Unsupported original directory entry: $entry"
            exit 1
        fi
    done
    /codex/helper bind-recursive "$shadow" "$origin"
    codex_track_mount_tree "$origin"
}
codex_shadow_directory "$G/system/priv-app" /codex/overlays/hdmi-priv-app
codex_shadow_directory "$G/system/etc/permissions" /codex/overlays/hdmi-permissions
codex_shadow_directory "$G/system/etc" /codex/overlays/hdmi-etc

# OEM remote audio HAL and exact validated dependency closure, RAM only.
codex_shadow_directory "$G/vendor/lib/hw" /codex/overlays/remote-vendor-audio-hw

# Builtin mic compatibility over the retained original primary HAL, RAM only.
/codex/helper bind-file /codex/overlays/builtin-mic-primary.so "$G/vendor/lib/hw/audio.primary.mt5879.so"
MOUNTS="$G/vendor/lib/hw/audio.primary.mt5879.so $MOUNTS"
