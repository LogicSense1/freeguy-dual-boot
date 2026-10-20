#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Create three NEW local ext4 files for an initial installation.

Never opens a TV or an existing image for writing. This is not a restore tool.
Outputs contain the owner's ADB public key and must remain outside the repository.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('runtime_root', ROOT / 'tools/assemble-runtime-root.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
need = runtime.need
IMAGES = (('userdata', 6144, 'system_data_file'),
          ('metadata', 64, 'metadata_file'), ('cache', 64, 'cache_file'))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def metadata(name, root_label):
    result = [('/', stat.S_IFDIR | 0o771, 1000, 1000, root_label)]
    if name == 'userdata':
        result += [('/misc', stat.S_IFDIR | 0o1771, 1000, 9998, 'misc_data_file'),
                   ('/misc/adb', stat.S_IFDIR | 0o2750, 1000, 2000, 'adb_keys_file'),
                   ('/misc/adb/adb_keys', stat.S_IFREG | 0o640, 1000, 2000, 'adb_keys_file')]
    return result


def commands(name, root_label):
    lines = []
    if name == 'userdata':
        lines += ['mkdir /misc', 'mkdir /misc/adb', 'write /work/adb_keys /misc/adb/adb_keys']
    for i, (path, mode, uid, gid, label) in enumerate(metadata(name, root_label)):
        lines += ['set_inode_field ' + path + ' mode 0%o' % mode,
                  'set_inode_field ' + path + ' uid ' + str(uid),
                  'set_inode_field ' + path + ' gid ' + str(gid),
                  'ea_set -f /work/' + name + '-label-' + str(i) + ' ' + path + ' security.selinux']
    return '\n'.join(lines) + '\n'


def check_image(path, name, mib, root_label, key):
    from dissect.extfs import ExtFS
    need(path.is_file() and not path.is_symlink() and path.stat().st_size == mib * 1024**2,
         'Unexpected image size or type')
    with path.open('rb') as stream:
        stream.seek(1024)
        superblock = stream.read(1024)
        need(struct.unpack_from('<H', superblock, 56)[0] == 0xef53, 'Not ext4')
        compat, incompat = struct.unpack_from('<II', superblock, 92)
        need(not compat & 0x1000 and not incompat & 0x2000 and incompat & 0x10000,
             'Unexpected ext4 orphan, metadata checksum seed or encryption features')
        stream.seek(0)
        fs = ExtFS(stream)
        for target, mode, uid, gid, label in metadata(name, root_label):
            node = fs.get(target)
            need((node.inode.i_mode, node.inode.i_uid, node.inode.i_gid) == (mode, uid, gid),
                 'Filesystem metadata readback mismatch')
            attrs = {a.name: a.value for a in node.xattr}
            need(attrs.get('security.selinux') == ('u:object_r:' + label + ':s0\0').encode(),
                 'SELinux label readback mismatch')
        if name == 'userdata':
            need(fs.get('/misc/adb/adb_keys').open().read() == key, 'ADB public key readback mismatch')
    return {'name': name + '.img', 'bytes': path.stat().st_size, 'sha256': digest(path),
            'metadataReadBack': True}


def build(key_path, key_sha256, docker, container, output):
    resolved = output.resolve()
    need(ROOT != resolved and ROOT not in resolved.parents,
         'Private data outputs must stay outside the repository')
    need(not output.exists() and output.parent.is_dir() and not output.is_symlink(),
         'Use a new output directory; existing images are never reused')
    need(',' not in str(output.resolve()) and '\n' not in str(output.resolve()), 'Unsupported mount path')
    need(re.fullmatch('[A-Za-z0-9][A-Za-z0-9._/:@-]*', container), 'Invalid local container reference')
    need(key_path.is_file() and not key_path.is_symlink() and key_path.stat().st_size < 4096,
         'Expected a local Android ADB public key file')
    key = key_path.read_bytes()
    runtime.validate_adb_key(key)
    need(re.fullmatch('[0-9a-f]{64}', key_sha256) and hashlib.sha256(key).hexdigest() == key_sha256,
         'ADB public key hash mismatch')
    need(shutil.disk_usage(output.parent).free > sum(m * 1024**2 for _, m, _ in IMAGES) + 2 * 1024**3,
         'Insufficient local disk reserve')
    # Fail before creating files if the reader dependency is unavailable.
    from dissect.extfs import ExtFS
    os.umask(0o077)
    output.mkdir(mode=0o700)
    (output / 'adb_keys').write_bytes(key)
    report = {'complete': False, 'deviceAccess': False, 'existingImagesChanged': False,
              'privateOutput': True, 'adbPublicKeySha256': key_sha256, 'images': []}
    for name, mib, label in IMAGES:
        path = output / (name + '.img')
        with path.open('xb') as stream:
            stream.truncate(mib * 1024**2)
        for i, (_, _, _, _, value) in enumerate(metadata(name, label)):
            (output / (name + '-label-' + str(i))).write_bytes(('u:object_r:' + value + ':s0\0').encode())
        (output / (name + '.commands')).write_text(commands(name, label))
        script = ('set -eu\n'
                  'mke2fs -q -t ext4 -b 4096 -I 256 -m 0 '
                  '-O ^orphan_file,^metadata_csum_seed,encrypt '
                  '-E lazy_itable_init=0,lazy_journal_init=0 /work/' + name + '.img\n'
                  'debugfs -w -f /work/' + name + '.commands /work/' + name + '.img\n'
                  'e2fsck -fn /work/' + name + '.img\n')
        with (output / (name + '.build.log')).open('wb') as log:
            subprocess.run([str(docker), 'run', '--rm', '--pull=never', '--network=none', '--read-only',
                            '--mount', 'type=bind,source=' + str(output.resolve()) + ',target=/work',
                            container, 'sh', '-ec', script], stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=600)
        with path.open('rb') as stream:
            os.fsync(stream.fileno())
        report['images'].append(check_image(path, name, mib, label, key))
        (output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
        print('Created and verified fresh ' + name + '.img', flush=True)
    report['complete'] = True
    (output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('adb-public-key', 'docker', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--adb-public-key-sha256', required=True)
    p.add_argument('--container', required=True, help='Existing local e2fsprogs image; never pulled')
    a = p.parse_args()
    r = build(a.adb_public_key, a.adb_public_key_sha256, a.docker, a.container, a.output)
    print(json.dumps({'complete': r['complete'], 'images': len(r['images']), 'deviceAccess': False}))


if __name__ == '__main__':
    main()
