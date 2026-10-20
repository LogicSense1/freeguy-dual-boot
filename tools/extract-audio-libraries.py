#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Extract the local audio dependency closure from pinned firmware images.

The output contains OEM/Android libraries and must stay private. No device I/O.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import zipfile

from dissect.extfs import ExtFS

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('image_inputs', Path(__file__).with_name('extract-board-inputs.py'))
image_inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_inputs)
MAX_FILE = 16 * 1024**2


def read_ext_file(fs, path):
    raw = fs.get(path).open().read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        raise ValueError('Extracted library exceeds bound')
    return raw


def read_stats_apex(raw):
    if len(raw) > MAX_FILE:
        raise ValueError('Stats APEX exceeds bound')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = [entry for entry in archive.infolist() if entry.filename == 'apex_payload.img']
        if len(entries) != 1 or entries[0].file_size > 64 * 1024**2:
            raise ValueError('Unexpected stats APEX payload')
        payload = archive.read(entries[0])
    if payload[1080:1082] != b'\x53\xef':
        raise ValueError('Expected ext4 stats APEX payload')
    return read_ext_file(ExtFS(io.BytesIO(payload)), '/lib/libstatssocket.so')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for prefix in ('stock-system', 'stock-vendor', 'google-system'):
        p.add_argument('--' + prefix, type=Path, required=True)
        p.add_argument('--' + prefix + '-sha256', required=True)
    p.add_argument('--dump-erofs', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    os.umask(0o077)
    if args.output.exists():
        p.error('Use a new output directory')
    sources = {name: (getattr(args, name), getattr(args, name + '_sha256'))
               for name in ('stock_system', 'stock_vendor', 'google_system')}
    states = {name: image_inputs.check_image(path, digest) for name, (path, digest) in sources.items()}
    files, origins = {}, {}

    def put(target, raw, image, path):
        if len(raw) < 52 or raw[:6] != b'\x7fELF\x01\x01' or struct.unpack_from('<HH', raw, 16) != (3, 40):
            raise ValueError('Expected ARM32 shared library: ' + target)
        files[target] = raw
        origins[target] = {'image': image, 'path': path, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}

    with args.stock_system.open('rb') as stream:
        fs = ExtFS(stream)
        remote = read_ext_file(fs, '/system/lib/hw/audio.rc.default.so')
        for folder in ('remote-audio-hw', 'remote-vendor-audio-hw'):
            put(folder + '/audio.rc.default.so', remote, 'stock_system', '/system/lib/hw/audio.rc.default.so')
        for name in ('libchrome.so', 'libstatslog.so'):
            path = '/system/lib/' + name
            put('remote-vendor-audio-hw/' + name, read_ext_file(fs, path), 'stock_system', path)
    with args.stock_vendor.open('rb') as stream:
        path = '/lib/hw/audio.primary.mt5879.so'
        put('remote-vendor-audio-hw/audio.primary.codex-original.so',
            read_ext_file(ExtFS(stream), path), 'stock_vendor', path)

    def donor(path):
        result = subprocess.run([str(args.dump_erofs.resolve()), '--cat', '--path=' + path,
                                 str(args.google_system.resolve())], check=True, capture_output=True, timeout=30)
        if len(result.stdout) > MAX_FILE:
            raise ValueError('Extracted donor file exceeds bound')
        return result.stdout

    path = '/system/lib/hw/android.hidl.memory@1.0-impl.so'
    put('remote-audio-hw/android.hidl.memory@1.0-impl.so', donor(path), 'google_system', path)
    path = '/system/apex/com.android.os.statsd.apex'
    put('remote-vendor-audio-hw/libstatssocket.so', read_stats_apex(donor(path)),
        'google_system', path + '!apex_payload.img!/lib/libstatssocket.so')
    if any(image_inputs.fingerprint(path) != states[name] for name, (path, _) in sources.items()):
        raise ValueError('Source image changed during extraction')
    args.output.mkdir(parents=True)
    for name, raw in files.items():
        target = args.output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    report = {'images': {name: digest for name, (_, digest) in sources.items()}, 'files': origins,
              'deviceAccess': False, 'sourceImagesChanged': False, 'containsThirdPartyBinaries': True,
              'runtimeTested': False, 'deployEligible': False}
    (args.output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'libraries': len(files), 'uniqueLibraries': len(set(files.values())),
                      'containsThirdPartyBinaries': True, 'deviceAccess': False}))


if __name__ == '__main__':
    main()
