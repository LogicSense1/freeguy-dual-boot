#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Rebuild the local bootstrap layer from the supported stock boot image.

No flash image, kernel, or device command is produced. The intermediate CPIO
contains OEM files and is private. Google files and runtime overlays still need
to be assembled before this layer can be used by the loader.
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
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('components', ROOT / 'tools/assemble-runtime-components.py')
components = importlib.util.module_from_spec(spec)
spec.loader.exec_module(components)
need, archive = components.need, components.archive
STOCK_SHA256 = 'e9f89733f586648d6af0784d5b4d233da9ba2d7f163bd275c427a454d3224da6'


def ramdisk_from_boot(raw):
    need(len(raw) >= 1660 and raw[:8] == b'ANDROID!', 'Expected Android boot image')
    u32 = lambda offset: struct.unpack_from('<I', raw, offset)[0]
    need(u32(40) == 2 and u32(1644) == 1660, 'Expected supported v2 header')
    page = u32(36)
    need(page in (2048, 4096, 8192, 16384), 'Invalid boot page size')
    position = page
    parts = []
    sizes = (u32(8), u32(16), u32(24), u32(1632), u32(1648))
    for size in sizes:
        need(position + size <= len(raw), 'Truncated boot component')
        parts.append(raw[position:position + size])
        position += (size + page - 1) // page * page
    if sizes[3]:
        expected = page + sum((size + page - 1) // page * page for size in sizes[:3])
        need(struct.unpack_from('<Q', raw, 1636)[0] == expected, 'Unexpected recovery DTBO offset')
    need(parts[1].startswith(b'\x1f\x8b'), 'Expected gzip ramdisk')
    with gzip.GzipFile(fileobj=io.BytesIO(parts[1])) as stream:
        expanded = stream.read(archive.MAX_BYTES + 1)
    need(len(expanded) <= archive.MAX_BYTES, 'Expanded ramdisk exceeds bound')
    return archive.entries(expanded)


def seed(items, helper):
    items = copy.deepcopy(items)
    byname = {e['name']: e for e in items}
    need(len(byname) == len(items), 'Duplicate source entry')
    need(byname['init']['data'] == b'/system/bin/init' and stat.S_ISLNK(byname['init']['fields'][1]),
         'Unexpected init link')
    need(helper[:6] == b'\x7fELF\x01\x01' and len(helper) >= 52 and
         struct.unpack_from('<HH', helper, 16) == (2, 40), 'Expected static ARM32 helper')

    def add(name, content, template):
        need(name not in byname, 'Entry already exists')
        entry = copy.deepcopy(byname[template])
        entry.update(name=name, data=content)
        entry['fields'][0] = max(e['fields'][0] for e in items) + 1
        items.append(entry)
        byname[name] = entry

    original_init = byname['system/bin/init']['data']
    patched_init = bytearray(original_init)
    # The validated stock init forces enforcing here. These two Thumb immediate
    # changes are local compatibility edits; the source boot image stays intact.
    need(patched_init[525260:525274].hex() == '2cf0d0e801280bd001202cf0d4e8', 'Unexpected init instruction context')
    patched_init[525264:525266] = bytes.fromhex('0028')
    patched_init[525268:525270] = bytes.fromhex('0020')
    add('system/bin/stock-init', bytes(patched_init), 'system/bin/init')
    byname['system/bin/init']['data'] = helper

    properties = {'ro.secure': '0', 'ro.debuggable': '1', 'ro.adb.secure': '0',
                  'service.adb.tcp.port': '0', 'persist.adb.tcp.port': '0',
                  'persist.sys.usb.config': 'adb', 'sys.usb.controller': '1c7a3e00.usb',
                  'ro.codex.native_backup': '3'}
    text = byname['prop.default']['data'].decode()
    for key, value in properties.items():
        text = re.sub(r'^' + re.escape(key) + r'=.*\n?', '', text, flags=re.M)
        text += '\n' + key + '=' + value + '\n'
    byname['prop.default']['data'] = text.encode()
    rc = byname['system/etc/init/hw/init.rc']['data'].decode()
    for before, after in (
        ('import /init.recovery.${ro.hardware}.rc', 'import /init.recovery.codex.rc'),
        ('service charger /system/bin/charger\n    critical', 'service charger /system/bin/charger\n    disabled'),
        ('service recovery /system/bin/recovery\n', 'service recovery /system/bin/recovery\n    disabled\n')):
        rc = components.replace_once(rc, before, after)
    rc += ('\n# Temporary USB-only RAM backup environment\non boot\n'
           '    setprop service.adb.tcp.port 0\n    setprop persist.adb.tcp.port 0\n'
           '    setprop sys.usb.controller 1c7a3e00.usb\n    setprop service.adb.root 1\n'
           '    setprop sys.usb.config adb\n    start adbd\n')
    byname['system/etc/init/hw/init.rc']['data'] = rc.encode()
    recovery = byname.pop('init.recovery.mt9653.rc')
    recovery['name'] = 'init.recovery.codex.rc'
    recovery['data'] = (b'# USB setup only; no block-device mounts, modules, or vendor executables.\n'
        b'on early-fs\n    setprop sys.usb.configfs 1\n\n'
        b'# If init reaches boot, return to the unchanged installed OS after 120 seconds.\n'
        b'on boot\n    start codex-autoreturn\n\n'
        b'service codex-autoreturn /system/bin/sh /codex-autoreturn.sh\n'
        b'    disabled\n    oneshot\n    user root\n    group root\n    seclabel u:r:recovery:s0\n')
    byname[recovery['name']] = recovery
    for entry in items:
        if entry['name'].endswith('fstab.mt9653') or entry['name'] == 'system/etc/recovery.fstab':
            entry['data'] = b'# Experimental backup environment: no automatic partition mounts.\n'
    modules = byname['lib/modules/modules.load.recovery']['data'].decode().splitlines()
    need(modules.count('mdrv-graphic.ko') == 1, 'Unexpected module list')
    modules.remove('mdrv-graphic.ko')
    module_data = ('\n'.join(modules) + '\n').encode()
    byname['lib/modules/modules.load.recovery']['data'] = module_data
    add('lib/modules/modules.load', module_data, 'lib/modules/modules.load.recovery')
    add('lib/modules/modules.options', b'options mtu3 mtu3_dev_en=1\noptions phy_mtk_tphy_tv pm_degrad=1\n',
        'lib/modules/modules.load.recovery')
    add('codex-autoreturn.sh', b'#!/system/bin/sh\n/system/bin/sleep 120\n/system/bin/reboot\n',
        'lib/modules/modules.load.recovery')
    # Mirror the same bootstrap after Android first-stage SwitchRoot. Keep
    # existing first-stage entries absent from the root, including empty fstab.
    roots = [e for e in items if e['name'] != 'first_stage_ramdisk' and
             not e['name'].startswith('first_stage_ramdisk/')]
    copies, nextino = [], max(e['fields'][0] for e in items) + 1
    for entry in roots:
        clone = copy.deepcopy(entry)
        clone['name'] = 'first_stage_ramdisk/' + entry['name']
        clone['fields'][0] = nextino
        nextino += 1
        copies.append(clone)
    replaced = {e['name'] for e in copies}
    items = [e for e in items if e['name'] not in replaced] + copies
    archive.table(items)
    return items, hashlib.sha256(original_init).hexdigest(), hashlib.sha256(patched_init).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock-boot', type=Path, required=True)
    parser.add_argument('--helper', type=Path, required=True)
    parser.add_argument('--helper-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    need(not args.output.exists(), 'Use a new output directory')
    stock = components.read_input({'path': str(args.stock_boot.resolve()), 'sha256': STOCK_SHA256}, Path('/'))
    helper = components.read_input({'path': str(args.helper.resolve()), 'sha256': args.helper_sha256}, Path('/'))
    items, original_init, patched_init = seed(ramdisk_from_boot(stock), helper)
    raw = components.pack(items)
    need(len(archive.entries(raw)) == len(items), 'CPIO readback failed')
    compressed = gzip.compress(raw, mtime=0)
    args.output.mkdir(parents=True)
    (args.output / 'bootstrap.cpio.gz').write_bytes(compressed)
    report = {'stockBootSha256': STOCK_SHA256, 'helperSha256': args.helper_sha256,
              'originalInitSha256': original_init, 'localPatchedInitSha256': patched_init,
              'outputSha256': hashlib.sha256(compressed).hexdigest(), 'entries': len(items),
              'stockImageChanged': False, 'deviceAccess': False,
              'completeRuntime': False, 'deployEligible': False, 'containsOemFiles': True,
              'bootstrapFallback': 'USB debugging without authentication; network ADB disabled',
              'runtimeTested': False}
    (args.output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'entries': len(items), 'deployEligible': False, 'outputSha256': report['outputSha256']}))


if __name__ == '__main__':
    main()
