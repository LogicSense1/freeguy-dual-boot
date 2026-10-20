#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the standalone ARM runtime helper. No device access or firmware input."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--clang', type=Path, required=True)
    link = p.add_mutually_exclusive_group(required=True)
    link.add_argument('--ld-lld', type=Path)
    link.add_argument('--docker-linker', help='Existing local image with ld.lld-18')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error('Use a new output directory')
    if args.docker_linker and not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._/:@-]*', args.docker_linker):
        p.error('Invalid image reference')
    os.umask(0o077)
    out = args.output.resolve()
    out.mkdir(parents=True)
    code = ROOT / 'runtime'
    sources = {n: hashlib.sha256((code / n).read_bytes()).hexdigest()
               for n in ('fileboot-helper.c', 'watchdog-policy.h')}

    def run(command):
        subprocess.run([str(x) for x in command], check=True)

    run([args.clang, '--target=armv7a-linux-gnueabi', '-marm', '-O2',
         '-ffreestanding', '-fno-builtin', '-fno-stack-protector',
         '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
         '-mfloat-abi=soft', '-mfpu=none', '-c', code / 'fileboot-helper.c',
         '-o', out / 'helper.o'])
    flags = ['-static', '-m', 'armelf_linux_eabi', '-e', '_start']
    if args.docker_linker:
        run(['docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
             '--mount', f'type=bind,source={out},target=/work', args.docker_linker,
             'ld.lld-18', *flags, '/work/helper.o', '-o', '/work/helper'])
    else:
        run([args.ld_lld, *flags, out / 'helper.o', '-o', out / 'helper'])
    raw = (out / 'helper').read_bytes()
    if raw[:6] != b'\x7fELF\x01\x01':
        raise ValueError('Expected little-endian ELF32 output')
    # Bootstrap uses the existing bounded watchdog mode; daily operation uses
    # watchdog-daily. Keep distinct paths so handover can stop only the former.
    (out / 'trial-guard').write_bytes(raw)
    (out / 'trial-guard').chmod(0o700)
    report = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
              'sources': sources, 'deviceAccess': False, 'liveTested': False,
              'transitionGuard': {'file': 'trial-guard', 'argument': 'watchdog',
                                  'sha256': hashlib.sha256(raw).hexdigest(),
                                  'sameSourceAsHelper': True, 'liveTested': False}}
    (out / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'sha256': report['sha256'], 'bytes': len(raw), 'deviceAccess': False}))


if __name__ == '__main__':
    main()
