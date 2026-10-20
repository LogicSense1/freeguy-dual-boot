# Boot-loop incident

The early independent-boot experiment left the tested TV in a repeating
Mi Logo / black-screen cycle. Stock HyperOS operation was recovered through the HDMI
serial console. This report separates the observed failure from the part of
the initial transition that was not recorded.

## What the records establish

The persistent loop was:

1. The older, February 2024 A bootloader started the A system.
2. Boot-image signature verification reported green.
3. First-stage mounting failed with a dm-verity error.
4. The kernel restarted with `dm-verity device corrupted`.

The same A-side verity error appeared in records from before this incident.
Green boot-image verification did not establish that the filesystem would pass
dm-verity. The evidence does not establish a physical eMMC failure.

The experiment immediately before the loss of access used a wrapper that
manually called AVB verification, then attempted a fallback boot. Its original
audit replaced the AVB call with a stub and missed the real error-handling
side effects. Later instruction-level tests of the actual B bootloader showed
that rejected verification can increment the hardware retry counter and reset
the processor.

That proves the mechanism exists. It does not recover the missing first
verification result or prove the exact initial B-to-A transition.

Running that experiment before a working serial recovery path had been
demonstrated was an implementation and validation error. A RAM-loaded image
does not imply that every command it calls is free of persistent side effects.

## Recovery and data loss

Serial recovery restored the required initialization state and reached the
original February 2025 B boot chain through the OEM fallback logic. Stock B
then passed a cold-power start, and the owner confirmed normal operation.
The B boot and bootloader images matched their pre-incident backups.

Recovery-menu wipes performed during troubleshooting erased the earlier
Google data. Those data were not recovered. The later data-retention checks
apply to the rebuilt installation, not to the erased installation.

The exact timing of every environment change is unknown: troubleshooting
also included wipes and an upgrade-entry experiment that could reset the
environment. Differences observed afterward cannot all be assigned to the
initial wrapper. Raw serial partition exports and Android logical reads also
used different read paths; unequal hashes alone did not prove a logical change.

## What changed

- The manual AVB wrapper was retired. Normal boots use the original B boot flow.
- The old A system is not treated as a usable recovery slot.
- Google lives in separate files on internal storage; B firmware is retained.
- Each temporary boot change has an exact, checked serial undo.
- The early loader removes its temporary parameters before starting Google.
- A failed runtime pauses automatic Google entry instead of retrying forever.
- Replacement image files retain their predecessors for rollback.

Ordinary restarts and both switch directions have passed on the rebuilt
installation, with no host-side boot command and with both data markers intact.
The current rebuilt installation has not completed a physical cold-power test.

The partition-prefix backup covers the partition table, firmware and
configuration before userdata. It excludes userdata, eMMC hardware boot areas
and RPMB; it is not a full-chip backup. Private backups and raw console records
are not published.
