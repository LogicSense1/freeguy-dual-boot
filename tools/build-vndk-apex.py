#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Package local stock VNDK files into a privately signed built-in APEX.

The signer is the owner's local key, never an OEM key. No TV access or stock
installation. Libraries, generated APEX and signing outputs must stay private.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import zipfile

from dissect.extfs import ExtFS

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / file)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


vndk = load('vndk', 'extract-vndk30.py')
vendor = load('vendor', 'build-vendor-image.py')
need = vendor.need
sha = lambda data: hashlib.sha256(data).hexdigest()
TREE_SHA = 'd5aa5bbec3b2a887bd51c2f214611c24a2321ececb147c2cec943f3f333c9bc2'


def read_sources(manifest, base):
    need(manifest.get('sourceImageSha256') == vndk.STOCK_SHA and
         manifest.get('sourcePath') == vndk.SOURCE and
         manifest.get('package') == 'com.android.vndk.v30' and
         manifest.get('format') == 'flattened', 'Unexpected VNDK source')
    rows = manifest.get('files')
    need(isinstance(rows, list) and 1 < len(rows) <= 2048, 'Invalid file list')
    need(base.is_dir() and not base.is_symlink(), 'Expected regular source directory')
    files, total = {}, 0
    for row in rows:
        name = row.get('path')
        need(isinstance(name, str) and re.fullmatch('[A-Za-z0-9_.+@/-]+', name) and
             all(part not in ('', '.', '..') for part in name.split('/')) and name not in files,
             'Unsafe or duplicate VNDK path')
        need(type(row.get('bytes')) is int and 0 <= row['bytes'] <= 32 * 1024**2,
             'Invalid source size')
        path = base / name
        need(all(not p.is_symlink() for p in (path, *path.parents) if p == base or base in p.parents),
             'Symlink in source path')
        data = vendor.board.components.read_input({'path': str(path.resolve()), 'sha256': row.get('sha256')}, Path('/'))
        need(len(data) == row['bytes'], 'Source size mismatch')
        total += len(data)
        need(total <= 256 * 1024**2, 'Source tree exceeds bound')
        files[name] = data
    need(files.get('apex_manifest.pb') == vndk.MANIFEST and 'apex_pubkey' in files,
         'Expected original flattened package metadata')
    need(any(name.startswith('lib/') for name in files), 'Missing VNDK libraries')
    roster = sorted((name, len(data), sha(data)) for name, data in files.items())
    need(sha(json.dumps(roster, separators=(',', ':')).encode()) == TREE_SHA,
         'VNDK tree differs from the supported stock revision')
    return files


def stored_entry(zipfile_out, name, data, alignment=4):
    need(name not in zipfile_out.namelist(), 'Duplicate container path')
    info = zipfile.ZipInfo(name, (2009, 1, 1, 0, 0, 0))
    offset = zipfile_out.fp.tell() + 30 + len(name.encode())
    pad = (-offset) % alignment
    if 0 < pad < 4:
        pad += alignment
    if pad:
        info.extra = struct.pack('<HH', 0xd935, pad - 4) + b'\0' * (pad - 4)
    info.external_attr = 0o100644 << 16
    zipfile_out.writestr(info, data)


