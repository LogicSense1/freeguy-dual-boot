#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only cold backup of existing Google data files from Xiaomi stock B.

No restore, formatting, switching or initialization commands. Local verification
also accepts earlier backup manifests but does not invent their device binding.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

NAMES = ('uenv.img', 'uuenv.img', 'persist.img', 'tvcertificate.img',
         'userdata.img', 'metadata.img', 'cache.img')
ROOT = Path(__file__).resolve().parents[1]
STAGE = '/data/local/tmp/codex-gtv-fileboot-v1'
MAX_TOTAL = 16 * 1024**3
PROPS = ('ro.product.device', 'ro.boot.slot_suffix', 'sys.boot_completed',
         'ro.build.version.release', 'ro.bootloader', 'ro.boot.verifiedbootstate', 'ro.codex.fileboot')


def need(ok, message):
    if not ok:
        raise ValueError(message)


def private_output(path):
    resolved = path.resolve()
    need(ROOT != resolved and ROOT not in resolved.parents,
         'Private data outputs must stay outside the repository')


def digest_file(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def stock_identity(props, mounts, boot_id, cid, serial, binding_source='emmc-cid'):
    need(props == ['freeguy', '_b', '1', '11', '01.01.250225', 'green', ''],
         'Verified original Xiaomi B is required')
    lines = [line.split() for line in mounts.splitlines()]
    need(lines and all(len(row) >= 6 for row in lines), 'Mount table unavailable or malformed')
    need(any(row[1:3] == ['/data', 'f2fs'] for row in lines), 'Original userdata mount missing')
    need(not any('/loop' in row[0] for row in lines) and
         'codex-gtv-fileboot-v1' not in mounts and 'fileboot-host' not in mounts,
         'Google image files may be active')
    need(re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot_id), 'Invalid boot identity')
    need(isinstance(serial, str) and serial.lower() not in
         ('', 'unknown', 'null', 'freeguy', '0123456789abcdef') and
         8 <= len(serial) <= 128 and len(set(serial)) > 1 and
         all(33 <= ord(c) <= 126 for c in serial), 'Device binding unavailable')
    need(binding_source in ('emmc-cid', 'android-serial'), 'Unknown device binding source')
    if binding_source == 'emmc-cid':
        need(isinstance(cid, str) and re.fullmatch('[0-9a-fA-F]{32}', cid),
             'eMMC identity unavailable; do not silently downgrade the binding')
        material = 'freeguy\0' + cid.lower() + '\0' + serial
        scheme = 'freeguy-emmc-cid-serial-v1'
    else:
        need(cid is None, 'Serial binding must not claim an eMMC identity')
        material = 'freeguy-android-serial-v1\0' + serial
        scheme = 'freeguy-android-serial-v1'
    binding = hashlib.sha256(material.encode()).hexdigest()
    return {'bootId': boot_id, 'deviceBindingSha256': binding,
            'deviceBindingScheme': scheme}


def verify_backup(folder):
    need(folder.is_dir() and not folder.is_symlink(), 'Backup directory must not be a symlink')
    manifest_file = folder / 'manifest.json'
    need(manifest_file.is_file() and not manifest_file.is_symlink() and manifest_file.stat().st_size < 65536,
         'Invalid backup manifest')
    report = json.loads(manifest_file.read_text())
    need(isinstance(report, dict), 'Backup manifest must be an object')
    need(report.get('complete') is True and report.get('stockBVerified') is True and
         report.get('googleImagesInactive') is True and report.get('deviceWritesIssued') is False,
         'Backup is incomplete or not a cold read-only capture')
    rows = report.get('files')
    need(isinstance(rows, list) and len(rows) == len(NAMES) and all(isinstance(r, dict) for r in rows) and
         all(isinstance(r.get('name'), str) for r in rows) and {r.get('name') for r in rows} == set(NAMES),
         'Expected all seven backing images exactly once')
    total = 0
    for row in rows:
        name = row['name']
        need(row.get('backupFile') == name + '.gz' and row.get('readbackVerified') is True and
             type(row.get('bytes')) is int and 4096 <= row['bytes'] <= MAX_TOTAL,
             'Invalid backup file record')
        need(all(isinstance(row.get(k), str) and re.fullmatch('[0-9a-f]{64}', row[k])
                 for k in ('sha256', 'compressedSha256')), 'Invalid backup digest')
        path = folder / row['backupFile']
        need(path.is_file() and not path.is_symlink(), 'Backup must be a regular file')
        need(digest_file(path) == row['compressedSha256'], 'Compressed backup changed')
        h, size = hashlib.sha256(), 0
        with gzip.open(path, 'rb') as stream:
            for data in iter(lambda: stream.read(4 * 1024**2), b''):
                size += len(data)
                need(size <= row['bytes'], 'Expanded backup exceeds declared size')
                h.update(data)
        need(size == row['bytes'] and h.hexdigest() == row['sha256'], 'Expanded backup mismatch')
        total += size
        need(total <= MAX_TOTAL, 'Backup exceeds total bound')
    binding = report.get('deviceBindingSha256')
    need(binding is None or isinstance(binding, str) and re.fullmatch('[0-9a-f]{64}', binding),
         'Invalid device binding')
    scheme = report.get('deviceBindingScheme')
    need(scheme is None or binding is not None and scheme in
         ('freeguy-emmc-cid-serial-v1', 'freeguy-android-serial-v1'), 'Invalid binding scheme')
    return {'filesVerified': len(rows), 'expandedBytes': total,
            'deviceBindingPresent': binding is not None,
            'deviceBindingScheme': scheme or 'legacy-unspecified',
            'currentDeviceOrDataVerified': False, 'restoreAuthorized': False}


