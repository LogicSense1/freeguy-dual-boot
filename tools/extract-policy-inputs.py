#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Extract a private policy profile from the five exact supported OTA images.

No device access, compiled policy or firmware is included in this source.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

from dissect.extfs import ExtFS

spec = importlib.util.spec_from_file_location(
    'image_inputs', Path(__file__).with_name('extract-board-inputs.py'))
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)

HASHES = {
    'stock_vendor': '87f22567d736d8f4955fa29f7bcd493be03f5b78c7c215003dcff94b916400db',
    'system': '52a1fa101a37ca2b23cc5a32b667e3df4fdb00a926969b0493908bdcb0c69169',
    'system_ext': '0ff218a07effba45363a14ecb6024792e51554c1fa61571a78798547cf4ec59c',
    'product': '3ae6a3b4faed03553c57949a263e1e386112d96d5c2fb6f781fe9d95008b3a91',
    'vendor': '40101574a01440687d704c624ff8e98b3b90e6eade7517a7799fee3e3f9ebcec',
}
INPUTS = {
    'system-plat_sepolicy.cil': ('system', '/system/etc/selinux/plat_sepolicy.cil'),
    'system-30.0.cil': ('system', '/system/etc/selinux/mapping/30.0.cil'),
    'system-30.0.compat.cil': ('system', '/system/etc/selinux/mapping/30.0.compat.cil'),
    'system_ext-system_ext_sepolicy.cil': ('system_ext', '/etc/selinux/system_ext_sepolicy.cil'),
    'system_ext-30.0.compat.cil': ('system_ext', '/etc/selinux/mapping/30.0.compat.cil'),
    'product-product_sepolicy.cil': ('product', '/etc/selinux/product_sepolicy.cil'),
    'plat_pub_versioned.cil': ('stock_vendor', '/etc/selinux/plat_pub_versioned.cil'),
}
CONTEXTS = {}
for image, prefix in (('system', 'plat'), ('system_ext', 'system_ext'), ('product', 'product')):
    folder = '/system/etc/selinux/' if image == 'system' else '/etc/selinux/'
    for kind in ('file', 'hwservice', 'property', 'seapp', 'service'):
        name = prefix + '_' + kind + '_contexts'
        CONTEXTS[image + '-' + name] = (image, folder + name)
CONTEXTS['system-plat_keystore2_key_contexts'] = (
    'system', '/system/etc/selinux/plat_keystore2_key_contexts')
for kind in ('property', 'file', 'hwservice', 'service'):
    name = 'vendor_' + kind + '_contexts'
    CONTEXTS['stock-' + name] = ('stock_vendor', '/etc/selinux/' + name)
VENDOR = {
    'stockVendor': ('stock_vendor', '/etc/selinux/vendor_sepolicy.cil'),
    'donorVendor': ('vendor', '/etc/selinux/vendor_sepolicy.cil'),
}
MAX_FILE = 16 * 1024**2


def assemble(read):
    """Read only the fixed paths; emit relative paths and hashes, never contents in reports."""
    files = {}

    def record(name, source):
        data = read(*source)
        if not isinstance(data, bytes) or len(data) > MAX_FILE or b'\0' in data:
            raise ValueError('Invalid policy text: ' + name)
        data.decode('utf-8')
        if name in files:
            raise ValueError('Duplicate policy output')
        files[name] = data
        return {'path': name, 'sha256': hashlib.sha256(data).hexdigest()}

    profile = {'version': 1,
               'inputs': {n: record(n, src) for n, src in INPUTS.items()},
               'contexts': {n: record(n, src) for n, src in CONTEXTS.items()}}
    for name, source in VENDOR.items():
        profile[name] = record(name + '.cil', source)
    return files, profile


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in HASHES:
        p.add_argument('--' + name.replace('_', '-'), type=Path, required=True)
    p.add_argument('--dump-erofs', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    os.umask(0o077)
    if args.output.exists():
        p.error('Use a new output directory')
    sources = {n: getattr(args, n) for n in HASHES}
    states = {n: images.check_image(path, HASHES[n]) for n, path in sources.items()}
    with sources['stock_vendor'].open('rb') as stream:
        stock = ExtFS(stream)

        def read(image, path):
            if image == 'stock_vendor':
                return stock.get(path).open().read(MAX_FILE + 1)
            # Write tool output to a temporary file so a malformed input cannot
            # force an unbounded capture in memory.
            import tempfile
            with tempfile.TemporaryFile() as output:
                subprocess.run([str(args.dump_erofs.resolve()), '--cat', '--path=' + path,
                                str(sources[image].resolve())], stdout=output,
                               stderr=subprocess.PIPE, check=True, timeout=30)
                output.seek(0)
                return output.read(MAX_FILE + 1)

        files, profile = assemble(read)
    if any(images.fingerprint(path) != states[n] for n, path in sources.items()):
        raise ValueError('Source image changed during extraction')
    args.output.mkdir(parents=True)
    for name, data in files.items():
        target = args.output / name
        target.write_bytes(data)
        if target.read_bytes() != data:
            raise ValueError('Extracted input readback failed')
    (args.output / 'profile.json').write_text(json.dumps(profile, indent=2) + '\n')
    report = {'inputImageSha256': HASHES, 'files': len(files), 'deviceAccess': False,
              'sourceImagesChanged': False, 'containsFirmwareDerivedPolicy': True}
    (args.output / 'extraction.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
