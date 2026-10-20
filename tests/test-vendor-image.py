#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Vendor mutation bounds and independent readback failure contracts."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('vendor',
    Path(__file__).resolve().parents[1] / 'tools/build-vendor-image.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def inputs():
    data = {name: b'# original\n' for name in build.PATHS}
    data['/etc/init/hw/init.mt9653.usb.rc'] = b'on init\n    write /sys/example keep\non boot\n    old-boot\n'
    data['/etc/init/init.insmod.rc'] = b'insmod driver.ko mtu3_dev_en=0\n'
    data['/etc/init/init.fusion.rc'] = b'import /vendor/etc/init/hw/init.insmod.rc\n'
    return data


class VendorTests(unittest.TestCase):
    def test_space_check_uses_output_filesystem_before_creating_it(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder).resolve()
            output = parent / 'not-created' / 'new-build'
            with patch.object(build.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)) as usage:
                with self.assertRaisesRegex(ValueError, 'output workspace'):
                    build.check_output_space(output, 1024)
                usage.assert_called_once_with(parent)
            self.assertFalse(output.parent.exists())
            with patch.object(build.shutil, 'disk_usage',
                              return_value=SimpleNamespace(free=3 * 1024**3)):
                build.check_output_space(output, 1024)

    def test_storage_mutation_services_and_triggers_are_removed(self):
        data = inputs()
        data['/etc/init/hw/init.common.rc'] = b'''service makeuserdata /vendor/bin/format
    oneshot
on mkfs_userdata
    exec -- erase-userdata
on boot
    start makeuserdata
    mount_all /vendor/etc/fstab_late
    symlink /dev/block/mmcblk0 /dev/disk
    start safe_service
service safe_service /vendor/bin/keep
    oneshot
'''
        result = build.render(data, b'(type fixture)\n')
        text = result['/etc/init/hw/init.common.rc']
        for forbidden in (b'makeuserdata', b'erase-userdata', b'mount_all', b'/dev/block/mmcblk0'):
            self.assertNotIn(forbidden, text)
        self.assertIn(b'start safe_service', text)
        self.assertIn(b'service safe_service /vendor/bin/keep', text)
        for name in ('/bin/set_uuenv.sh', '/bin/update_wakeup_rc.sh'):
            self.assertTrue(result[name].endswith(b'exit 0\n'))
        self.assertIn(b'uuenv get wake_up_rc', result['/bin/init.product.sh'])
        self.assertNotIn(b'uuenv set', result['/bin/init.product.sh'])

    def test_usb_preboot_configuration_is_retained(self):
        result = build.render(inputs(), b'(type fixture)\n')
        self.assertIn(b'on init\n    write /sys/example keep\n', result['/etc/init/hw/init.mt9653.usb.rc'])
        self.assertNotIn(b'old-boot', result['/etc/init/hw/init.mt9653.usb.rc'])
        self.assertIn(b'ro.adb.secure=1\n', result['/build.prop'])

    def test_unknown_revision_and_extra_input_are_rejected(self):
        for name in ('/etc/init/init.insmod.rc', '/etc/init/init.fusion.rc', '/etc/init/hw/init.mt9653.usb.rc'):
            data = inputs()
            data[name] = b'unknown revision\n'
            with self.subTest(name=name), self.assertRaises(ValueError):
                build.render(data, b'(type fixture)\n')
        with self.assertRaises(ValueError):
            build.render({**inputs(), '/unexpected': b'x'}, b'(type fixture)\n')

    def fixture(self):
        row = {'mode': 0o100644, 'uid': 0, 'gid': 0, 'xattrs': {'security.selinux': 'aa'},
               'bytes': 3, 'sha256': hashlib.sha256(b'old').hexdigest()}
        return {'/changed': row, '/untouched': copy.deepcopy(row)}

    def test_unrelated_content_corruption_fails_readback(self):
        before = self.fixture()
        after = copy.deepcopy(before)
        after['/untouched']['sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            build.verify_inventory(before, after, {})

    def test_metadata_or_path_changes_fail_even_on_patch_target(self):
        before = self.fixture()
        for key, value in (('mode', 0o100777), ('uid', 1000), ('gid', 2000), ('xattrs', {})):
            after = copy.deepcopy(before)
            after['/changed'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                build.verify_inventory(before, after, {'/changed': b'old'})
        for after in ({'/changed': before['/changed']}, {**before, '/new': before['/changed']}):
            with self.assertRaises(ValueError):
                build.verify_inventory(before, after, {})

    def test_only_declared_file_content_can_change(self):
        before = self.fixture()
        after = copy.deepcopy(before)
        after['/changed'].update(bytes=4, sha256=hashlib.sha256(b'new!').hexdigest())
        build.verify_inventory(before, after, {'/changed': b'new!'})
        with self.assertRaises(ValueError):
            build.verify_inventory(before, after, {})

    def test_security_label_terminator_is_not_normalized_away(self):
        before = self.fixture()
        before['/changed']['xattrs']['security.selinux'] = b'u:object_r:vendor_file:s0\0'.hex()
        after = copy.deepcopy(before)
        after['/changed']['xattrs']['security.selinux'] = b'u:object_r:vendor_file:s0'.hex()
        with self.assertRaises(ValueError):
            build.verify_inventory(before, after, {'/changed': b'old'})


if __name__ == '__main__':
    unittest.main()
