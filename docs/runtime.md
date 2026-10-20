# Internal startup

[简体中文](runtime.zh-CN.md)

This is the implementation used by the reference TV. It is not an installation
procedure for another device. Read the [recovery requirements](equipment.md) and
[incident report](incident.md) before changing startup state.

## Boot path

The original B boot chain initializes the hardware. An early loader reconstructs
a RAM filesystem and removes its temporary startup arguments from both
environment copies before handing over to the dispatcher.

The dispatcher checks the remembered system and fault state. HyperOS follows the
original path. Google uses locally assembled image files and a separate data
area on internal storage. Switching systems does not initialize those data files.

The loader **writes startup configuration**. It is not a read-only RAM test.
Its partition layout, offsets and expected arguments are specific to the
reference device. The original B boot images are retained; the faulty old A slot
is not the fallback.

| Source | Role |
| --- | --- |
| `runtime/loader.c`, `internal-env-*.h`, `internal-env-transform.c` | Reconstruct the early filesystem; check, restore and read back both environment files. |
| `runtime/fileboot-entry.sh`, `mount-overlays.sh` | Mount image files, prepare the Google environment and start its init. |
| `runtime/fileboot-helper.c`, `watchdog-policy.h` | Dispatch, namespace operations and startup/runtime health checks. |
| `runtime/runtime-policy.sh.in`, `runtime-policy-start.sh` | Start the hardware adapters and policy services. |

The daily watchdog allows 600 seconds for startup and 120 seconds without a
health heartbeat. It has **no one-hour operating limit**. A separate, bounded
bootstrap guard is stopped after the daily watchdog takes over. A detected
runtime failure records a fault and returns to HyperOS; automatic Google entry
stays paused until the user selects it again.

These checks cannot recover every early failure. A kernel hang, failed
environment write or unavailable bootloader can still require serial intervention
or repair. The bootstrap fallback also enables unauthenticated USB debugging;
keep physical access and the local network trusted.

## Build stages

All generated files, profiles and firmware inputs belong outside this repository.
Use new output directories and retain the previous working files.

1. Build the helper with `tools/build-helper.py`.
2. `tools/build-bootstrap-root.py` extracts the exact supported original B boot
   ramdisk and applies checked local init changes. It refuses a different boot
   image hash.
3. Build the [hardware adapters](hardware.md), board overlays and policy.
4. `tools/assemble-runtime-root.py` combines the bootstrap root, adapters,
   overlays, private configuration and image hashes.
5. `tools/build-loader.py` embeds that assembled ramdisk in the ARM loader.

`tools/assemble-runtime-components.py` can update components in an existing
assembled root. It is also used by the complete root assembler.

The remaining image builders, device-specific profile preparation, initial
privileged installation and pairing-data provisioning are not yet a complete
published workflow. Do not substitute a previous experiment or guessed boot
command for a missing step.

The helper and final loader can be built with Clang and an ARM-capable LLVM
linker:

```sh
python3 tools/build-helper.py \
  --clang /path/to/clang --ld-lld /path/to/ld.lld \
  --output /private/build/helper

python3 tools/build-loader.py \
  --ramdisk /private/build/runtime/ramdisk.cpio.gz \
  --ramdisk-sha256 YOUR_VERIFIED_SHA256 \
  --clang /path/to/clang --ld-lld /path/to/ld.lld \
  --output /private/build/loader
```

The alternative `--docker-linker` option requires an already available local
image containing `ld.lld-18`; it does not download an image. The generated loader
contains firmware and local configuration. Do not publish it.

## Offline checks

The ARM audits require Python `unicorn` and `pyelftools`. They emulate syscalls,
clock changes and failures; they do not access a television.

```sh
python3 tests/test-archive.py
python3 tests/test-bootstrap-root.py
python3 tests/test-component-integration.py
python3 tests/test-runtime-root.py
python3 tools/audit-helper.py \
  --helper /private/build/helper/helper \
  --report /private/build/helper/audit.json
python3 tools/audit-loader.py --build /private/build/loader
```

On 2026-10-20, a fresh source build reproduced the deployed helper and loader
byte for byte. The loader audit passed 64 cases and checked 1,005 embedded
entries; the helper audit passed 21 cases. This verifies the published source
against that local build, not compatibility with another television. Keep
physical cold-power testing separate from ordinary reboot results.
