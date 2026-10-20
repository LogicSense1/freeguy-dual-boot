#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify and expand a bound cold backup into NEW local files.

This prepares recovery material. It never contacts a TV or installs files.
The caller-supplied binding is a comparison, not live device authentication.
These file-backed images must never be written to physical TV partitions.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('backup', Path(__file__).with_name('backup-data.py'))
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
need = backup.need
SCHEMES = ('freeguy-emmc-cid-serial-v1', 'freeguy-android-serial-v1')
CHUNK = 4 * 1024**2


def prepare(source, destination, expected_binding, expected_scheme):
    backup.private_output(destination)
    need(not destination.exists() and not destination.is_symlink() and destination.parent.is_dir(),
         'Use a new local output directory')
    need(expected_scheme in SCHEMES and isinstance(expected_binding, str) and
         re.fullmatch('[0-9a-f]{64}', expected_binding), 'Explicit known device binding required')
    manifest = source / 'manifest.json'
    need(source.is_dir() and not source.is_symlink() and manifest.is_file() and
         not manifest.is_symlink() and manifest.stat().st_size < 65536, 'Invalid backup manifest')
    original = manifest.read_bytes()
    record = json.loads(original)
    need(isinstance(record, dict) and record.get('deviceBindingSha256') == expected_binding and
         record.get('deviceBindingScheme') == expected_scheme, 'Backup device binding mismatch')
    verified = backup.verify_backup(source)
    need(manifest.read_bytes() == original, 'Backup manifest changed')
    need(shutil.disk_usage(destination.parent).free > verified['expandedBytes'] + 2 * 1024**3,
         'Insufficient local recovery reserve')
    os.umask(0o077)
    destination.mkdir(mode=0o700)
    report = dict(complete=False, deviceAccess=False, installed=False, privateOutput=True,
                  sourceManifestSha256=hashlib.sha256(original).hexdigest(),
                  deviceBindingSha256=expected_binding, deviceBindingScheme=expected_scheme,
                  currentDeviceVerified=False, filesystemConsistencyVerified=False, files=[])
    output_manifest = destination / 'manifest.json'
    backup.save(output_manifest, report)
    for row in record['files']:
        compressed = source / row['backupFile']
        need(compressed.is_file() and not compressed.is_symlink() and
             backup.digest_file(compressed) == row['compressedSha256'], 'Backup changed before expansion')
        partial = destination / (row['name'] + '.partial')
        count, digest = 0, hashlib.sha256()
        with gzip.open(compressed, 'rb') as inp, partial.open('xb') as out:
            while True:
                data = inp.read(CHUNK)
                if not data:
                    break
                count += len(data)
                need(count <= row['bytes'], 'Expanded backup exceeds declared size')
                digest.update(data)
                if data == b'\0' * len(data):
                    out.seek(len(data), os.SEEK_CUR)
                else:
                    out.write(data)
            need(count == row['bytes'] and digest.hexdigest() == row['sha256'],
                 'Expanded backup mismatch')
            out.truncate(count)
            out.flush()
            os.fsync(out.fileno())
        need(backup.digest_file(partial) == row['sha256'], 'Local image readback mismatch')
        final = destination / row['name']
        partial.rename(final)
        report['files'].append(dict(name=final.name, bytes=count, sha256=row['sha256'],
                                    readbackVerified=True))
        backup.save(output_manifest, report)
        print('Prepared and read back ' + final.name, flush=True)
    need(manifest.read_bytes() == original, 'Backup manifest changed during preparation')
    report['complete'] = True
    backup.save(output_manifest, report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backup', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--expected-binding', required=True)
    p.add_argument('--expected-binding-scheme', choices=SCHEMES, required=True)
    a = p.parse_args()
    r = prepare(a.backup, a.output, a.expected_binding, a.expected_binding_scheme)
    print(json.dumps(dict(complete=r['complete'], files=len(r['files']), installed=False)))


if __name__ == '__main__':
    main()
