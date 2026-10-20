#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build a local ARM entry from an explicitly supplied, hashed RAM filesystem.

No device I/O. The resulting loader contains firmware and local configuration;
keep it private. This is one build stage, not a complete firmware assembler.
"""
import argparse
import gzip
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 96 * 1024 * 1024


def need(ok, message):
    if not ok:
        raise ValueError(message)


def entries(data):
    need(len(data) <= MAX_BYTES, 'Archive exceeds bound')
    pos, result, names = 0, [], set()
    while pos + 110 <= len(data):
        header = data[pos:pos + 110]
        need(header[:6] == b'070701', 'Expected newc archive')
        fields = [int(header[6 + i * 8:14 + i * 8], 16) for i in range(13)]
        size, namesize = fields[6], fields[11]
        need(1 < namesize <= 256 and pos + 110 + namesize <= len(data), 'Invalid entry name')
        raw = data[pos + 110:pos + 110 + namesize]
        need(raw[-1:] == b'\0' and b'\0' not in raw[:-1], 'Invalid entry name terminator')
        name = raw[:-1].decode('ascii')
        need(all(33 <= ord(c) < 127 for c in name), 'Nonprintable or ambiguous path')
        begin = (pos + 110 + namesize + 3) & ~3
        need(begin + size <= len(data), 'Truncated entry')
        content = data[begin:begin + size]
        pos = (begin + size + 3) & ~3
        if name == 'TRAILER!!!':
            need(size == 0 and not any(data[pos:]), 'Invalid archive trailer')
            need(result, 'Empty archive')
            return result
        need(name not in names and not name.startswith('/') and
             all(part not in ('', '.', '..') for part in name.split('/')), 'Duplicate or unsafe path')
        names.add(name)
        kind = stat.S_IFMT(fields[1])
        need(kind in (stat.S_IFDIR, stat.S_IFREG, stat.S_IFLNK), 'Special nodes are not accepted')
        if kind == stat.S_IFREG:
            need(fields[4] == 1, 'Hard links are not accepted')
        if kind == stat.S_IFDIR:
            need(size == 0, 'Directory contains data')
        if kind == stat.S_IFLNK:
            need(0 < size < 256 and b'\0' not in content, 'Invalid symlink target')
        need(len(result) < 8192, 'Too many entries')
        result.append({'name': name, 'fields': fields, 'data': content})
    raise ValueError('Missing archive trailer')


def table(items):
    items = sorted(items, key=lambda e: (not stat.S_ISDIR(e['fields'][1]),
                                        e['name'].count('/'), e['name']))
    directories, blob, rows, index = set(), bytearray(), [], []
    for e in items:
        name, f, content = e['name'], e['fields'], e['data']
        parent = name.rpartition('/')[0]
        need(not parent or parent in directories, 'Parent must be a declared directory')
        if stat.S_ISDIR(f[1]):
            directories.add(name)
        offset = len(blob)
        blob.extend(content)
        if stat.S_ISLNK(f[1]):
            blob.append(0)
        need(len(blob) < MAX_BYTES, 'Embedded filesystem exceeds bound')
        rows.append('{' + json.dumps(name) + ',' +
                    ','.join(str(v) for v in (f[1], f[2], f[3], offset, len(content))) + '},')
        index.append({'name': name, 'mode': f[1], 'uid': f[2], 'gid': f[3],
                      'offset': offset, 'bytes': len(content),
                      'sha256': hashlib.sha256(content).hexdigest()})
    return bytes(blob), rows, index


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ramdisk', required=True, type=Path)
    p.add_argument('--ramdisk-sha256', required=True)
    p.add_argument('--owner-capture-build', type=Path,
                   help='Use an audited owner-capture root instead of the Google runtime')
    p.add_argument('--stock-boot', type=Path,
                   help='Exact original B image, required only with --owner-capture-build')
    p.add_argument('--clang', required=True, type=Path)
    link = p.add_mutually_exclusive_group(required=True)
    link.add_argument('--ld-lld', type=Path)
    link.add_argument('--docker-linker', help='Existing local image containing ld.lld-18')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    os.umask(0o077)
    need(re.fullmatch('[0-9a-f]{64}', args.ramdisk_sha256), 'Invalid input hash')
    need(args.ramdisk.is_file() and args.ramdisk.stat().st_size <= MAX_BYTES, 'Invalid input size')
    source = args.ramdisk.read_bytes()
    sha = lambda b: hashlib.sha256(b).hexdigest()
    need(sha(source) == args.ramdisk_sha256, 'Input does not match expected hash')
    if source.startswith(b'\x1f\x8b'):
        # Decompress the bytes whose hash was checked, not a second path read.
        with gzip.GzipFile(fileobj=io.BytesIO(source)) as f:
            raw = f.read(MAX_BYTES + 1)
    else:
        raw = source
    need(len(raw) <= MAX_BYTES, 'Expanded input exceeds bound')
    items = entries(raw)
    byname = {e['name']: e for e in items}
    need('init' in byname and stat.S_ISLNK(byname['init']['fields'][1]) and
         byname['init']['data'] == b'/system/bin/init', 'Expected dispatcher symlink')
    need(bool(args.stock_boot)==bool(args.owner_capture_build),
         'Owner-capture builds require both the capture build and original stock boot')
    required=('system/bin/init', 'system/bin/stock-init')
    if args.owner_capture_build:
        spec=importlib.util.spec_from_file_location('owner_root',ROOT/'tools/build-owner-root.py')
        owner=importlib.util.module_from_spec(spec);spec.loader.exec_module(owner)
        expected,owner_info=owner.prepare(args.stock_boot.read_bytes(),args.owner_capture_build)
        need(table(items)[2]==table(expected)[2],'Owner root differs from exact stock/capture assembly')
    else:
        required+=('codex/entry.sh','codex/helper','codex/trial-guard')
    for name in required:
        need(name in byname and stat.S_ISREG(byname[name]['fields'][1]), 'Missing runtime entry: ' + name)
    blob, rows, index = table(items)
    need(not args.output.exists(), 'Use a new output directory')
    out = args.output.resolve()
    out.mkdir(parents=True)
    (out / 'stock-root.bin').write_bytes(blob)
    (out / 'stock-root-table.h').write_text('#define ROW_COUNT ' + str(len(rows)) +
        '\nstatic const struct row rows[]={\n' + '\n'.join(rows) + '\n};\n')
    (out / 'stock-root.S').write_text('.section .rodata\n.global stock_blob\nstock_blob:\n'
        '.incbin "stock-root.bin"\n.section .note.GNU-stack,"",%progbits\n')
    code = ROOT / 'runtime'
    flags = [args.clang, '--target=armv7a-linux-gnueabi', '-marm', '-Oz', '-ffreestanding',
             '-fno-builtin', '-fno-stack-protector', '-fno-unwind-tables',
             '-fno-asynchronous-unwind-tables', '-mfloat-abi=soft', '-mfpu=none']
    defines = ['-DRESTORE_ENV', '-DTRIAL_INIT="init=/local/tmp/rd-root/envdaily"',
               '-DROOT_TMPFS_OPTIONS="mode=0755,size=192m"',
               '-DROOT_READY_MESSAGE="<6>CTV_GOOGLE_ROOT_ACCEPTED_RAMDISK_RECONSTRUCTED\\n"',
               '-DROOT_EXEC_MESSAGE="<6>CTV_GOOGLE_ROOT_EXEC_ACCEPTED_DISPATCHER\\n"']
    if args.owner_capture_build:
        defines[-2:]=['-DROOT_READY_MESSAGE="<6>FREEGUY_OWNER_ROOT_RECONSTRUCTED\\n"',
                      '-DROOT_EXEC_MESSAGE="<6>FREEGUY_OWNER_ROOT_EXEC\\n"']

    def run(command, **kw):
        subprocess.run([str(v) for v in command], check=True, capture_output=True, **kw)

    run(flags + defines + ['-I', out, '-I', code, '-c', code / 'loader.c', '-o', out / 'loader.o'])
    run(flags + ['-c', 'stock-root.S', '-o', 'stock-root.o'], cwd=out)
    if args.docker_linker:
        need(re.fullmatch('[A-Za-z0-9][A-Za-z0-9._/:@-]*', args.docker_linker), 'Invalid local image reference')
        run(['docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
             '--mount', f'type=bind,source={out},target=/work', args.docker_linker,
             'ld.lld-18', '-static', '-m', 'armelf_linux_eabi', '-e', '_start',
             '/work/loader.o', '/work/stock-root.o', '-o', '/work/loader'])
    else:
        run([args.ld_lld, '-static', '-m', 'armelf_linux_eabi', '-e', '_start',
             out / 'loader.o', out / 'stock-root.o', '-o', out / 'loader'])
    manifest = {'sha256': sha((out / 'loader').read_bytes()), 'sourceSha256': sha((code / 'loader.c').read_bytes()),
                'inputRamdiskSha256': sha(source), 'embeddedBlobSha256': sha(blob),
                'entries': index, 'deployEligible': False, 'liveTested': False,
                'commandLineLogging': 'length-only',
                'componentSources': {n: sha((code / n).read_bytes()) for n in
                                     ('internal-env-inspect.h', 'internal-env-transform.c', 'internal-env-once.h')}}
    if args.owner_capture_build:
        manifest.update(rootKind='owner-capture',ownerCapture=owner_info)
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'sha256': manifest['sha256'], 'entries': len(index),
                      'deployEligible': False, 'firmwareContainedInLocalOutput': True}))


if __name__ == '__main__':
    main()
