#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Integrate source-built components into a pinned local daily ramdisk.

This is not the initial stock/donor firmware assembly stage. The input ramdisk
and resulting firmware-bearing output remain private. No device access.
"""
import argparse
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('loader_build', ROOT / 'tools/build-loader.py')
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)
need = archive.need
sha = lambda value: hashlib.sha256(value).hexdigest()
VOICE = ('classes.dex', 'reconnect.dex', 'wake.dex', 'start.sh', 'lifecycle.sh',
         'supervisor.sh', 'register-wake.sh', 'oem.apk', 'libmibluetooth_jni.so')
MAPPED = {
    'helper': ('codex/helper', 'system/bin/init', 'codex/trial-guard'),
    'hdmi': ('codex/overlays/hdmi-priv-app/OriginalHdmi/OriginalHdmi.apk',),
    'label': ('codex/overlays/hdmi-priv-app/NativeHdmiLabel/NativeHdmiLabel.apk',),
    'audio': ('codex/overlays/builtin-mic-primary.so',),
    'voice-index': ('codex/autovoice/payload.sha256',),
    **{'voice-' + n: ('codex/autovoice/' + n,) for n in VOICE if n not in ('oem.apk', 'libmibluetooth_jni.so')},
}


def read_input(record, parent):
    need(isinstance(record, dict) and set(record) == {'path', 'sha256'}, 'Invalid input record')
    need(isinstance(record['sha256'], str) and re.fullmatch('[0-9a-f]{64}', record['sha256']), 'Invalid hash')
    need(isinstance(record['path'], str), 'Invalid input path')
    path = parent / record['path']
    need(not path.is_symlink() and path.is_file(), 'Expected regular local input')
    need(path.stat().st_size <= archive.MAX_BYTES, 'Input exceeds bound')
    with path.open('rb') as file:
        data = file.read(archive.MAX_BYTES + 1)
    need(len(data) <= archive.MAX_BYTES and sha(data) == record['sha256'], 'Input hash mismatch')
    return data


def pack(items):
    data = bytearray()
    trailer = {'name': 'TRAILER!!!', 'fields': [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0], 'data': b''}
    for e in [*items, trailer]:
        name, fields = e['name'].encode('ascii') + b'\0', e['fields'][:]
        fields[6], fields[11], fields[12] = len(e['data']), len(name), 0
        need(all(isinstance(v, int) and 0 <= v <= 0xffffffff for v in fields), 'Invalid metadata')
        data.extend(b'070701' + ''.join('%08x' % v for v in fields).encode('ascii'))
        data.extend(name)
        data.extend(b'\0' * (-len(data) % 4))
        data.extend(e['data'])
        data.extend(b'\0' * (-len(data) % 4))
        need(len(data) <= archive.MAX_BYTES, 'Output exceeds bound')
    return bytes(data)


def replace_once(text, before, after):
    need(text.count(before) == 1, 'Unexpected daily startup script')
    return text.replace(before, after, 1)


def patch_entry(raw):
    text = raw.decode('utf-8')
    need('watchdog-daily' in text and 'TRANSITION_GUARD_PID' in text and
         'shared-default-v1' in text, 'Expected daily startup entry')
    text = replace_once(text, 'mkdir -p /dev/codex-autovoice\n',
        'cp /codex/runtime-policy.sh /dev/codex-runtime-policy.sh\n'
        'cp /codex/runtime-policy-start.sh /dev/codex-runtime-policy-start.sh\n'
        'chmod 700 /dev/codex-runtime-policy.sh /dev/codex-runtime-policy-start.sh\n'
        'mkdir -p /dev/codex-autovoice\n')
    old = '    /system/bin/sh /dev/codex-autovoice/start.sh > /data/local/tmp/codex-fileboot-logs/remote-voice-startup.txt 2>&1 &\n'
    new = ('    (\n'
           '        /system/bin/sh /dev/codex-runtime-policy-start.sh > /data/local/tmp/codex-fileboot-logs/runtime-policy.txt 2>&1 || exit 1\n'
           '        /system/bin/sh /dev/codex-autovoice/start.sh > /data/local/tmp/codex-fileboot-logs/remote-voice-startup.txt 2>&1\n'
           '    ) &\n')
    text = replace_once(text, old, new)
    text = replace_once(text, '            if [ "$(getprop sys.boot_completed)" = 1 ] &&\n',
        '            if [ "$(cat /dev/codex-runtime-policy-ready 2>/dev/null)" = ready ] &&\n'
        '               [ "$(getprop sys.boot_completed)" = 1 ] &&\n')
    return text.encode()


def integrate(items, artifacts, starter):
    old = {e['name']: copy.deepcopy(e) for e in items}
    byname = {e['name']: e for e in items}
    need(len(old) == len(items), 'Duplicate archive names')
    index = {}
    for line in artifacts['voice-index'].decode('ascii').splitlines():
        match = re.fullmatch('([0-9a-f]{64})  ([A-Za-z0-9_.-]+)', line)
        need(match is not None and match[2] not in index, 'Invalid voice hash index')
        index[match[2]] = match[1]
    need(set(index) == set(VOICE), 'Unexpected voice files')
    for name in VOICE:
        need(sha(artifacts['voice-' + name]) == index[name], 'Voice payload mismatch')
    for key, target in (('hdmi', '/system/priv-app/OriginalHdmi/OriginalHdmi.apk'),
                        ('label', '/system/priv-app/NativeHdmiLabel/NativeHdmiLabel.apk'),
                        ('audio', '/vendor/lib/hw/audio.primary.mt5879.so')):
        need(artifacts['policy'].count((sha(artifacts[key]) + '  ' + target).encode()) == 1,
             'Policy does not match built artifacts')
    allowed = set()
    for prefix in ('', 'first_stage_ramdisk/'):
        # OEM voice files remain external. Refuse to silently change that input
        # contract while updating the bridge and its index.
        previous_index = dict((line.split()[1], line.split()[0]) for line in
                              old[prefix + 'codex/autovoice/payload.sha256']['data'].decode().splitlines())
        for name in ('oem.apk', 'libmibluetooth_jni.so'):
            need(previous_index.get(name) == index[name], 'OEM dependency changed')
        for key, paths in MAPPED.items():
            for suffix in paths:
                name = prefix + suffix
                need(name in byname and stat.S_ISREG(byname[name]['fields'][1]), 'Missing component')
                byname[name]['data'] = artifacts[key]
                allowed.add(name)
        entry = prefix + 'codex/entry.sh'
        byname[entry]['data'] = patch_entry(byname[entry]['data'])
        allowed.add(entry)
        for suffix, content in (('runtime-policy.sh', artifacts['policy']), ('runtime-policy-start.sh', starter)):
            name = prefix + 'codex/' + suffix
            need(name not in byname, 'Startup policy already integrated')
            value = copy.deepcopy(old[entry])
            value.update(name=name, data=content)
            value['fields'][0] = max(e['fields'][0] for e in items) + 1
            value['fields'][1] = stat.S_IFREG | 0o700
            items.append(value)
            byname[name] = value
            allowed.add(name)
    changes = []
    for name, value in byname.items():
        prior = old.get(name)
        if prior != value:
            need(name in allowed, 'Unexpected runtime change')
            need(prior is None or prior['fields'] == value['fields'], 'Existing metadata changed')
            changes.append({'path': name, 'before': sha(prior['data']) if prior else None,
                            'after': sha(value['data'])})
    need(set(old).issubset(byname), 'Archive entry removed')
    for name in allowed:
        if not name.startswith('first_stage_ramdisk/'):
            need(byname[name]['data'] == byname['first_stage_ramdisk/' + name]['data'], 'Duplicate roots differ')
    archive.table(items)  # Verify parents are declared directories, never symlinks.
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    need(not args.output.exists(), 'Use a new output directory')
    need(args.profile.stat().st_size <= 32768, 'Profile exceeds bound')
    profile = json.loads(args.profile.read_text())
    need(set(profile) == {'version', 'ramdisk', 'artifacts'} and profile['version'] == 1, 'Unexpected profile')
    keys = set(MAPPED) | {'policy', 'voice-oem.apk', 'voice-libmibluetooth_jni.so'}
    need(set(profile['artifacts']) == keys, 'Unexpected artifact set')
    source = read_input(profile['ramdisk'], args.profile.parent)
    artifacts = {key: read_input(value, args.profile.parent) for key, value in profile['artifacts'].items()}
    if source.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=io.BytesIO(source)) as stream:
            raw = stream.read(archive.MAX_BYTES + 1)
    else:
        raw = source
    items = archive.entries(raw)
    starter = (ROOT / 'runtime/runtime-policy-start.sh').read_bytes()
    changes = integrate(items, artifacts, starter)
    for item in items:
        if item['name'].endswith(('codex/entry.sh', 'codex/runtime-policy.sh', 'codex/runtime-policy-start.sh')):
            subprocess.run(['sh', '-n'], input=item['data'], check=True, capture_output=True)
    assembled = pack(items)
    decoded = archive.entries(assembled)
    semantic = lambda rows: [(e['name'], e['fields'][1:6], e['data']) for e in rows]
    need(semantic(decoded) == semantic(items), 'Archive readback mismatch')
    compressed = gzip.compress(assembled, mtime=0)
    args.output.mkdir(parents=True)
    (args.output / 'ramdisk.cpio.gz').write_bytes(compressed)
    report = {'inputSha256': sha(source), 'outputSha256': sha(compressed), 'changes': changes,
              'artifacts': {key: sha(value) for key, value in artifacts.items()},
              'startupPolicySourceSha256': sha(starter), 'entries': len(items),
              'unlistedEntriesUnchanged': True, 'stockInitPreserved': True,
              'deviceAccess': False, 'deployEligible': False, 'runtimeVerified': False,
              'containsPrivateFirmwareAndConfiguration': True,
              'initialFirmwareAssemblyIncluded': False}
    (args.output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'entries': len(items), 'changes': len(changes),
                      'deployEligible': False, 'outputSha256': sha(compressed)}))


if __name__ == '__main__':
    main()