def inspect_container(path, payload_hash, public_key, manifest):
    with zipfile.ZipFile(path) as z, path.open('rb') as raw:
        names = z.namelist()
        need(len(names) == len(set(names)) and set(names) ==
             {'apex_payload.img', 'apex_pubkey', 'apex_manifest.pb', 'AndroidManifest.xml', 'resources.arsc'},
             'Unexpected APEX entries')
        part = z.getinfo('apex_payload.img')
        raw.seek(part.header_offset)
        header = raw.read(30)
        need(header[:4] == b'PK\x03\x04', 'Invalid local ZIP header')
        name_len, extra_len = struct.unpack_from('<HH', header, 26)
        offset = part.header_offset + 30 + name_len + extra_len
        need(offset % 4096 == 0 and part.compress_type == zipfile.ZIP_STORED,
             'APEX payload must be stored and page-aligned')
        need(sha(z.read(part)) == payload_hash and z.read('apex_pubkey') == public_key and
             z.read('apex_manifest.pb') == manifest, 'APEX payload or metadata mismatch')
    return offset


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('input-manifest', 'input-directory', 'avb-key', 'certificate', 'avbtool',
                 'aapt2', 'android-jar', 'apksigner-jar', 'java', 'output'):
        p.add_argument('--' + name, required=True, type=Path)
    p.add_argument('--input-manifest-sha256', required=True)
    p.add_argument('--docker-image', required=True)
    args = p.parse_args()
    os.umask(0o077)
    need(not args.output.exists(), 'Use a new output directory')
    need(args.avb_key.is_file() and not args.avb_key.is_symlink() and
         args.avb_key.stat().st_mode & 0o077 == 0, 'Signing key must be private')
    need(re.fullmatch('[A-Za-z0-9][A-Za-z0-9._/:@-]*', args.docker_image), 'Invalid local tool image')
    manifest_bytes = vendor.board.components.read_input(
        {'path': str(args.input_manifest.resolve()), 'sha256': args.input_manifest_sha256}, Path('/'))
    need(len(manifest_bytes) < 2 * 1024**2, 'Source manifest exceeds bound')
    files = read_sources(json.loads(manifest_bytes), args.input_directory.resolve())
    tool_files = (args.avbtool, args.aapt2, args.android_jar, args.apksigner_jar, args.java, args.certificate)
    need(all(p.is_file() for p in tool_files), 'Missing local tool or certificate')
    args.output.mkdir(parents=True)
    out = args.output.resolve()
    with (out / 'build.log').open('wb') as log:
        def run(command):
            subprocess.run([str(x) for x in command], check=True, stdout=log, stderr=log, stdin=subprocess.DEVNULL)

        # Check that the provided certificate and AVB key have the same public
        # key. Do not print the key or include private key material in reports.
        key_pub = subprocess.check_output(['openssl', 'pkey', '-in', str(args.avb_key), '-pubout'], stderr=subprocess.PIPE)
        cert_pub = subprocess.check_output(['openssl', 'x509', '-in', str(args.certificate), '-pubkey', '-noout'], stderr=subprocess.PIPE)
        need(key_pub == cert_pub, 'Certificate and signing key do not match')
        root = out / 'payload-root'
        root.mkdir()
        for name, data in files.items():
            if name == 'apex_pubkey':
                continue
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o644)
        paths = [root, *sorted(root.rglob('*'))]
        expected, commands = {}, []
        for path in paths:
            name = '/' if path == root else '/' + path.relative_to(root).as_posix()
            mode = 0o40755 if path.is_dir() else 0o100644
            label = ('u:object_r:' + ('system_lib_file' if name.startswith('/lib/') else 'system_file') + ':s0').encode()
            expected[name] = {'mode': mode, 'uid': 1000, 'gid': 1000,
                              'xattrs': {'security.selinux': label.hex()}}
            if path.is_file():
                data = files[name[1:]]
                expected[name].update(bytes=len(data), sha256=sha(data))
            commands.extend(['set_inode_field ' + name + ' uid 1000',
                'set_inode_field ' + name + ' gid 1000',
                'set_inode_field ' + name + ' mode 0%o' % mode,
                'ea_set ' + name + ' security.selinux ' + label.decode()])
        expected['/lost+found'] = {'mode': 0o40700, 'uid': 0, 'gid': 0, 'xattrs': {}}
        (out / 'metadata.debugfs').write_text('\n'.join(commands) + '\n')
        docker = ['docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
                  '--mount', 'type=bind,source=' + str(out) + ',target=/out', args.docker_image]
        run(docker + ['mke2fs', '-q', '-F', '-t', 'ext4', '-b', '4096', '-I', '256', '-m', '0',
                      '-O', '^has_journal,^orphan_file,^metadata_csum_seed', '-d', '/out/payload-root',
                      '/out/apex_payload.img', '64M'])
        run(docker + ['debugfs', '-w', '-f', '/out/metadata.debugfs', '/out/apex_payload.img'])
        run(docker + ['e2fsck', '-fn', '/out/apex_payload.img'])
        payload = out / 'apex_payload.img'
        with payload.open('rb') as stream:
            actual = vendor.inventory(ExtFS(stream))
        need(actual == expected, 'APEX filesystem content or metadata mismatch')
        avb = [sys.executable, args.avbtool]
        pubkey = out / 'apex_pubkey'
        run(avb + ['extract_public_key', '--key', args.avb_key, '--output', pubkey])
        run(avb + ['add_hashtree_footer', '--image', payload, '--algorithm', 'SHA256_RSA4096',
                   '--hash_algorithm', 'sha256', '--key', args.avb_key, '--prop', 'apex.key:com.android.vndk.v30',
                   '--salt', sha(files['apex_manifest.pb']), '--do_not_generate_fec'])
        run(avb + ['verify_image', '--image', payload, '--key', args.avb_key])
        # Signing adds data after the ext4 payload, not a change to its tree.
        with payload.open('rb') as stream:
            need(vendor.inventory(ExtFS(stream)) == expected, 'Filesystem changed during signing')
        xml = out / 'AndroidManifest.xml'
        xml.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android"\n'
            'package="com.android.vndk.v30" android:versionCode="1">\n'
            '<uses-sdk android:minSdkVersion="29" android:targetSdkVersion="30"/>\n'
            '<application android:hasCode="false"/></manifest>\n')
        run([args.aapt2, 'link', '--manifest', xml, '-I', args.android_jar, '-o', out / 'container.apk'])
        unsigned = out / 'unsigned.apex'
        with zipfile.ZipFile(unsigned, 'w') as z:
            stored_entry(z, 'apex_payload.img', payload.read_bytes(), 4096)
            stored_entry(z, 'apex_pubkey', pubkey.read_bytes())
            stored_entry(z, 'apex_manifest.pb', files['apex_manifest.pb'])
            with zipfile.ZipFile(out / 'container.apk') as apk:
                for name in apk.namelist():
                    stored_entry(z, name, apk.read(name))
        pk8 = out / 'container.pk8'
        run(['openssl', 'pkcs8', '-topk8', '-nocrypt', '-in', args.avb_key, '-outform', 'DER', '-out', pk8])
        output = out / 'com.android.vndk.v30.apex'
        run([args.java, '-jar', args.apksigner_jar, 'sign', '--key', pk8, '--cert', args.certificate,
             '--v1-signing-enabled', 'false', '--v2-signing-enabled', 'true', '--v3-signing-enabled', 'false',
             '--out', output, unsigned])
        run([args.java, '-jar', args.apksigner_jar, 'verify', '--verbose', output])
        payload_hash = sha(payload.read_bytes())
        offset = inspect_container(output, payload_hash, pubkey.read_bytes(), files['apex_manifest.pb'])
    report = {'inputManifestSha256': sha(manifest_bytes), 'sourceFilesVerified': len(files),
              'payloadPathsVerifiedBeforeAndAfterSigning': len(expected),
              'outputSha256': sha(output.read_bytes()), 'payloadSha256': payload_hash,
              'payloadOffset': offset, 'publicKeySha256': sha(pubkey.read_bytes()),
              'avbSignatureVerified': True, 'apkSignatureVerified': True,
              'signer': 'Owner local key; not Xiaomi or Google', 'containsThirdPartyFiles': True,
              'privateSigningFileInOutputDirectory': True, 'deviceAccess': False,
              'runtimeTested': False, 'deployEligible': False}
    (out / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('sourceFilesVerified', 'payloadPathsVerifiedBeforeAndAfterSigning',
                                           'outputSha256', 'deployEligible')}))


if __name__ == '__main__':
    main()
