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

Installation alone is insufficient. Verify the supported stock B system and
record the owner's device binding first. For a new installation, install the
locally built `adb-helper.apk` using the explicitly selected ADB endpoint.
Do not uninstall an existing helper or use an APK signed by an unrelated key
to force an update.

The provisioning tool requires the published version 4 to be installed already.
It matches the installed APK hash to the reviewed local build. Start with an
inspection:

```sh
python3 tools/provision-adb-helper.py inspect \
  --adb /path/to/adb --device TV_ADB_SERIAL_OR_ENDPOINT \
  --apk /private/new-adb-helper-build/adb-helper.apk \
  --apk-sha256 VERIFIED_HELPER_APK_SHA256 \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --output /private/helper-inspect-01
```

Use `--binding-source android-serial` only if that was the explicitly recorded
binding scheme. After reviewing the journal, use `enable` with a different new
output directory. Every check runs again.

The tool records and rechecks the accessibility list, permission, idle exemption
and two background app-ops. It grants only this helper's required permission,
allows its background operation and adds its idle exemption. It opens the helper
to enable its own preference **before** binding the accessibility service;
otherwise the service can immediately disable itself. Other selected services
remain in their original order.

It does not install APKs, edit OEM policy files, change startup parameters,
reboot, disable ADB authentication or select a debugging port.
Concurrent settings changes, a different boot or a failed operation stop the
run. Keep `journal.json`; a pending operation may already have taken effect.
Do not overwrite current settings with the entire saved snapshot or blindly
rerun an incomplete operation.

Seven offline tests cover service-list preservation, opt-in order, existing
configuration, owner/boot checks and interruption at every device-operation
boundary. The new provisioning CLI has not been applied to a fresh installation.
`complete: true` means configuration and activity launch passed; it does not
prove recovery after boot. Check that during the next planned system switch.

```sh
python3 -B tests/test-provision-adb-helper.py
```

Version 2 experimented with editing an OEM startup-policy file; that approach
was replaced by the system-bound service after the OEM rewrote its policy.
Do not reproduce those policy-file edits.

For removal, turn off the feature in the app, remove only its accessibility
component (`local.codex.tvadb/.ConnectionService`), uninstall this package and
remove its own idle-allowlist entry if one was added. Preserve other services and
settings. Verify recovery during the next planned system-switch test; a separate
reboot solely for this helper is unnecessary.
