#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check bounded boot parsing and bootstrap transformations on synthetic data."""
import copy
import gzip
import importlib.util
from pathlib import Path
import stat
import struct
import sys
import unittest

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('bootstrap',
    Path(__file__).resolve().parents[1] / 'tools/build-bootstrap-root.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def item(name, mode, content=b''):
    return {'name': name, 'fields': [1, mode, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0], 'data': content}


def fixture():
    init = bytearray(b'\0' * 526000)
    init[525260:525274] = bytes.fromhex('2cf0d0e801280bd001202cf0d4e8')
    files = {
        'init': (0o120777, b'/system/bin/init'),
        'system/bin/init': (0o100750, bytes(init)),
        'system/etc/init/hw/init.rc': (0o100644, b'import /init.recovery.${ro.hardware}.rc\n'
            b'service charger /system/bin/charger\n    critical\n'
            b'service recovery /system/bin/recovery\n'),
        'system/etc/recovery.fstab': (0o100644, b'original mounts\n'),
        'first_stage_ramdisk/fstab.mt9653': (0o100644, b'first-stage mounts\n'),
        'init.recovery.mt9653.rc': (0o100644, b'original hardware initialization\n'),
        'prop.default': (0o100644, b'ro.secure=1\nro.adb.secure=1\nro.debuggable=0\n'),
        'lib/modules/modules.load.recovery': (0o100644, b'mtu3.ko\nmdrv-graphic.ko\nphy-mtk-tphy-tv.ko\n'),
    }
    directories = set()
    for name in files:
        directories.update(str(p) for p in Path(name).parents if str(p) != '.')
    rows = [item(name, 0o40755) for name in sorted(directories)]
    rows += [item(name, mode, data) for name, (mode, data) in files.items()]
    helper = bytearray(52)
    helper[:6] = b'\x7fELF\x01\x01'
    struct.pack_into('<HH', helper, 16, 2, 40)
    return rows, bytes(helper)


class BootstrapTests(unittest.TestCase):
    def test_seed_preserves_input_and_only_expected_init_bytes(self):
        rows, helper = fixture()
        before = copy.deepcopy(rows)
        output, _, _ = build.seed(rows, helper)
        self.assertEqual(rows, before)
        byname = {e['name']: e for e in output}
        old_init = next(e['data'] for e in rows if e['name'] == 'system/bin/init')
        patched = byname['system/bin/stock-init']['data']
        self.assertEqual([i for i, pair in enumerate(zip(old_init, patched)) if pair[0] != pair[1]], [525264, 525268])
        self.assertEqual(byname['system/bin/init']['data'], helper)
        for name, row in byname.items():
            if not name.startswith('first_stage_ramdisk'):
                self.assertEqual(row['data'], byname['first_stage_ramdisk/' + name]['data'])
        self.assertNotIn('init.recovery.mt9653.rc', byname)
        self.assertNotIn(b'mdrv-graphic.ko', byname['lib/modules/modules.load']['data'])
        self.assertIn(b'service.adb.tcp.port=0\n', byname['prop.default']['data'])
        self.assertNotIn(b'original mounts', byname['system/etc/recovery.fstab']['data'])

    def test_wrong_instruction_context_and_helper_architecture_fail(self):
        for kind in ('init', 'helper'):
            rows, helper = fixture()
            if kind == 'init':
                e = next(e for e in rows if e['name'] == 'system/bin/init')
                e['data'] = e['data'][:525264] + b'\xff' + e['data'][525265:]
            else:
                helper = helper[:18] + b'\xb7\x00' + helper[20:]
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                build.seed(rows, helper)

    def test_missing_or_duplicate_module_change_fails(self):
        for value in (b'mtu3.ko\n', b'mdrv-graphic.ko\nmdrv-graphic.ko\n'):
            rows, helper = fixture()
            next(e for e in rows if e['name'] == 'lib/modules/modules.load.recovery')['data'] = value
            with self.assertRaises(ValueError):
                build.seed(rows, helper)

    def test_valid_boot_and_truncated_component(self):
        cpio = build.components.pack([item('file', 0o100644, b'payload')])
        ram = gzip.compress(cpio, mtime=0)
        header = bytearray(2048)
        header[:8] = b'ANDROID!'
        for offset, value in ((16, len(ram)), (36, 2048), (40, 2), (1644, 1660)):
            struct.pack_into('<I', header, offset, value)
        raw = bytes(header) + ram + b'\0' * (-len(ram) % 2048)
        self.assertEqual(build.ramdisk_from_boot(raw)[0]['data'], b'payload')
        for broken in (raw[:2048 + len(ram) - 1], b'wrong' + raw[5:], raw[:36] + struct.pack('<I', 123) + raw[40:]):
            with self.assertRaises(ValueError):
                build.ramdisk_from_boot(broken)


if __name__ == '__main__':
    unittest.main()
