# Switch app

[简体中文](switch-app.zh-CN.md)

The app has two builds: `stock` keeps the HyperOS launcher artwork; `google`
uses a circular fallback icon and an adaptive icon. Both use the same television
banner and follow the system language: English by default, Chinese for `zh`.

The app is one part of an existing installation. Installing the APK alone does
not install Google TV, provision the shared state directory, or establish a
recovery path.

## Build

Use Python 3, JDK 17, Android SDK build tools (`aapt2`, `apksigner`, D8/R8),
and an Android platform `android.jar`. Supply your own signing key and a private
password file with mode `0600`. Keep the output and profile outside this repo.

```sh
python3 tools/build-switch-app.py \
  --profile /private/profile.json \
  --java-home /path/to/jdk-17 \
  --android-jar /path/to/android.jar \
  --aapt2 /path/to/aapt2 \
  --d8-jar /path/to/r8.jar \
  --apksigner-jar /path/to/apksigner.jar \
  --keystore /private/signing.p12 \
  --keystore-password-file /private/signing-password.txt \
  --version-code 5 \
  --daily-default \
  --launcher-style google \
  --output /private/new-google-app-build
```

Run again with `--launcher-style stock` and a different output directory for
the HyperOS installation. The builder refuses an existing output directory.
Use the same signing key for later updates. This command does not connect to
or change a TV.

## Local profile

The builder accepts only L75MA-SPL / `freeguy` with bootloader `01.01.250225`.
These identifiers are necessary checks, not a complete compatibility test.
The profile contains:

- `model`, `device`, `bootloader`.
- `immutableFiles`: SHA-256 values for the 14 exact runtime paths listed by
  `EXPECTED` in the builder. The loader and both external voice files are included.
- `normalizedBootargsSha256`: one to four locally verified boot configuration
  hashes. Only the validated boot-reason and brightness fields are normalized.
- `serialUndoVerified`: a record of an actual successful console undo.
- `dailyRuntime`: matching loader evidence for persistent defaults. The builder
  accepts a recorded runtime check or the explicitly defined binary clock model
  plus live helper/mode checks.

The profile records evidence; setting a boolean does not perform a test.
The synthetic profile in `tests/test-profile.py` is for host tests only.
Never use its placeholder hashes on a device.

## Runtime checks

Before entering Google, the worker checks stock B, verified-boot state, the
bootloader version, the proxy paths and file metadata, both environment CRCs,
the complete immutable file set, and command-line space. It saves private
snapshots, writes the backup environment before the primary, and reads back
both. An unrecognized state stops the operation.

The persistent preference is separate from the temporary boot parameters.
Automatic startup is dispatched once per stock boot. Google acknowledges a
stable health lease; an incomplete attempt pauses automatic entry. Returning
to HyperOS writes that preference before rebooting.

On this stock firmware, automatic dispatch also uses a system-bound
accessibility service. It requests no UI events, window content, or gestures.
Provisioning must preserve any existing accessibility services. That
first-install configuration is described below.

These checks do not make a two-file update atomic under arbitrary power loss.
The original B boot chain and a verified serial recovery route remain required.

## First-install startup binding

After building both APK variants, install the stock variant into HyperOS with
the owner's explicit ADB selector:

```sh
adb -s TV_STOCK_ADB_SERIAL install /private/new-stock-app-build/switch.apk
```

This command intentionally omits replacement and downgrade flags. If the
package already exists, inspect its signature, version and current installation
before changing it. Do not uninstall it or clear its data as an install retry.
The Google variant is installed with the same command and its own APK path
after entering temporary Google; it uses that system's separate package data.
The Android package manager must accept each signature. Retain the signing key.

Before staging the Google system, configure the stock app:

```sh
python3 tools/provision-switch-startup.py inspect \
  --adb /path/to/adb --device TV_STOCK_ADB_SERIAL \
  --apk /private/new-stock-app-build/switch.apk \
  --apk-sha256 VERIFIED_STOCK_APK_SHA256 \
  --build-result /private/new-stock-app-build/build-result.json \
  --build-result-sha256 VERIFIED_BUILD_RESULT_SHA256 \
  --known-device-binding INDEPENDENTLY_RECORDED_OWNER_DEVICE_BINDING_SHA256 \
  --known-device-binding-scheme freeguy-emmc-cid-serial-v1 \
  --output /private/switch-startup-inspect-01
```

Use `enable` and a new journal directory after reviewing the inspection.
It requires the matching installed APK, original B, ordinary stock ADB and no
shared default directory. It opens the app to clear Android's stopped-package
state, allows its background work and adds its startup service without removing
other accessibility services. The unfinished installation may show the app's
unavailable message; that is not a failed system switch.

It does not grant new permissions, create the shared directory, write startup
arguments or reboot. Missing shared defaults prevent the app from dispatching
Google at this stage. Create defaults only through the later
[daily finalization](first-install.md#commit-the-daily-files).

Changes are checked and journaled individually. An interruption or outside
settings change stops the tool without retrying or overwriting the changed
settings. Completion verifies configuration, not that Android has bound the
service or that a later boot will switch. This first-install helper has offline
tests; it has not been applied to a fresh TV.

```sh
python3 -B tests/test-provision-switch-startup.py
```

## Naming and compatibility

The stock OS is **Xiaomi HyperOS** (Chinese: **小米澎湃OS**). The tested
stock build is `OS2.0.5.0.RFTMATV`, based on Android 11. The TV brand remains
Xiaomi; the donor environment is Google TV.

`XIAOMI` in the v1 state file, `stock` in build options and existing action/file
names are stable compatibility identifiers. They still refer to HyperOS.
Renaming display strings does not migrate or reset either system's data.

Official naming: [Xiaomi HyperOS / 小米澎湃OS](https://os1.hyperos.mi.com/).
