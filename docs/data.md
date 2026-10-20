# Data images and backups

[简体中文](data.zh-CN.md)

Google uses seven writable files in `/data/local/tmp/codex-gtv-fileboot-v1/`.
They contain private device and user data. Keep them outside Git and release
attachments. These files are **not raw physical-partition backups**, even where
the names match a partition.

| Files | Contents |
| --- | --- |
| `uenv.img`, `uuenv.img` | File-backed copies used inside the Google environment |
| `persist.img`, `tvcertificate.img` | Same-device settings and identity material |
| `userdata.img`, `metadata.img`, `cache.img` | Separate Google data files |

Do not write any of these files to `/dev/block/by-name/*` or use them as input
to Fastboot flash. Never substitute another TV's device data. The complete
initial extraction and checked device installation of these seven files is
still missing from the public workflow.

## Back up an existing installation

Return normally to HyperOS first. The capture tool requires the supported
original B system, completed Android 11 startup and no attached loop images.
It refuses an active Google environment and does not switch systems for you.
Keep the TV in HyperOS for the entire capture.

```sh
python3 tools/backup-data.py capture \
  --adb /path/to/adb \
  --device TV_ADB_SERIAL_OR_ENDPOINT \
  --destination /private/tv-backup-01
```

This issues read-only ADB commands. It checks the boot identity, source file
metadata and source hashes before and after copying. It also checks every
earlier file again before marking the complete set valid. Each compressed file
is decompressed and hashed locally. A failed capture remains incomplete.

The default device binding hashes the eMMC CID and Android serial together.
If the stock system denies CID access, investigate that failure first.
`--binding-source android-serial` explicitly chooses a weaker, separate binding
scheme; the tool never downgrades automatically. A binding is an identity check,
not encryption or proof of device authenticity.

Allow free space for the full expanded total plus 2 GiB, even though transfer
uses gzip. The destination must be new and outside this checkout, including
through symlinked parent paths. Local output is private; it is not encrypted.

Verify a retained copy without connecting to the TV:

```sh
python3 tools/backup-data.py verify-local /private/tv-backup-01
```

Verification checks the seven files, compressed and expanded hashes, sizes and
completion record. It does not check the current TV, filesystem consistency or
application behavior. Earlier manifests without a binding can be checked for
copy integrity but are not accepted by the recovery preparation tool.

## Prepare a backup for recovery

Use the binding and scheme recorded separately when the owner's TV was checked.
Do not treat values copied from an unknown backup as an independent identity
check. Replace `KNOWN_DEVICE_BINDING_SHA256` below with that known 64-digit hash.

```sh
python3 tools/prepare-data-restore.py \
  --backup /private/tv-backup-01 \
  --expected-binding KNOWN_DEVICE_BINDING_SHA256 \
  --expected-binding-scheme freeguy-emmc-cid-serial-v1 \
  --output /private/tv-recovery-01
```

Use `freeguy-android-serial-v1` only for a backup captured with that scheme.
This command expands and reads back new local files. It never contacts a TV,
overwrites existing images or installs the recovered files.

Hash equality alone is insufficient for a restore. A retained reference backup
passed copy verification while its two environment-copy files failed read-only
filesystem checks. Local repair copies later passed those checks; they were
not installed. Preserve originals, inspect filesystem consistency separately,
and retain a checked rollback before any future installation. Do not turn an
offline preparation result into a claim of device recovery.

## Create data for a new installation only

`create-data-images.py` creates empty ext4 files: 6144 MiB `userdata.img`, 64 MiB
`metadata.img` and 64 MiB `cache.img`. It installs the owner's ADB public key in
the new userdata file and checks file ownership, modes and SELinux labels.
It does not supply the other four same-device files.

```sh
python3 tools/create-data-images.py \
  --adb-public-key /private/owner-adbkey.pub \
  --adb-public-key-sha256 PUBLIC_KEY_FILE_SHA256 \
  --docker /path/to/docker \
  --container local/e2fsprogs \
  --output /private/fresh-google-data-01
```

Use a locally prepared e2fsprogs container and the Python reader described in
[Dependencies](../DEPENDENCIES.md). The container is not pulled, has no network,
and mounts only the new output directory. Allow more than 8.125 GiB free.
The build checks ext4 features, runs read-only `e2fsck`, then independently reads
the image metadata and ADB key. Existing output directories are rejected.

These are empty initialization images, not an update or recovery. Replacing an
existing installation with them would discard its Google accounts and settings.
Do not use this tool during ordinary switching or an image update.

## Checks

```sh
python3 -B tests/test-backup-data.py
python3 -B tests/test-prepare-data-restore.py
python3 -B tests/test-create-data-images.py
```

Tests cover identity and in-use-image rejection, changing sources, corrupt or
incomplete backups, output isolation, and preservation of existing files.
Simulated captures exercise an allowlist of read-only device commands. They do
not replace a live capture, filesystem checks or a verified device restore.
