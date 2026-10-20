# Working on this project

Read README, `docs/equipment.md`, `docs/incident.md` and `docs/firmware.md` first.
This is a device-specific source preview, not a generic TV installer.

- Keep source in this repository. Keep firmware, generated images, local
  profiles, backups, keys, device identities and logs outside it.
- Record the exact board, bootloader, firmware and input hashes. A model name
  or a successful compilation is insufficient evidence of compatibility.
- Before changing startup state, establish interactive HDMI UART and verify
  the exact undo on that device. Preserve the original B boot chain. The old A
  system on the reference TV is faulty and is not a recovery slot.
- Do not replay historical experiments, manually invoke AVB verification,
  replace mboot, change slot retries or flash a donor image as a shortcut.
- Build into new directories. Preserve previous loaders, images and data.
  Never replace an existing user's data images with newly initialized files.
- Keep offline tests, build checks, device observations and owner-confirmed
  physical results separate. A screenshot is not proof of a lit panel.
- Stop a failing boot sequence and use the verified undo. Do not respond to
  repeated failures with unplanned writes, wipes or another unverified boot.
- For publication, inspect the exact staged files and their origin. Do not
  add raw logs, generated declarations, APKs, extracted libraries or signing
  material. Apache-2.0 covers the project's code, not third-party firmware.

Use the component build guides for available steps. If an installation step is
not yet documented or implemented, identify it as missing; do not fill it with
guessed device commands. A completed reproduction must demonstrate both switch
directions, remembered defaults, retained data and a working recovery path on
the recipient's own TV.
