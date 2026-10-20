#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Extract the stock VNDK 30 input tree from a local, verified system image.

No TV connection or live filesystem dump is needed. Extracted libraries and
the source manifest remain private and retain their respective licenses.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys

from dissect.extfs import ExtFS

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('extract',
    Path(__file__).resolve().parents[1] / 'tools/extract-board-inputs.py')
extract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract)
STOCK_SHA = '191cb32b8e4ea036e60402eb7ecc09acaf07034a39908aa949fb92c698aeeed8'
SOURCE = '/system/apex/com.android.vndk.current'
MANIFEST = bytes.fromhex('0a14636f6d2e616e64726f69642e766e646b2e7633301001')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stock-system', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    os.umask(0o077)
    if args.output.exists():
        p.error('Use a new output directory')
    state = extract.check_image(args.stock_system, STOCK_SHA)
    values, metadata, total = {}, {}, 0
    with args.stock_system.open('rb') as stream:
        fs = ExtFS(stream)
        pending, seen = [('', fs.get(SOURCE))], set()
        while pending:
            name, node = pending.pop()
            if name in seen or len(seen) > 2048:
                raise ValueError('Repeated or oversized VNDK tree')
            seen.add(name)
            if stat.S_ISDIR(node.inode.i_mode):
                for child in node.iterdir():
                    if child.filename in ('.', '..'):
                        continue
                    if not re.fullmatch('[A-Za-z0-9_.+@-]+', child.filename):
                        raise ValueError('Unsupported VNDK file name')
                    pending.append(((name + '/' if name else '') + child.filename, child))
            elif stat.S_ISREG(node.inode.i_mode):
                if node.size > 32 * 1024**2:
                    raise ValueError('VNDK input exceeds bound')
                data = node.open().read(32 * 1024**2 + 1)
                if len(data) != node.size:
                    raise ValueError('Truncated VNDK input')
                total += len(data)
                if total > 256 * 1024**2:
                    raise ValueError('VNDK tree exceeds bound')
                values[name] = data
                metadata[name] = {'mode': node.inode.i_mode, 'uid': node.inode.i_uid,
                                  'gid': node.inode.i_gid,
                                  'xattrs': {x.name: x.value.hex() for x in node.xattr}}
            else:
                raise ValueError('VNDK input must contain only regular files and directories')
    if values.get('apex_manifest.pb') != MANIFEST or not any(n.startswith('lib/') for n in values):
        raise ValueError('Expected the validated flattened VNDK 30 package')
    if extract.fingerprint(args.stock_system) != state:
        raise ValueError('Source changed during extraction')
    args.output.mkdir(parents=True)
    target = args.output / 'files'
    target.mkdir()
    rows = []
    for name, data in sorted(values.items()):
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Extracted input readback failed')
        rows.append({'path': name, 'bytes': len(data), 'sha256': digest,
                     'sourceMetadata': metadata[name]})
    report = {'sourceImageSha256': STOCK_SHA, 'sourcePath': SOURCE,
              'package': 'com.android.vndk.v30', 'format': 'flattened', 'files': rows,
              'originalImageChanged': False, 'containsThirdPartyFiles': True,
              'liveDumpRequired': False, 'deviceAccess': False, 'deployEligible': False}
    (args.output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'files': len(rows), 'bytes': total, 'liveDumpRequired': False, 'deviceAccess': False}))


if __name__ == '__main__':
    main()
