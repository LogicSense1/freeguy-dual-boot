#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the isolated system startup transform without accessing firmware."""
import importlib.util
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
spec = importlib.util.spec_from_file_location('system_build',
    Path(__file__).resolve().parents[1] / 'tools/build-system-image.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def fixture():
    files = {name: b'service safe /system/bin/safe\n    class core\n    reboot_on_failure reboot,bootloader\n'
             for name in build.READ_PATHS}
    files['/system/build.prop'] = b'ro.adb.secure=0\nkeep.property=value\n'
    files['/system/etc/init/hw/init.rc'] += (
        b'on early-boot\n    exec_start update_verifier\n'
        b'    exec -- /system/bin/vdc checkpoint markBootAttempt\n'
        b'import /vendor/etc/init/hw/init.bsp-mtk.rc\n'
        b'    start safe_service\n')
    return files


class SystemTests(unittest.TestCase):
    def test_ota_and_bootloader_failure_requests_removed(self):
        result = build.render(fixture(), b'#!/system/bin/sh\nexit 0\n')
        self.assertEqual(len(result), 14)
        for name in build.DISABLED:
            self.assertTrue(result['/system/etc/init/' + name + '.rc']['data'].startswith(b'# Disabled'))
        for name in build.READ_PATHS[:4]:
            value = result[name]['data']
            self.assertNotIn(b'reboot_on_failure', value)
            self.assertIn(b'service safe /system/bin/safe', value)
        value = result['/system/etc/init/hw/init.rc']['data']
        for forbidden in (b'exec_start update_verifier', b'checkpoint markBootAttempt', b'init.bsp-mtk.rc'):
            self.assertNotIn(forbidden, value)
        self.assertIn(b'start safe_service', value)

    def test_authenticated_adb_and_migration_bytes_retained(self):
        migration = b'#!/system/bin/sh\n# local fixture only\nexit 0\n'
        result = build.render(fixture(), migration)
        props = result['/system/build.prop']['data']
        self.assertEqual(props.count(b'ro.adb.secure='), 1)
        self.assertIn(b'ro.adb.secure=1\n', props)
        self.assertIn(b'keep.property=value', props)
        self.assertEqual(result['/system/bin/move_widevine_data.sh']['data'], migration)
        self.assertEqual(result['/perm']['template'], '/mnt')

    def test_unknown_input_or_invalid_helper_rejected(self):
        with self.assertRaises(ValueError):
            build.render({**fixture(), '/extra': b'x'}, b'#!/bin/sh\n')
        for data in (b'', b'not a script', b'#!/bin/sh\n' + b'x' * 65536):
            with self.assertRaises(ValueError):
                build.render(fixture(), data)


if __name__ == '__main__':
    unittest.main()
