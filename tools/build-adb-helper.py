#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the HyperOS ADB recovery helper with local Android tools and signing inputs.

No device access, key generation, embedded password or installation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ANDROID = '{http://schemas.android.com/apk/res/android}'
PERMISSIONS = {
    'android.permission.RECEIVE_BOOT_COMPLETED',
    'android.permission.WRITE_SECURE_SETTINGS',
    'android.permission.FOREGROUND_SERVICE',
}


def need(condition, message):
    if not condition:
        raise ValueError(message)


def check_manifest(source):
    root = ET.parse(source / 'AndroidManifest.xml').getroot()
    need(root.get('package') == 'local.codex.tvadb', 'Unexpected package')
    permissions = [p.get(ANDROID + 'name') for p in root.findall('uses-permission')]
    need(len(permissions) == len(PERMISSIONS) and set(permissions) == PERMISSIONS,
         'Unexpected helper permission set')
    app = root.find('application')
    need(app is not None and app.get(ANDROID + 'allowBackup') == 'false' and
         app.get(ANDROID + 'directBootAware') == 'true', 'Unexpected application configuration')
    services = {s.get(ANDROID + 'name'): s for s in app.findall('service')}
    need(set(services) == {'.KeeperService', '.ConnectionService', '.RestoreJob'},
         'Unexpected service set')
    need(services['.KeeperService'].get(ANDROID + 'exported') == 'false' and
         services['.ConnectionService'].get(ANDROID + 'permission') ==
         'android.permission.BIND_ACCESSIBILITY_SERVICE' and
         services['.RestoreJob'].get(ANDROID + 'permission') ==
         'android.permission.BIND_JOB_SERVICE', 'Unexpected service protection')
    a11y = ET.parse(source / 'res/xml/connection_service.xml').getroot()
    expected = {
        ANDROID + 'accessibilityFeedbackType': 'feedbackGeneric',
        ANDROID + 'notificationTimeout': '0',
        ANDROID + 'canRetrieveWindowContent': 'false',
        ANDROID + 'canPerformGestures': 'false',
    }
    need(a11y.tag == 'accessibility-service' and a11y.attrib == expected,
         'Accessibility capabilities changed')
    keys = None
    for locale in ('values', 'values-zh'):
        strings = ET.parse(source / 'res' / locale / 'strings.xml').getroot()
        names = [n.get('name') for n in strings.findall('string')]
        need(len(names) == len(set(names)) and all(n.text for n in strings), 'Invalid localized strings')
        need(keys is None or keys == set(names), 'Localization keys differ')
        keys = set(names)
    return int(root.get(ANDROID + 'versionCode'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('android-jar', 'aapt2', 'd8-jar', 'apksigner-jar', 'java-home',
                 'keystore', 'keystore-password-file', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    source = ROOT / 'adb-helper'
    version = check_manifest(source)
    need(not args.output.exists(), 'Use a new output directory')
    for path in (args.android_jar, args.aapt2, args.d8_jar, args.apksigner_jar, args.keystore):
        need(path.is_file(), 'Missing local build input: ' + path.name)
    password = args.keystore_password_file
    need(not password.is_symlink() and password.is_file() and
         password.stat().st_mode & 0o077 == 0, 'Password file must be private (0600 or stricter)')
    out = args.output.resolve()
    out.mkdir(parents=True)
    for name in ('classes', 'dex', 'generated'):
        (out / name).mkdir()
    java = args.java_home / 'bin'

    def run(command):
        subprocess.run([str(x) for x in command], check=True, capture_output=True,
                       stdin=subprocess.DEVNULL)

    run([args.aapt2, 'compile', '--dir', source / 'res', '-o', out / 'resources.zip'])
    run([args.aapt2, 'link', '-I', args.android_jar, '--manifest', source / 'AndroidManifest.xml',
         '--java', out / 'generated', '-o', out / 'unsigned.apk', out / 'resources.zip'])
    run([java / 'javac', '--release', '8', '-classpath', args.android_jar, '-d', out / 'classes',
         *sorted((source / 'src').rglob('*.java')), *sorted((out / 'generated').rglob('*.java'))])
    run([java / 'java', '-cp', args.d8_jar, 'com.android.tools.r8.D8', '--min-api', '30',
         '--lib', args.android_jar, '--output', out / 'dex', *sorted((out / 'classes').rglob('*.class'))])
    with zipfile.ZipFile(out / 'unsigned.apk', 'a') as archive:
        archive.write(out / 'dex/classes.dex', 'classes.dex', compress_type=zipfile.ZIP_DEFLATED)
    run([java / 'java', '-jar', args.apksigner_jar, 'sign', '--ks', args.keystore,
         '--ks-pass', 'file:' + str(password.resolve()), '--out', out / 'adb-helper.apk',
         out / 'unsigned.apk'])
    run([java / 'java', '-jar', args.apksigner_jar, 'verify', '--verbose', out / 'adb-helper.apk'])
    with zipfile.ZipFile(out / 'adb-helper.apk') as archive:
        need('classes.dex' in archive.namelist(), 'Missing helper DEX')
    result = {'package': 'local.codex.tvadb', 'versionCode': version,
              'sha256': hashlib.sha256((out / 'adb-helper.apk').read_bytes()).hexdigest(),
              'signatureVerified': True, 'permissions': sorted(PERMISSIONS),
              'accessibilityUiEventsRequested': False,
              'deviceAccess': False, 'deployed': False, 'runtimeVerified': False}
    (out / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
