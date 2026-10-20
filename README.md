# Freeguy Dual Boot

[简体中文](README.zh-CN.md)

Switch between the stock Xiaomi HyperOS system and a locally assembled Google TV system
on the Xiaomi L75MA-SPL (freeguy).

**Development status:** switching and remembered defaults passed ordinary-reboot
tests on one TV. Google starts through the stock system's startup stage. Both
systems retained their test data. The independently rebuilt system has started and returned to HyperOS.
Its watchdog has no one-hour operating limit. Remembered defaults passed
ordinary reboots in both directions. Physical cold-power testing remains pending.
There is no installation release yet.

The switch app remembers the selected system. HyperOS and Google TV keep separate
data, and normal switching does not recreate either data area. The early loader
removes its temporary boot parameters before entering Google. A runtime health
failure records a fault and returns to HyperOS; automatic entry then stays paused
until Google is explicitly selected again. This path has passed a live heartbeat-loss test.

## Before using this code

Read the [equipment and recovery requirements](docs/equipment.md).
Without a verified HDMI serial connection, an early-boot failure may require
paid repair or motherboard replacement. ADB and a spare slot are not substitutes.

Keep the original B system, verified backups and a working serial recovery
connection. Earlier experiments caused boot loops that required serial recovery.
A matching model name alone does not establish firmware compatibility.

The current Google environment uses permissive SELinux and privileged debugging.
The bootstrap fallback has unauthenticated USB debugging. OTA and DRM compatibility
are not promised.

## Source preview

The switch app, boot-state policy, checked environment and image-file
transactions, internal startup runtime, HDMI/CEC, remote voice and microphone adapters are published here.
Image builders are included with a [build map](BUILD.md). Supporting apps and full deployment
instructions are being imported. This checkout cannot yet install the complete TV system.

- `app/`: bilingual switch UI, launcher artwork and boot-state logic.
- `updater/src/`: [checked image-file replacement and rollback](docs/image-updater.md).
- `tools/build-switch-app.py`: local APK builder; [build guide](docs/switch-app.md).
- `tools/test-core.py`, `tests/`: offline tests with synthetic fixtures.
- `hdmi/`, `hdmi-label/`, `voice/`: [hardware adapters and build commands](docs/hardware.md).
- `runtime/`: [internal startup, rollback and component assembly](docs/runtime.md).
- [First installation](docs/first-install.md): temporary setup-root build and the remaining deployment sequence.
- Image builders: [official-input extraction and local assembly](docs/images.md).
- `adb-helper/`: [HyperOS debugging recovery](docs/adb-helper.md).
- [Shared remote pairing](docs/remote-pairing.md): offline preparation and device sequence.
- [Data and backups](docs/data.md): cold capture, local recovery preparation and fresh data creation.
- [Recovery](RECOVERY.md): bounded serial undo, physical configuration readback and verification limits.

```sh
python3 tools/test-core.py --java-home /path/to/jdk-17
python3 -B tests/test-profile.py
```

Read the [incident report](docs/incident.md) before planning a deployment.
Obtain the exact inputs from the [official firmware sources](docs/firmware.md).
Firmware, Google apps, device backups, pairing data and keys are not included.
Generated images, loaders and local profiles stay outside this repository.

## License

Original tooling: [Apache-2.0](LICENSE). Third-party components retain their own
terms. This project is independent of Xiaomi and Google.

Provided as is, without warranty. Use can cause data loss, boot loops or permanent
device damage. Contributors accept no liability to the extent permitted by
applicable law; see the [disclaimer](docs/equipment.md#disclaimer) and license.
