# Official firmware inputs

[简体中文](firmware.zh-CN.md)

The project uses two exact A/B OTA packages. Neither is a USB recovery package.
Do not rename them to `MiTVUpgrade_*.bin` or flash the donor to a physical slot.
Google TV here is assembled locally from an overseas Xiaomi release; there is
no generic Google TV installer for this Chinese-market board.

| Input | Version | Distribution |
| --- | --- | --- |
| Original Xiaomi HyperOS | `freeguy`, Android 11, `OS2.0.5.0.RFTMATV` | [Xiaomi OTA CDN](https://ota-mitv.cdn.pandora.xiaomi.com/OS2.0.5.0.RFTMATV/freeguy-ota_full-OS2.0.5.0.RFTMATV-user-11.0-6fc20cb6f5.zip) |
| Overseas Google TV donor | `ladybird`, Android 14, build `490` | [Google OTA distribution](https://android.googleapis.com/packages/ota-api/package/904596d74ece1960947b81849914c8e79d0dadc1.zip) |

Both addresses returned the expected ZIP prefix and total size on 2026-10-20.
The full local packages were hashed; exact sizes, SHA-256 values and build
fingerprints are in [`firmware-inputs.json`](../tools/firmware-inputs.json).
The donor address may redirect to Google's `gvt1.com` CDN. Its temporary redirect
URL is not a stable download link.

```sh
mkdir -p /private/tv-inputs
python3 tools/firmware-inputs.py download hyperos \
  --file /private/tv-inputs/hyperos.zip
python3 tools/firmware-inputs.py download google-tv-donor \
  --file /private/tv-inputs/google-tv-donor.zip
```

Use `verify` instead of `download` for files already obtained. The helper checks
size, full SHA-256, device, build and OTA type. It does not establish certificate
trust, authorize redistribution, or write anything to a television.

If a link expires, use the matching device's official updater or ask Xiaomi
for the exact release. The Chinese updater in the investigated release used
`https://whippet.bsp.xiaomi.com/mi/ota/updater/v1/checkUpdate`.
Its client protocol is not a public compatibility guarantee. Do not invent
device identities, copy another owner's credentials, publish embedded client
secrets, or substitute a nearby model's package. A newly obtained version needs
fresh compatibility work; changing the expected hash is not an adaptation.

Extract `payload.bin` locally using a separately obtained, reviewed A/B payload
extractor, then check partition hashes against the payload metadata. Builders
also check their exact input image hashes. Whole-file cryptographic integrity,
trust in a signing certificate, target compatibility and successful physical
operation are separate checks. Google's [A/B OTA documentation](https://source.android.com/docs/core/ota/ab)
describes the update format, not compatibility with this project.

Calibration, pairing records, certificates and other board data must come from
the owner's own TV. They cannot be replaced by OTA files or another TV's backup.
Do not commit downloaded packages, extracted images, certificates or build
outputs. The repository does not grant redistribution rights to their contents.