def save(path, report):
    temp = path.with_suffix('.tmp')
    with temp.open('w') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)
    directory = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def capture(adb_path, device, destination, binding_source='emmc-cid'):
    private_output(destination)
    need(re.fullmatch('[A-Za-z0-9_.:-]+', device), 'Invalid explicit ADB selector')
    need(binding_source in ('emmc-cid', 'android-serial'), 'Unknown device binding source')
    need(not destination.exists() and not destination.is_symlink() and destination.parent.is_dir(),
         'Use a new backup directory')
    adb = [str(adb_path), '-s', device]
    def shell(command, timeout=30):
        return subprocess.check_output(adb + ['shell', command], text=True, stderr=subprocess.PIPE,
                                       timeout=timeout).strip()
    def identity():
        props = [shell('getprop ' + name) for name in PROPS]
        # An unmounted loop device can still hold and modify a backing file.
        # Reject every attached loop, rather than only checking mount entries.
        loop_check = shell('for p in /sys/block/loop*/loop/backing_file; do '
                           '[ -e "$p" ] || continue; cat "$p" || exit 1; done')
        need(not loop_check, 'Loop backing files are attached; cold backup refused')
        cid = shell('cat /sys/block/mmcblk0/device/cid') if binding_source == 'emmc-cid' else None
        return stock_identity(props, shell('cat /proc/1/mounts'),
            shell('cat /proc/sys/kernel/random/boot_id'),
            cid, shell('getprop ro.serialno'), binding_source)
    first = identity()
    need(shell('test ! -L ' + STAGE + ' && cat ' + STAGE + '/marker') == 'codex-freeguy-fileboot-v1',
         'Existing image directory marker missing')
    def metadata(name):
        path = STAGE + '/' + name
        value = shell('test -f ' + path + ' && test ! -L ' + path + ' && stat -c "%d %i %h %s" ' + path)
        fields = value.split()
        need(len(fields) == 4 and all(n.isdigit() for n in fields) and fields[2] == '1',
             'Image must be a regular non-hardlinked file')
        need(4096 <= int(fields[3]) <= MAX_TOTAL, 'Invalid image size')
        return fields
    before = {name: metadata(name) for name in NAMES}
    total = sum(int(row[3]) for row in before.values())
    need(total <= MAX_TOTAL and shutil.disk_usage(destination.parent).free > total + 2 * 1024**3,
         'Insufficient backup reserve')
    destination.mkdir(mode=0o700)
    report = {**first, 'stockBVerified': True, 'googleImagesInactive': True,
              'deviceWritesIssued': False, 'privateBackup': True, 'files': [], 'complete': False}
    record = destination / 'manifest.json'
    save(record, report)
    def source_hash(name):
        value = shell('sha256sum ' + STAGE + '/' + name, 300).split()[0]
        need(re.fullmatch('[0-9a-f]{64}', value), 'Invalid source digest')
        return value
    for name in NAMES:
        need(identity() == first and metadata(name) == before[name], 'Boot or source identity changed')
        digest = source_hash(name)
        temporary = destination / (name + '.partial')
        with temporary.open('xb') as stream:
            subprocess.run(adb + ['exec-out', 'gzip', '-1', '-c', STAGE + '/' + name],
                           stdout=stream, stderr=subprocess.PIPE, check=True, timeout=1200)
            stream.flush()
            os.fsync(stream.fileno())
        h, size = hashlib.sha256(), 0
        with gzip.open(temporary, 'rb') as stream:
            for data in iter(lambda: stream.read(4 * 1024**2), b''):
                size += len(data)
                need(size <= int(before[name][3]), 'Backup expanded beyond original size')
                h.update(data)
        need(size == int(before[name][3]) and h.hexdigest() == digest, 'Transfer readback failed')
        need(identity() == first and metadata(name) == before[name] and source_hash(name) == digest,
             'Source changed during transfer')
        final = destination / (name + '.gz')
        temporary.rename(final)
        report['files'].append({'name': name, 'bytes': size, 'sha256': digest, 'backupFile': final.name,
                                'compressedSha256': digest_file(final), 'readbackVerified': True})
        save(record, report)
        print('Verified ' + name, flush=True)
    for row in report['files']:
        need(metadata(row['name']) == before[row['name']] and source_hash(row['name']) == row['sha256'],
             'Earlier image changed before backup completion')
    need(identity() == first, 'Boot identity changed before completion')
    report.update(complete=True, completedAtEpoch=int(time.time()))
    save(record, report)
    print(json.dumps(verify_backup(destination)))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    local = sub.add_parser('verify-local')
    local.add_argument('directory', type=Path)
    live = sub.add_parser('capture')
    live.add_argument('--adb', type=Path, required=True)
    live.add_argument('--device', required=True)
    live.add_argument('--destination', type=Path, required=True)
    live.add_argument('--binding-source', choices=('emmc-cid', 'android-serial'), default='emmc-cid',
                      help='Explicitly use android-serial if stock denies CID reads; no automatic fallback')
    args = p.parse_args()
    os.umask(0o077)
    if args.command == 'verify-local':
        print(json.dumps(verify_backup(args.directory)))
    else:
        capture(args.adb, args.device, args.destination, args.binding_source)


if __name__ == '__main__':
    main()
