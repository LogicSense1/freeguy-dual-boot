#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build HDMI/CEC service and native-menu resource overlay, without device access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def defined_types(raw):
    if len(raw) < 112 or raw[:4] != b'dex\n' or raw[7] != 0:
        raise ValueError('Expected standard DEX')
    word = lambda offset: struct.unpack_from('<I', raw, offset)[0]
    if word(32) != len(raw) or word(40) != 0x12345678:
        raise ValueError('Invalid DEX size or byte order')
    strings, string_offset = word(56), word(60)
    types, type_offset = word(64), word(68)
    classes, class_offset = word(96), word(100)
    for count, offset, width in ((strings, string_offset, 4), (types, type_offset, 4),
                                 (classes, class_offset, 32)):
        if offset < 112 or offset + count * width > len(raw):
            raise ValueError('Invalid DEX table bounds')
    names = []
    for n in range(classes):
        type_id = word(class_offset + n * 32)
        if type_id >= types:
            raise ValueError('Invalid class type')
        string_id = word(type_offset + type_id * 4)
        if string_id >= strings:
            raise ValueError('Invalid class name')
        offset = word(string_offset + string_id * 4)
        for _ in range(5):
            if offset >= len(raw):
                raise ValueError('Truncated DEX string')
            byte = raw[offset]
            offset += 1
            if not byte & 128:
                break
        else:
            raise ValueError('Invalid string length')
        end = raw.find(b'\0', offset)
        if end < 0:
            raise ValueError('Missing string terminator')
        names.append(raw[offset:end].decode('ascii'))
    return names


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('android-jar', 'aapt2', 'd8-jar', 'apksigner-jar', 'java-home',
                 'keystore', 'keystore-password-file', 'output'):
        p.add_argument('--' + name, required=True, type=Path)
    args = p.parse_args()
    if args.output.exists():
        p.error('Use a new output directory')
    password = args.keystore_password_file
    if password.is_symlink() or not password.is_file() or password.stat().st_mode & 0o077:
        p.error('Keystore password file must be private (0600 or stricter)')
    for file in (args.android_jar, args.aapt2, args.d8_jar, args.apksigner_jar, args.keystore):
        if not file.is_file():
            p.error('Missing local build input: ' + file.name)
    os.umask(0o077)
    out = args.output.resolve()
    out.mkdir(parents=True)
    java = args.java_home / 'bin'

    def run(command):
        return subprocess.run([str(v) for v in command], check=True, capture_output=True, text=True)

    def sign(unsigned, target):
        run([java / 'java', '-jar', args.apksigner_jar, 'sign', '--ks', args.keystore,
             '--ks-pass', 'file:' + str(password.resolve()), '--out', target, unsigned])
        run([java / 'java', '-jar', args.apksigner_jar, 'verify', target])

    source = ROOT / 'hdmi'
    for n in ('classes', 'dex', 'tests'):
        (out / n).mkdir()
    run([java / 'javac', '--release', '8', '-cp', args.android_jar, '-d', out / 'classes',
         *sorted((source / 'stubs').rglob('*.java')), *sorted((source / 'src').glob('*.java'))])
    # Only our package is emitted. Android declarations are compile-time stubs.
    real = sorted((out / 'classes/local').rglob('*.class'))
    run([java / 'java', '-cp', args.d8_jar, 'com.android.tools.r8.D8', '--min-api', '30',
         '--lib', args.android_jar, '--classpath', out / 'classes', '--output', out / 'dex', *real])
    dex = (out / 'dex/classes.dex').read_bytes()
    names = defined_types(dex)
    # D8 adds this annotation to desugared lambda methods in the accepted build.
    compiler_types = {'Lcom/android/tools/r8/annotations/LambdaMethod;'}
    if not names or any(not n.startswith('Llocal/codex/hdmi/') and n not in compiler_types for n in names):
        raise ValueError('Unexpected implementation classes in DEX')
    run([java / 'javac', '--release', '8', '-cp', out / 'classes', '-d', out / 'tests',
         *sorted((source / 'tests').glob('*.java'))])
    tests = []
    for name in ('CecKeyGateTest', 'SignalRefreshGateTest'):
        result = run([java / 'java', '-cp', str(out / 'classes') + os.pathsep + str(out / 'tests'),
                      'local.codex.hdmi.' + name])
        if not result.stdout.startswith('PASS'):
            raise ValueError('Incomplete HDMI tests')
        tests.append(result.stdout.strip())

    for folder, apk, code in ((source, 'OriginalHdmi.apk', True),
                               (ROOT / 'hdmi-label', 'NativeHdmiLabel.apk', False)):
        prefix = out / apk.removesuffix('.apk')
        prefix.mkdir()
        resources = prefix / 'resources.zip'
        unsigned = prefix / 'unsigned.apk'
        run([args.aapt2, 'compile', '--dir', folder / 'res', '-o', resources])
        extra = [] if code else ['--no-resource-deduping', '--no-resource-removal']
        run([args.aapt2, 'link', '-I', args.android_jar, '--manifest', folder / 'AndroidManifest.xml',
             *extra, '-o', unsigned, resources])
        if code:
            with zipfile.ZipFile(unsigned, 'a') as z:
                z.writestr('classes.dex', dex, compress_type=zipfile.ZIP_DEFLATED)
        sign(unsigned, out / apk)
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    report = {'hdmiSha256': sha(out / 'OriginalHdmi.apk'),
              'labelSha256': sha(out / 'NativeHdmiLabel.apk'), 'dexSha256': sha(out / 'dex/classes.dex'),
              'definedClasses': names, 'compileStubsExcluded': True, 'tests': tests,
              'signaturesVerified': True, 'deployed': False, 'runtimeVerified': False}
    (out / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'definedClasses'}))


if __name__ == '__main__':
    main()
