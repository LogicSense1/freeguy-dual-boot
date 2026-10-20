#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Rebuild private donor framework images with the local VNDK 30 APEX.

Outputs are file-backed inputs, not flash images. All original file content and
selected metadata are read back. No firmware, signing keys or tools are bundled.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
from erofs_metadata import ErofsMetadata, need

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('image_input', ROOT / 'tools/extract-board-inputs.py')
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)
HASHES = {'system': '52a1fa101a37ca2b23cc5a32b667e3df4fdb00a926969b0493908bdcb0c69169',
          'system_ext': '0ff218a07effba45363a14ecb6024792e51554c1fa61571a78798547cf4ec59c'}
sha = lambda data: hashlib.sha256(data).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(4 * 1024**2), b''):
            digest.update(data)
    return digest.hexdigest()


def add_vndk_manifest(original):
    root = ET.fromstring(original)
    need(root.tag == 'manifest' and original.count(b'</manifest>') == 1 and
         sorted(n.findtext('version') for n in root.findall('vendor-ndk')) == ['34'],
         'Expected donor VNDK 34 manifest without an existing VNDK 30 declaration')
    result = original.replace(b'</manifest>',
        b'    <vendor-ndk>\n        <version>30</version>\n    </vendor-ndk>\n</manifest>')
    need(sorted(n.findtext('version') for n in ET.fromstring(result).findall('vendor-ndk')) == ['30', '34'],
         'Unexpected resulting VNDK declarations')
    return result


def rebuild(source, output, changes, dump, mkfs, fsck):
    """Each change supplies data and an existing metadata-template path."""
    need(not output.exists(), 'Output already exists')
    oldfs = ErofsMetadata(source)
    old = oldfs.inventory()
    need(all(p.startswith('/') and all(s not in ('.', '..') for s in p.split('/')) and
             (p in old or change['template'] in old) for p, change in changes.items()), 'Invalid change path')
    size = sum(n['size'] for n in old.values() if stat.S_ISREG(n['mode']))
    size += sum(len(c['data']) for c in changes.values())
    need(shutil.disk_usage(output.parent).free > size * 2 + 2 * 1024**3, 'Insufficient workspace reserve')
    tar = output.with_suffix('.tar')
    need(not tar.exists(), 'Temporary archive already exists')
    expected = {}
    with tarfile.open(tar, 'w', format=tarfile.PAX_FORMAT) as stream:
        for path in sorted(set(old) | set(changes)):
            change = changes.get(path)
            meta = old.get(path) or old[change['template']]
            info = tarfile.TarInfo(path.lstrip('/') or '.')
            info.uid, info.gid, info.mode, info.mtime = meta['uid'], meta['gid'], stat.S_IMODE(meta['mode']), meta['mtime']
            info.pax_headers = {'SCHILY.xattr.' + k: bytes.fromhex(v).decode('utf-8', 'surrogateescape')
                                for k, v in meta['xattrs'].items()}
            row = {k: meta.get(k) for k in ('mode', 'uid', 'gid', 'xattrs', 'link')}
            if stat.S_ISDIR(meta['mode']):
                info.type = tarfile.DIRTYPE
                stream.addfile(info)
            elif stat.S_ISLNK(meta['mode']):
                info.type, info.linkname = tarfile.SYMTYPE, meta['link']
                stream.addfile(info)
            else:
                need(stat.S_ISREG(meta['mode']), 'Unsupported input node')
                data = change['data'] if change else subprocess.check_output(
                    [str(dump), '--cat', '--path=' + path, str(source)], stderr=subprocess.PIPE, timeout=60)
                need(change is not None or len(data) == meta['size'], 'Original file size mismatch')
                info.size = len(data)
                stream.addfile(info, io.BytesIO(data))
                row.update(bytes=len(data), sha256=sha(data))
            expected[path] = row
    with output.with_suffix('.log').open('wb') as log:
        subprocess.run([str(mkfs), '--quiet', '-b4096', '--tar=f', '-zlz4hc,level=9',
                        '-C4096', '-T1230768000', str(output), str(tar)], check=True, stdout=log, stderr=log)
        subprocess.run([str(fsck), '--extract', str(output)], check=True, stdout=log, stderr=log)
    newfs = ErofsMetadata(output)
    new = newfs.inventory()
    need(set(new) == set(expected), 'Output path set differs')
    regular = 0
    for path, meta in new.items():
        wanted = expected[path]
        need(all(meta.get(k) == wanted[k] for k in ('mode', 'uid', 'gid', 'xattrs', 'link')),
             'Output metadata mismatch: ' + path)
        if 'sha256' in wanted:
            data = subprocess.check_output([str(dump), '--cat', '--path=' + path, str(output)],
                                           stderr=subprocess.PIPE, timeout=60)
            need(len(data) == wanted['bytes'] and sha(data) == wanted['sha256'], 'Output content mismatch: ' + path)
            regular += 1
    oldfs.close()
    newfs.close()
    tar.unlink()
    report = {'pathsVerified': len(new), 'regularFilesReadBack': regular,
              'changedPaths': {p: sha(v['data']) for p, v in changes.items()},
              'allContentModesOwnersXattrsAndLinksVerified': True,
              'timestampsAndInodeNumbersCompared': False, 'outputSha256': file_hash(output)}
    output.with_suffix('.readback.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('system', 'system-ext', 'vndk-apex', 'dump-erofs', 'mkfs-erofs', 'fsck-erofs', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--vndk-apex-sha256', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    need(not args.output.exists(), 'Use a new output directory')
    sources = {'system': args.system.resolve(), 'system_ext': args.system_ext.resolve()}
    fingerprints = {k: inputs.check_image(p, HASHES[k]) for k, p in sources.items()}
    inputs.check_image(args.vndk_apex, args.vndk_apex_sha256)
    apex = args.vndk_apex.read_bytes()
    need(len(apex) < 256 * 1024**2 and sha(apex) == args.vndk_apex_sha256, 'APEX input changed')
    args.output.mkdir(parents=True)
    result = {}
    for name, source in sources.items():
        oldfs = ErofsMetadata(source)
        old = oldfs.inventory()
        if name == 'system':
            path = '/system/apex/com.android.vndk.v30.apex'
            need(path not in old, 'VNDK 30 APEX already present')
            changes = {path: {'data': apex, 'template': '/system/apex/com.android.vndk.current.apex'}}
        else:
            path = '/etc/vintf/manifest.xml'
            original = subprocess.check_output([str(args.dump_erofs), '--cat', '--path=' + path, str(source)], stderr=subprocess.PIPE)
            changes = {path: {'data': add_vndk_manifest(original), 'template': path}}
        oldfs.close()
        result[name] = rebuild(source, args.output / (name + '-vndk30.erofs'), changes,
                               args.dump_erofs.resolve(), args.mkfs_erofs.resolve(), args.fsck_erofs.resolve())
        need(inputs.fingerprint(source) == fingerprints[name], 'Original source changed during build')
        print(json.dumps({'partition': name, **result[name]}), flush=True)
    report = {'sources': HASHES, 'vndkApexSha256': args.vndk_apex_sha256, 'images': result,
              'originalImagesChanged': False, 'containsThirdPartyFiles': True,
              'deviceAccess': False, 'runtimeTested': False, 'deployEligible': False,
              'filebootSystemChangesStillRequired': True}
    (args.output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
