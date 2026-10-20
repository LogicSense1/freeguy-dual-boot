# Build map

[简体中文](docs/build.zh-CN.md)

Builds run locally and write new files. They do not install a system on a TV.
Keep firmware, profiles, signing keys, generated files and backups outside Git.
Read [equipment](docs/equipment.md), [the incident](docs/incident.md) and
[dependencies](DEPENDENCIES.md) first.

| Stage | Guide | Output |
| --- | --- | --- |
| Obtain exact OTA inputs | [Official sources](docs/firmware.md) | Original HyperOS and donor Google TV images |
| Build file-backed system images | [Image build](docs/images.md) | system, system_ext, vendor; policy and board/audio overlays |
| Build HDMI, CEC, voice and microphone adapters | [Hardware](docs/hardware.md) | Local APKs, DEX and audio adapter |
| Build internal startup | [Runtime](docs/runtime.md) | Helper, bootstrap root, runtime root and early loader |
| Build both switch apps | [Switch app](docs/switch-app.md) | English/Chinese UI, target-specific launcher icons |
| Build the HyperOS ADB helper | [ADB recovery](docs/adb-helper.md) | Owner-signed helper APK |
| Prepare writable data or back up existing data | [Data and backups](docs/data.md) | New local data files or a checked private backup |
| Build an updater for an existing installation | [Image updater](docs/image-updater.md) | A fixed four-file transaction DEX and plan |
| Prepare a first installation | [First installation](docs/first-install.md) | Bounded setup root, fixed file inventory and checked initial upload |

The order has dependencies: VNDK packaging precedes framework images; policy
compilation precedes the vendor image; final image and component hashes go into
the runtime profile. The final loader and image hashes go into the switch profile.
Use hashes from the local outputs, not a copied build report.

The runtime also needs board-specific backing files and separate writable data
images. Those must be prepared for the recipient TV. An OTA package does not
contain its calibration, pairing records or personal data. Existing data must
never be replaced by empty initialization images.

## Still missing from the published workflow

- General activation and checked deployment of same-device backing data.
  [Early owner capture](docs/owner-capture.md), cold backup, local expansion
  and fresh empty data creation are documented.
- Initial privileged installation, permissions and startup-service provisioning.
- Device-side pairing installation and checked ADB-helper provisioning
  ([offline pairing preparation](docs/remote-pairing.md) is available).
- A complete installation runbook and recipient-specific serial undo acceptance.
  The CLI's write path has passed a live reference-device test; see
  [Recovery](RECOVERY.md) for its scope and limits.

Component sources and checks are available; the checkout is not yet a complete
installer. Do not infer missing deployment commands from historical experiments.

## Verification

Run the tests in each guide. Firmware builders read back content, owners, modes,
security labels and links. ARM audits simulate failure paths. Neither proves
physical hardware behavior on another TV.

On the reference TV, ordinary reboot, both switch directions, remembered defaults
and data markers have passed using the independently rebuilt system. Current-build
physical cold-power testing remains unverified. The daily watchdog has no
one-hour operating cap; startup and heartbeat protection remain active.
