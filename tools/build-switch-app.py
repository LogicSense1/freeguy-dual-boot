#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the switch app from source and an owner's local validation profile.

This command has no device access. Firmware, profiles, keys and build outputs
are local inputs, not distributable parts of the source repository.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = '/data/local/tmp/codex-gtv-fileboot-v1/'
IMAGES = ('system.img', 'vendor.img', 'system_ext.img', 'product.img',
          'tvservice.img', 'linux_rootfs_a.img', '3rd_a.img', 'tvconfig_b.img',
          'bootdata_b.img', 'project_id.img', 'factory_a_b.img')
MUTABLE = ('uenv.img', 'uuenv.img', 'persist.img', 'tvcertificate.img',
           'userdata.img', 'metadata.img', 'cache.img')
EXPECTED = {BASE + n for n in IMAGES} | {
    '/data/local/tmp/rd-root/envdaily',
    BASE + 'voice-assets-v1/oem.apk', BASE + 'voice-assets-v1/libmibluetooth_jni.so'}


def need(ok, message):
    if not ok:
        raise ValueError(message)


def pins(profile, daily_default):
    need(profile.get('model') == 'L75MA-SPL' and profile.get('device') == 'freeguy',
         'This source profile supports only L75MA-SPL/freeguy')
    need(profile.get('bootloader') == '01.01.250225', 'Unsupported bootloader')
    files = profile.get('immutableFiles', {})
    need(set(files) == EXPECTED, 'The complete runtime dependency list is required')
    normalized = profile.get('normalizedBootargsSha256', [])
    need(isinstance(normalized, list) and 1 <= len(normalized) <= 4,
         'Expected locally validated boot configuration hashes')
    need(all(isinstance(h, str) and re.fullmatch('[0-9a-f]{64}', h)
             for h in [*files.values(), *normalized]), 'Invalid SHA-256')
    need(profile.get('serialUndoVerified') is True, 'Serial undo has not been recorded')
    if daily_default:
        test = profile.get('dailyRuntime', {})
        matching = test.get('passed') is True and (
            test.get('loaderSha256') == files['/data/local/tmp/rd-root/envdaily'])
        long_run = test.get('method', 'long-runtime') == 'long-runtime' and (
            test.get('awakeSeconds', 0) > 3600)
        clock_model = test.get('method') == 'binary-clock-model' and all(
            test.get(k) is True for k in (
                'liveHelperHashMatched', 'liveDailyModeConfirmed',
                'transitionGuardAbsent', 'healthyHourPassed', 'healthyMonthPassed',
                'startupFailurePassed', 'healthFailurePassed')) and (
            isinstance(test.get('helperSha256'), str) and
            re.fullmatch('[0-9a-f]{64}', test['helperSha256']))
        need(matching and (long_run or clock_model),
             'Daily default requires matching runtime or binary-clock-model evidence')
    quote = lambda x: json.dumps(x, ensure_ascii=True)
    rows = ',\n'.join('  {' + quote(p) + ',' + quote(files[p]) + '}' for p in sorted(files))
    values = ','.join(quote(h) for h in normalized)
    mutable = ','.join(quote(BASE + n) for n in MUTABLE)
    return ('package local.codex.tvswitchprobe;\nfinal class AssetPins {\n'
            ' static final boolean ENABLE_ENTER_GOOGLE=true;\n'
            f' static final boolean ENABLE_PERSISTENT_DEFAULT={str(daily_default).lower()};\n'
            ' static final String INIT_NAME="envdaily";\n'
            ' static final int APPENDED_CMDLINE_BYTES=626;\n'
            f' static final String[] NORMALIZED_BOOTARGS={{{values}}};\n'
            f' static final String[][] FILES={{\n{rows}\n }};\n'
            f' static final String[] MUTABLE={{{mutable}}};\n}}\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', required=True, type=Path)
    p.add_argument('--android-jar', required=True, type=Path)
    p.add_argument('--aapt2', required=True, type=Path)
    p.add_argument('--d8-jar', required=True, type=Path)
    p.add_argument('--apksigner-jar', required=True, type=Path)
    p.add_argument('--java-home', required=True, type=Path)
    p.add_argument('--keystore', required=True, type=Path)
    p.add_argument('--keystore-password-file', required=True, type=Path)
    p.add_argument('--version-code', required=True, type=int)
    p.add_argument('--daily-default', action='store_true')
    p.add_argument('--launcher-style', choices=('stock', 'google'), default='stock',
                   help='Google uses a circular/adaptive icon; stock keeps the original artwork')
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    os.umask(0o077)
    need(1 <= args.version_code <= 2100000000, 'Invalid version code')
    need(not args.keystore_password_file.is_symlink() and
         args.keystore_password_file.is_file() and
         args.keystore_password_file.stat().st_mode & 0o077 == 0,
         'Keystore password file must be private (0600 or stricter)')
    for file in (args.profile, args.android_jar, args.aapt2, args.d8_jar,
                 args.apksigner_jar, args.keystore):
        need(file.is_file(), 'Missing local build input: ' + file.name)
    generated = pins(json.loads(args.profile.read_text()), args.daily_default)
    need(not args.output.exists(), 'Use a new output directory; previous builds are preserved')
    out = args.output.resolve()
    out.mkdir(parents=True)
    for name in ('classes', 'dex', 'generated'):
        (out / name).mkdir()
    java = args.java_home / 'bin'
    source = ROOT / 'app'
    (out / 'generated/AssetPins.java').write_text(generated)
    manifest = (source / 'AndroidManifest.xml').read_text()
    manifest, n = re.subn(r'android:versionCode="\d+"',
                         f'android:versionCode="{args.version_code}"', manifest)
    need(n == 1, 'Unexpected manifest version')
    resources = source / 'res'
    if args.launcher_style == 'google':
        resources = out / 'res'
        shutil.copytree(source / 'res', resources)
        shutil.copytree(source / 'res-google', resources, dirs_exist_ok=True)
        manifest, n = re.subn(r'android:icon="@drawable/ic_switch"',
                             'android:icon="@mipmap/ic_switch_google" '
                             'android:roundIcon="@mipmap/ic_switch_google"', manifest)
        need(n == 1, 'Unexpected application icon')
    (out / 'AndroidManifest.xml').write_text(manifest)

    def run(command):
        # Do not echo commands or the password file's contents.
        subprocess.run([str(v) for v in command], check=True, capture_output=True)

    run([java / 'javac', '--release', '8', '-classpath', args.android_jar,
         '-d', out / 'classes', *sorted((source / 'src').rglob('*.java')),
         out / 'generated/AssetPins.java'])
    run([java / 'java', '-cp', args.d8_jar, 'com.android.tools.r8.D8',
         '--min-api', '28', '--lib', args.android_jar, '--output', out / 'dex',
         *sorted((out / 'classes').rglob('*.class'))])
    run([args.aapt2, 'compile', '--dir', resources, '-o', out / 'resources.zip'])
    run([args.aapt2, 'link', '-I', args.android_jar, '--manifest', out / 'AndroidManifest.xml',
         out / 'resources.zip', '-o', out / 'unsigned.apk'])
    with zipfile.ZipFile(out / 'unsigned.apk', 'a') as z:
        z.write(out / 'dex/classes.dex', 'classes.dex', compress_type=zipfile.ZIP_DEFLATED)
    run([java / 'java', '-jar', args.apksigner_jar, 'sign', '--ks', args.keystore,
         '--ks-pass', 'file:' + str(args.keystore_password_file.resolve()),
         '--out', out / 'switch.apk', out / 'unsigned.apk'])
    run([java / 'java', '-jar', args.apksigner_jar, 'verify', '--verbose', out / 'switch.apk'])
    result = {'sha256': hashlib.sha256((out / 'switch.apk').read_bytes()).hexdigest(),
              'versionCode': args.version_code, 'dailyDefault': args.daily_default,
              'launcherStyle': args.launcher_style,
              'signatureVerified': True, 'deployed': False, 'runtimeVerified': False}
    (out / 'build-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
