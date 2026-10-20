# ADB recovery helper

[简体中文](adb-helper.zh-CN.md)

The stock HyperOS service can turn off ADB after startup. `local.codex.tvadb`
restores the existing `Settings.Global.ADB_ENABLED` setting after the owner
enables this helper. It does not replace the system's ADB authentication or
select a network port. It requests no Internet permission.

The helper cannot recover a bootloader/kernel hang or power on a stopped TV.
It supplements the [serial recovery path](equipment.md); it does not replace it.

## Lifecycle

The helper stores its enabled preference in device-protected storage. It uses
boot/wake broadcasts, a foreground service and a system-bound accessibility
service to observe the ADB setting. A change is followed by a delayed restore;
a ten-second check and scheduled jobs provide additional retries.

The accessibility service requests no UI events, window contents or gestures.
It is used for process lifecycle and settings observation. A device owner still
needs to enable that service and grant `WRITE_SECURE_SETTINGS` through an
authorized setup path. The app cannot grant that permission to itself.

Opening the helper enables automatic recovery. **Turn off automatic recovery**
disables the preference, cancels its jobs and stops its foreground service.
The accessibility service disables itself on its next check. Turning this
feature off does not turn off ADB itself.

To enable it again, reopen the app and re-enable its accessibility service.
Preserve every other enabled accessibility service. Do not replace the entire
settings list with this package name.

## Build

```sh
python3 tools/build-adb-helper.py \
  --android-jar /path/to/android.jar \
  --aapt2 /path/to/aapt2 \
  --d8-jar /path/to/r8.jar \
  --apksigner-jar /path/to/apksigner.jar \
  --java-home /path/to/jdk-17 \
  --keystore /private/signing.p12 \
  --keystore-password-file /private/signing-password.txt \
  --output /private/new-adb-helper-build

python3 tests/test-adb-helper.py
```

The builder checks permissions and accessibility metadata, compiles both
languages, signs with the supplied local key and verifies the APK signature.
It neither generates a key nor contacts a television. Keep the signing key for
updates; an APK signed by another key cannot replace an installed version.

Version 4 has simplified English/Chinese text. Its restore, boot-receiver and
accessibility lifecycle code is retained from the version used on the reference
TV. The public build has passed compilation, signature and configuration tests;
the version 4 APK has not been installed or reboot-tested on the TV.

## Deployment and removal

Installation alone is insufficient. The deployment flow must record the current
accessibility services, idle allowlist and background restrictions, grant the
required permission, then enable only this service. The complete checked
provisioning tool is not yet published.

Version 2 experimented with editing an OEM startup-policy file; that approach
was replaced by the system-bound service after the OEM rewrote its policy.
Do not reproduce those policy-file edits.

For removal, turn off the feature in the app, remove only its accessibility
component (`local.codex.tvadb/.ConnectionService`), uninstall this package and
remove its own idle-allowlist entry if one was added. Preserve other services and
settings. Verify recovery during the next planned system-switch test; a separate
reboot solely for this helper is unnecessary.
