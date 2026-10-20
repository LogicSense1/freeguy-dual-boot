#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Assemble a private runtime root from stock bootstrap and individual inputs.

No previous trial ramdisk, device access or partition writes. This produces the
runtime CPIO, not the backing Google filesystems or an installation release.
"""
import argparse
import base64
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('components', ROOT / 'tools/assemble-runtime-components.py')
components = importlib.util.module_from_spec(spec)
spec.loader.exec_module(components)
archive, need = components.archive, components.need
sha = lambda data: hashlib.sha256(data).hexdigest()

# Order is part of the board contract: data/metadata/cache use loops 15/16/17.
LAYOUT = (
    ('system.img', 'erofs', '', 'ro'),
    ('vendor.img', 'ext4', '/vendor', 'ro'),
    ('system_ext.img', 'erofs', '/system_ext', 'ro'),
    ('product.img', 'erofs', '/product', 'ro'),
    ('tvservice.img', 'ext4', '/mnt/vendor/tvservice', 'ro'),
    ('linux_rootfs_a.img', 'squashfs', '/mnt/vendor/linux_rootfs', 'ro'),
    ('3rd_a.img', 'squashfs', '/mnt/vendor/3rd', 'ro'),
    ('tvconfig_b.img', 'ext4', '/vendor/tvconfig', 'ro'),
    ('bootdata_b.img', 'ext4', '/mnt/vendor/bootdata', 'ro'),
    ('project_id.img', 'ext4', '/vendor/project_id', 'ro'),
    ('uenv.img', 'ext4', '/mnt/vendor/uenv', 'rw'),
    ('uuenv.img', 'ext4', '/mnt/vendor/uuenv', 'rw'),
    ('factory_a_b.img', 'ext4', '/mnt/vendor/factory', 'ro'),
    ('persist.img', 'ext4', '/vendor/persist', 'rw'),
    ('tvcertificate.img', 'ext4', '/vendor/tvcertificate', 'rw'),
    ('userdata.img', 'ext4', '/data', 'rw'),
    ('metadata.img', 'ext4', '/metadata', 'rw'),
    ('cache.img', 'ext4', '/cache', 'rw'),
)
BOARD = (
    'CaptureRecorderServer.rc', 'audio_policy_configuration.xml', 'boot-control.xml',
    'boringssl_self_test.rc', 'dtv_svc.sh', 'fstab.mt9653', 'init.mt9653.rc',
    'mik-logo-detection.ko', 'native-init.rc', 'remote-32b8.kl', 'vendor-build.prop',
    'hdmi-etc/mitv.config.xml', 'hdmi-permissions/codex-builtin-microphone.xml',
)
AUDIO = (
    'remote-audio-hw/android.hidl.memory@1.0-impl.so', 'remote-audio-hw/audio.rc.default.so',
    'remote-vendor-audio-hw/audio.primary.codex-original.so',
    'remote-vendor-audio-hw/audio.rc.default.so', 'remote-vendor-audio-hw/libchrome.so',
    'remote-vendor-audio-hw/libstatslog.so', 'remote-vendor-audio-hw/libstatssocket.so',
)
DIRECTORIES = ('codex', 'codex/autovoice', 'codex/overlays',
    'codex/overlays/hdmi-etc', 'codex/overlays/hdmi-permissions',
    'codex/overlays/hdmi-priv-app', 'codex/overlays/remote-audio-hw',
    'codex/overlays/remote-vendor-audio-hw',
    'codex/overlays/hdmi-priv-app/NativeHdmiLabel',
    'codex/overlays/hdmi-priv-app/OriginalHdmi')


def mount_files(hashes):
    names = {name for name, _, _, mode in LAYOUT if mode == 'ro'}
    need(isinstance(hashes, dict) and set(hashes) == names, 'Unexpected immutable image set')
    need(all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
             for value in hashes.values()), 'Invalid immutable image hash')
    mounts = '#!/system/bin/sh\n' + ''.join(
        'image_mount %s %s "$G%s" %s\n' % row for row in LAYOUT[1:])
    index = ''.join(hashes[name] + '  ' + name + '\n'
                    for name, _, _, mode in LAYOUT if mode == 'ro')
    return mounts.encode(), index.encode()


def validate_adb_key(data):
    need(0 < len(data) < 4096 and data.endswith(b'\n') and data.count(b'\n') == 1,
         'Expected one owner-authorized ADB public key')
    need(all(32 <= byte < 127 for byte in data[:-1]), 'Invalid public key text')
    token = data.split(b' ', 1)[0].strip()
    try:
        decoded = base64.b64decode(token, validate=True)
    except Exception as exc:
        raise ValueError('Invalid ADB public key encoding') from exc
    need(len(decoded) == 524 and struct.unpack_from('<I', decoded)[0] == 64 and
         struct.unpack_from('<I', decoded, 520)[0] == 65537, 'Expected Android RSA public key')


def assemble(seed, artifacts, overlays, policy, key, hashes, entry, mounts, permission, starter):
    validate_adb_key(key)
    need(set(overlays) == set(BOARD + AUDIO), 'Unexpected overlay set')
    image_mounts, immutable = mount_files(hashes)
    items = copy.deepcopy(seed)
    names = {e['name'] for e in items}
    need(len(names) == len(items), 'Duplicate bootstrap entry')
    need(not any(n in ('codex', 'first_stage_ramdisk/codex') or
                 n.startswith(('codex/', 'first_stage_ramdisk/codex/'))
                 for n in names), 'Bootstrap already contains runtime entries')
    byname = {e['name']: e for e in items}
    for prefix in ('', 'first_stage_ramdisk/'):
        need(byname[prefix + 'system/bin/init']['data'] == artifacts['helper'], 'Bootstrap helper mismatch')
        need(stat.S_ISREG(byname[prefix + 'system/bin/stock-init']['fields'][1]), 'Missing stock fallback')
    files = {
        'codex/entry.sh': (entry, 0o700),
        'codex/candidate-policy': (policy, 0o600),
        'codex/host-adb-key': (key, 0o600),
        'codex/immutable.sha256': (immutable, 0o600),
        'codex/mount-images.sh': (image_mounts, 0o600),
        'codex/mount-overlays.sh': (mounts, 0o600),
        'codex/overlays/hdmi-permissions/privapp-permissions-original-hdmi.xml': (permission, 0o644),
        **{'codex/overlays/' + name: (content, 0o644) for name, content in overlays.items()},
    }
    for source, targets in components.MAPPED.items():
        for name in targets:
            if name == 'system/bin/init':
                continue
            mode = 0o755 if name in ('codex/helper', 'codex/trial-guard') else 0o644
            files[name] = (artifacts[source], mode)
    nextino = max(e['fields'][0] for e in items) + 1
    for prefix in ('', 'first_stage_ramdisk/'):
        for name in DIRECTORIES:
            mode = 0o700 if name in ('codex', 'codex/overlays') else 0o755
            items.append({'name': prefix + name,
                          'fields': [nextino, stat.S_IFDIR | mode, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0], 'data': b''})
            nextino += 1
        for name, (content, mode) in files.items():
            items.append({'name': prefix + name,
                          'fields': [nextino, stat.S_IFREG | mode, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0], 'data': content})
            nextino += 1
    components.integrate(items, artifacts, starter)
    archive.table(items)
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    need(not args.output.exists(), 'Use a new output directory')
    raw_profile = args.profile.read_bytes()
    need(len(raw_profile) <= 65536, 'Profile exceeds bound')
    profile = json.loads(raw_profile)
    need(set(profile) == {'version', 'bootstrap', 'artifacts', 'overlays', 'policy',
                         'adb-public-key', 'immutable-images'} and profile['version'] == 1,
         'Unexpected profile')
    need(set(profile['artifacts']) == set(components.MAPPED) |
         {'policy', 'voice-oem.apk', 'voice-libmibluetooth_jni.so'}, 'Unexpected component set')
    need(set(profile['overlays']) == set(BOARD + AUDIO), 'Unexpected overlay set')
    read = lambda record: components.read_input(record, args.profile.parent)
    source = read(profile['bootstrap'])
    with gzip.GzipFile(fileobj=io.BytesIO(source)) as stream:
        seed = archive.entries(stream.read(archive.MAX_BYTES + 1))
    artifacts = {name: read(record) for name, record in profile['artifacts'].items()}
    overlays = {name: read(record) for name, record in profile['overlays'].items()}
    scripts = {name: (ROOT / 'runtime' / name).read_bytes() for name in
               ('fileboot-entry.sh', 'mount-overlays.sh', 'runtime-policy-start.sh')}
    items = assemble(seed, artifacts, overlays, read(profile['policy']), read(profile['adb-public-key']),
                     profile['immutable-images'], scripts['fileboot-entry.sh'], scripts['mount-overlays.sh'],
                     (ROOT / 'hdmi/privapp-permissions-original-hdmi.xml').read_bytes(),
                     scripts['runtime-policy-start.sh'])
    for e in items:
        if not e['name'].startswith('first_stage_ramdisk/') and e['name'].startswith('codex/') and e['name'].endswith('.sh'):
            subprocess.run(['sh', '-n'], input=e['data'], check=True, capture_output=True)
    packed = components.pack(items)
    need(len(archive.entries(packed)) == len(items), 'CPIO readback failed')
    output = gzip.compress(packed, mtime=0)
    args.output.mkdir(parents=True)
    (args.output / 'ramdisk.cpio.gz').write_bytes(output)
    report = {'profileSha256': sha(raw_profile), 'bootstrapSha256': sha(source),
              'outputSha256': sha(output), 'entries': len(items),
              'priorTrialRamdiskRequired': False, 'containsOemFilesAndOwnerKey': True,
              'backingFilesystemsBuilt': False, 'backingFilesystemsVerified': False,
              'runtimeTested': False, 'deployEligible': False, 'deviceAccess': False,
              'omittedObsoletePwmTraceFiles': True}
    (args.output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
