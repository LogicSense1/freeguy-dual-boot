# Freeguy Dual Boot

[简体中文](README.zh-CN.md)

Switch between Xiaomi's stock system and a locally assembled Google TV system
on the Xiaomi L75MA-SPL (freeguy).

**Development status:** switching and remembered defaults passed ordinary-reboot
tests on one TV. Google starts through the stock system's startup stage. Both
systems retained their test data. The independently rebuilt system has started and returned to Xiaomi.
Its watchdog has no one-hour operating limit. Remembered defaults passed
ordinary reboots in both directions. Physical cold-power testing remains pending.
There is no installation release yet.

The switch app remembers the selected system. Xiaomi and Google keep separate
data, and normal switching does not recreate either data area. The early loader
removes its temporary boot parameters before entering Google. A runtime health
failure records a fault and returns to Xiaomi; automatic entry then stays paused
until Google is explicitly selected again. This path has passed a live heartbeat-loss test.

## Before using this code

Keep the original B system, verified backups and a working serial recovery
connection. Earlier experiments caused boot loops that required serial recovery.
A matching model name alone does not establish firmware compatibility.

The current Google environment uses permissive SELinux and privileged debugging.
The bootstrap fallback has unauthenticated USB debugging. OTA and DRM compatibility
are not promised.

## Source preview

This first public snapshot contains the boot-state codec, default-system policy,
checked environment and image-file transactions, and their offline tests.
The switch UI, builders and hardware integration are still being prepared for
publication. This checkout cannot yet build or install the complete TV system.

- `app/src/`: boot-state and transaction logic.
- `updater/src/`: checked image-file replacement and rollback.
- `tests/`: synthetic fixtures; no device backups.
- `tools/test-core.py`: run the core tests with Python 3 and JDK 17.

```sh
python3 tools/test-core.py --java-home /path/to/jdk-17
```

These tests do not contact a TV. Passing them does not establish compatibility
with another device. Firmware, Google apps, device backups, pairing data and keys
are not included. Generated images, loaders and local profiles stay outside this
repository.

## License

Original tooling: [Apache-2.0](LICENSE). Third-party components retain their own
terms. This project is independent of Xiaomi and Google.
