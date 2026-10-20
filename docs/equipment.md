# Equipment and recovery limits

[简体中文](equipment.zh-CN.md)

## First installation

| Item | Purpose |
| --- | --- |
| A compatible L75MA-SPL / freeguy TV | The only board tested by this project. Verify firmware, boot chain and image hashes as well as the model. |
| A computer with enough free storage | Build images, retain original files and store verified backups outside the TV. The completed daily setup does not need the computer to boot. |
| A compatible Xiaomi/MTK HDMI-to-UART USB adapter, often sold as “HDMI to TTL” | Read early boot output, interrupt the bootloader and undo a supported startup change when Android cannot start. |
| Verified backups stored outside the TV | Keep the original boot configuration and required firmware/data backups available even when Android cannot start. Files stored only on the TV are not an independent backup. |
| Ethernet to a trusted local network | Transfer and inspect files while Android runs. It is not an early-boot recovery connection. |
| A TV remote and access to mains power | Operate the UI and recover from a stopped system when instructed. |
| Stable power | A power failure during configuration or filesystem writes can leave an unusable state. |

These are first-installation and recovery requirements. Once installed and
validated, daily switching uses the TV's internal storage; it does not require
a connected computer, serial adapter or USB drive. Keep the recovery adapter
and backups available.

Allow space for two full image sets, expanded working files and independent
backups. Check the planned sizes before building; a nearly full disk is not
adequate. The [prefix backup tool](boot-backup.md) requires a FAT USB drive with
at least 5 GiB free unless a verified independent prefix backup already exists.
It is not a permanent boot dependency. An external backup disk is useful when
the computer lacks space.

On the tested board the debug adapter used HDMI 1 at **115200, 8N1**.
An ordinary HDMI cable, capture card or USB video adapter is not a UART adapter.
Confirm the adapter's pinout and electrical compatibility with its supplier.
Do not connect a generic TTL board by guessing pins or voltage, and do not
feed an external power pin into the television.
Incorrect wiring or signal voltage can damage the TV or adapter.
The computer must recognize the adapter as a serial port. A product labelled
“HDMI to TTL” is not sufficient evidence that it matches this board.

Before any startup modification, verify all three: readable boot logs, an
interactive bootloader prompt, and a successful exact configuration undo.
Receiving text alone does not prove that the transmit path works.
These checks also apply when an agent performs the installation. Having the
adapter on hand without testing it is not a verified recovery route.

## Without HDMI serial

You can inspect source, download official inputs and build locally without a
serial adapter. This project's installation procedure requires a verified
serial recovery route before changing startup configuration.
If that route is unavailable, stop at offline preparation; do not change the
TV's startup configuration.

ADB depends on a running Android system. Fastboot and the Recovery menu are
not guaranteed to provide a usable write path on this TV. If startup fails,
having an ADB cable, a USB drive or two firmware slots may not be enough.
In the original incident, wipes and USB-entry attempts did not fix the loop;
serial recovery was needed.

Proceeding without it can leave a television that needs a compatible service
package, board-level repair or motherboard replacement. The project does not
promise that an adapter can recover every failure either: it cannot undo
physical damage or recover an unusable bootloader by itself.

## Optional functional checks

- HDMI source: picture, sound, refresh-rate changes and source switching.
- CEC-capable source, such as a console: device names and remote navigation.
- USB storage, Bluetooth audio and ARC/eARC equipment: only if those features
  matter to the owner. Otherwise record them as unverified.
- Camera aimed at the panel: useful for unattended observation. An Android
  screenshot alone does not prove that the physical panel is lit.

## Disclaimer

This is experimental, unofficial software, provided **as is**, without a
warranty of compatibility, reliability or recoverability. Use may cause data
loss, boot loops, loss of device functions or permanent device damage, and may
require paid repair or replacement.
These risks apply to manual steps, scripts and agent-assisted operation.
Neither Xiaomi nor Google has endorsed this project or promised to support
devices modified with it.

You are responsible for deciding whether to use it and for keeping backups and
a recovery route. To the extent permitted by applicable law, contributors are
not liable for resulting damage or loss. Nothing here excludes rights or
liability that applicable law does not allow to be excluded.

The governing license terms are [Apache-2.0, sections 7 and 8](../LICENSE).
This notice does not grant rights to third-party firmware or replace its terms.
It also does not replace the documented prerequisites, known limitations or
the [incident report](incident.md).
