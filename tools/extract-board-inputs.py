#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Extract the required private overlay inputs from local stock/donor images.

Requires dissect.extfs and dump.erofs. No firmware download or device access.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from dissect.extfs import ExtFS

VENDOR_PATHS = {
    'stock_vendor_props': '/build.prop',
    'stock_board_init': '/etc/init/hw/init.mt9653.rc',
    'stock_audio_policy': '/etc/audio_policy_configuration.xml',
    'stock_ssl_init': '/etc/init/boringssl_self_test.rc',
    'stock_keylayout': '/usr/keylayout/Vendor_2717_Product_32b8.kl',
    'stock_panel_module': '/lib/modules/mik.ko',
}


def fingerprint(path):
    st = path.stat()
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def check_image(path, expected):
    if path.is_symlink() or not path.is_file() or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('Invalid image input')
    before = fingerprint(path)
    if before[2] > 4 * 1024**3:
        raise ValueError('Image exceeds bound')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            digest.update(block)
    if fingerprint(path) != before or digest.hexdigest() != expected:
        raise ValueError('Image changed or does not match its hash')
    return before


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for prefix in ('stock-vendor', 'google-system'):
        p.add_argument('--' + prefix, type=Path, required=True)
        p.add_argument('--' + prefix + '-sha256', required=True)
    p.add_argument('--dump-erofs', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    os.umask(0o077)
    if args.output.exists():
        p.error('Use a new output directory')
    stock_state = check_image(args.stock_vendor, args.stock_vendor_sha256)
    google_state = check_image(args.google_system, args.google_system_sha256)
    values = {}
    with args.stock_vendor.open('rb') as stream:
        fs = ExtFS(stream)
        for name, path in VENDOR_PATHS.items():
            value = fs.get(path).open().read(16 * 1024**2 + 1)
            if len(value) > 16 * 1024**2:
                raise ValueError('Extracted file exceeds bound')
            values[name] = value
    donor_path = '/system/etc/init/hw/init.rc'
    result = subprocess.run([str(args.dump_erofs.resolve()), '--cat', '--path=' + donor_path,
                             str(args.google_system.resolve())], check=True, capture_output=True, timeout=30)
    if len(result.stdout) > 1024**2:
        raise ValueError('Donor init exceeds bound')
    values['google_system_init'] = result.stdout
    # Detect concurrent rewrites/replacements before emitting a profile that
    # binds extracted bytes to the checked image versions.
    if fingerprint(args.stock_vendor) != stock_state or fingerprint(args.google_system) != google_state:
        raise ValueError('Source image changed during extraction')
    args.output.mkdir(parents=True)
    records = {}
    for name, value in values.items():
        (args.output / name).write_bytes(value)
        records[name] = {'path': name, 'sha256': hashlib.sha256(value).hexdigest()}
    profile = {'version': 1, 'inputs': records, 'dataLoops': {'data': 15, 'metadata': 16, 'cache': 17}}
    (args.output / 'overlay-profile.json').write_text(json.dumps(profile, indent=2) + '\n')
    provenance = {'stockVendorSha256': args.stock_vendor_sha256,
                  'googleSystemSha256': args.google_system_sha256,
                  'pathsInStockVendor': VENDOR_PATHS, 'pathInGoogleSystem': donor_path,
                  'files': {key: row['sha256'] for key, row in records.items()},
                  'sourceImagesChanged': False, 'containsOemFiles': True, 'deviceAccess': False,
                  'dataLoopMapping': 'Existing file-backed layout; check against final image mount map'}
    (args.output / 'extraction.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(json.dumps({'files': len(records), 'sourceImagesChanged': False, 'containsOemFiles': True}))


if __name__ == '__main__':
    main()
