#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build and test the original-code microphone adapter; no OEM binaries required."""
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

    def run(command):
        return subprocess.run([str(x) for x in command], check=True, capture_output=True, text=True)

    def compile(source, target):
        run([args.clang, '--target=armv7a-linux-androideabi30', '-mthumb', '-O2',
             '-fPIC', '-fno-stack-protector', '-ffreestanding', '-fno-builtin',
             '-c', source, '-o', target])

    def link(flags):
        if args.docker_linker:
            run(['docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
                 '--mount', f'type=bind,source={out},target=/work', args.docker_linker,
                 'ld.lld-18', '-shared', '-m', 'armelf_linux_eabi',
                 *[str(x).replace(str(out) + '/', '/work/') for x in flags]])
        else:
            run([args.ld_lld, '-shared', '-m', 'armelf_linux_eabi', *flags])

    # These declarations supply link symbols only. This library is never installed.
    stub = out / 'libdl-link-only.c'
    stub.write_text('void *dlopen(const char *s,int f){return 0;}\n'
                    'void *dlsym(void *h,const char *s){return 0;}\n'
                    'char *dlerror(void){return 0;}\n')
    compile(stub, out / 'libdl-link-only.o')
    link(['-soname', 'libdl.so', out / 'libdl-link-only.o', '-o', out / 'libdl-link-only.so'])
    source = ROOT / 'runtime/voc-primary-compat.c'
    compile(source, out / 'adapter.o')
    link(['--no-undefined', out / 'adapter.o', out / 'libdl-link-only.so',
          '-o', out / 'builtin-mic-primary.so'])
    test = ROOT / 'tests/test-voc-primary-compat.c'
    run([args.clang, '-O1', '-fsanitize=address,undefined', test, '-o', out / 'host-test'])
    result = run([out / 'host-test'])
    if result.stdout.count('case=') != 16 or 'routing exact-key and truncated-input cases PASS' not in result.stdout:
        raise ValueError('Incomplete contract test results')
    (out / 'host-tests.txt').write_text(result.stdout + result.stderr)
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    report = {'sha256': sha(out / 'builtin-mic-primary.so'), 'sourceSha256': sha(source),
              'testSourceSha256': sha(test), 'hostTestsSha256': sha(out / 'host-tests.txt'),
              'hostContractTestsPassed': True, 'deviceAccess': False,
              'oemLibrariesIncluded': False, 'deploymentApproved': False,
              'note': 'Requires the separately validated OEM HAL layout and VAD startup.'}
    (out / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
