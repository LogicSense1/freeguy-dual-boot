#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Cold-backup identity, corruption and incomplete-capture rejection tests."""
import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('backup', Path(__file__).resolve().parents[1] / 'tools/backup-data.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'backup'
        self.folder.mkdir()
        self.manifest = dict(complete=True, stockBVerified=True, googleImagesInactive=True,
                             deviceWritesIssued=False, files=[])
        for i, name in enumerate(backup.NAMES):
            data = bytes([i]) * 4096
            path = self.folder / (name + '.gz')
            path.write_bytes(gzip.compress(data))
            self.manifest['files'].append(dict(name=name, bytes=len(data), backupFile=path.name,
                sha256=hashlib.sha256(data).hexdigest(), compressedSha256=backup.digest_file(path),
                readbackVerified=True))
        self.write()

    def write(self):
        (self.folder / 'manifest.json').write_text(json.dumps(self.manifest))

    def test_repository_output_and_alias_rejected_before_device_access(self):
        alias = self.root / 'checkout'
        alias.symlink_to(backup.ROOT, target_is_directory=True)
        for destination in (backup.ROOT / 'private-backup-fixture', alias / 'private-backup-fixture'):
            with patch.object(backup.subprocess, 'check_output') as read, \
                 self.assertRaisesRegex(ValueError, 'outside the repository'):
                backup.capture(Path('/unused/adb'), 'fixture-device', destination)
            read.assert_not_called()
            self.assertFalse(destination.exists())

    def test_dangling_capture_destination_rejected_before_device_access(self):
        destination = self.root / 'dangling'
        destination.symlink_to(self.root / 'absent')
        with patch.object(backup.subprocess, 'check_output') as read, self.assertRaises(ValueError):
            backup.capture(Path('/unused/adb'), 'fixture-device', destination)
        read.assert_not_called()
        self.assertFalse((self.root / 'absent').exists())

    def test_legacy_backup_does_not_invent_binding_or_restore_authority(self):
        result = backup.verify_backup(self.folder)
        self.assertEqual(result['filesVerified'], 7)
        self.assertEqual(result['expandedBytes'], 7 * 4096)
        for field in ('deviceBindingPresent', 'currentDeviceOrDataVerified', 'restoreAuthorized'):
            self.assertIs(result[field], False)

    def test_new_binding_is_reported_but_not_compared_to_a_live_device(self):
        self.manifest['deviceBindingSha256'] = 'a' * 64
        self.write()
        result = backup.verify_backup(self.folder)
        self.assertTrue(result['deviceBindingPresent'])
        self.assertFalse(result['currentDeviceOrDataVerified'])

    def test_explicit_serial_binding_has_a_distinct_scheme(self):
        original = self.identity()
        result = backup.stock_identity(
            ['freeguy', '_b', '1', '11', '01.01.250225', 'green', ''],
            '/dev/block/dm-1 /data f2fs rw 0 0\n',
            '12345678-1234-1234-1234-123456789abc', None, 'fixture-device', 'android-serial')
        self.assertEqual(result['deviceBindingScheme'], 'freeguy-android-serial-v1')
        self.assertNotEqual(result['deviceBindingSha256'], original['deviceBindingSha256'])
        self.manifest.update(deviceBindingSha256=result['deviceBindingSha256'],
                             deviceBindingScheme=result['deviceBindingScheme'])
        self.write()
        self.assertEqual(backup.verify_backup(self.folder)['deviceBindingScheme'],
                         'freeguy-android-serial-v1')
        self.manifest['deviceBindingScheme'] = 'unknown'
        self.write()
        with self.assertRaises(ValueError):
            backup.verify_backup(self.folder)

    def test_no_implicit_binding_fallback_or_generic_serial(self):
        args = (['freeguy', '_b', '1', '11', '01.01.250225', 'green', ''],
                '/dev/block/dm-1 /data f2fs rw 0 0\n',
                '12345678-1234-1234-1234-123456789abc')
        with self.assertRaises(ValueError):
            backup.stock_identity(*args, None, 'fixture-device')
        for serial in ('unknown', '0000000000', '0123456789abcdef', 'freeguy', 'a\nb'):
            with self.subTest(serial=serial), self.assertRaises(ValueError):
                backup.stock_identity(*args, None, serial, 'android-serial')

    def test_incomplete_or_non_cold_capture_is_rejected(self):
        original = copy.deepcopy(self.manifest)
        for field in ('complete', 'stockBVerified', 'googleImagesInactive', 'deviceWritesIssued'):
            self.manifest = copy.deepcopy(original)
            self.manifest[field] = not self.manifest[field]
            self.write()
            with self.subTest(field=field), self.assertRaises(ValueError):
                backup.verify_backup(self.folder)

    def test_missing_duplicate_and_malformed_records(self):
        rows = copy.deepcopy(self.manifest['files'])
        for changed in (rows[:-1], rows[:-1] + [rows[0]], rows[:-1] + [None],
                        rows[:-1] + [dict(rows[-1], name=[])]):
            self.manifest['files'] = changed
            self.write()
            with self.assertRaises(ValueError):
                backup.verify_backup(self.folder)

    def test_paths_and_symlinks_are_rejected(self):
        self.manifest['files'][0]['backupFile'] = '../outside.gz'
        self.write()
        with self.assertRaises(ValueError):
            backup.verify_backup(self.folder)
        alias = self.root / 'alias'
        alias.symlink_to(self.folder)
        with self.assertRaises(ValueError):
            backup.verify_backup(alias)

    def test_corruption_truncation_and_expansion_bound(self):
        row = self.manifest['files'][0]
        path = self.folder / row['backupFile']
        original = path.read_bytes()
        path.write_bytes(original + b'changed')
        with self.assertRaises(ValueError):
            backup.verify_backup(self.folder)
        for bad in (original[:-4], gzip.compress(b'x' * 4097), gzip.compress(b'x' * 4096)):
            path.write_bytes(bad)
            row['compressedSha256'] = backup.digest_file(path)
            self.write()
            with self.assertRaises((ValueError, EOFError, OSError)):
                backup.verify_backup(self.folder)

    def identity(self, props=None, mounts=None, cid=None):
        return backup.stock_identity(props or ['freeguy', '_b', '1', '11', '01.01.250225', 'green', ''],
            mounts or '/dev/block/dm-1 /data f2fs rw 0 0\n',
            '12345678-1234-1234-1234-123456789abc', cid or 'ab' * 16, 'fixture-device')

    def test_only_verified_stock_b_identity_is_accepted(self):
        accepted = self.identity()
        self.assertEqual(len(accepted['deviceBindingSha256']), 64)
        for index, value in enumerate(('ladybird', '_a', '0', '14', 'other', 'orange', '1')):
            props = ['freeguy', '_b', '1', '11', '01.01.250225', 'green', '']
            props[index] = value
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.identity(props=props)
        with self.assertRaises(ValueError):
            self.identity(cid='unknown')
        with self.assertRaises(ValueError):
            self.identity(mounts='/dev/block/dm-1 /data f2fs rw 0 0\n/dev/block/loop1 /other ext4 rw 0 0')

    def test_existing_destination_is_rejected_before_any_adb_call(self):
        with patch.object(backup.subprocess, 'check_output') as run:
            with self.assertRaises(ValueError):
                backup.capture(Path('/unused/adb'), 'fixture', self.folder)
            run.assert_not_called()

    def fake_capture(self, *, attached=False, change_earlier=False, serial_binding=False):
        payload = {name: bytes([i]) * 4096 for i, name in enumerate(backup.NAMES)}
        transferred = []
        props = dict(zip(backup.PROPS, ['freeguy', '_b', '1', '11', '01.01.250225', 'green', '']))
        def shell(argv, **kwargs):
            command = argv[-1]
            if command.startswith('getprop '):
                name = command.split()[1]
                return props.get(name, 'fixture-device')
            if command.startswith('for p in /sys/block/loop'):
                return '/data/local/tmp/in-use.img' if attached else ''
            if command == 'cat /proc/1/mounts':
                return '/dev/block/dm-1 /data f2fs rw 0 0'
            if command == 'cat /proc/sys/kernel/random/boot_id':
                return '12345678-1234-1234-1234-123456789abc'
            if command == 'cat /sys/block/mmcblk0/device/cid':
                self.assertFalse(serial_binding, 'Serial-only capture must never read CID')
                return 'ab' * 16
            if command.endswith('/marker'):
                return 'codex-freeguy-fileboot-v1'
            if 'stat -c' in command:
                return '1 100 1 4096'
            if command.startswith('sha256sum '):
                name = command.rsplit('/', 1)[1]
                if change_earlier and len(transferred) == 7 and name == backup.NAMES[0]:
                    return 'f' * 64
                return hashlib.sha256(payload[name]).hexdigest()
            self.fail('Unexpected device command: ' + command)
        def stream(argv, **kwargs):
            self.assertEqual(argv[3:7], ['exec-out', 'gzip', '-1', '-c'])
            name = argv[-1].rsplit('/', 1)[1]
            kwargs['stdout'].write(gzip.compress(payload[name]))
            transferred.append(name)
        target = self.root / 'new'
        with patch.object(backup.subprocess, 'check_output', side_effect=shell), \
             patch.object(backup.subprocess, 'run', side_effect=stream), \
             patch.object(backup.shutil, 'disk_usage') as disk:
            disk.return_value.free = 32 * 1024**3
            backup.capture(Path('/unused/adb'), 'fixture', target,
                           'android-serial' if serial_binding else 'emmc-cid')
        return target

    def test_attached_but_unmounted_loop_rejects_capture(self):
        with self.assertRaisesRegex(ValueError, 'Loop backing files'):
            self.fake_capture(attached=True)
        self.assertFalse((self.root / 'new').exists())

    def test_earlier_file_change_keeps_capture_incomplete(self):
        with self.assertRaisesRegex(ValueError, 'Earlier image changed'):
            self.fake_capture(change_earlier=True)
        saved = json.loads((self.root / 'new/manifest.json').read_text())
        self.assertFalse(saved['complete'])
        with self.assertRaises(ValueError):
            backup.verify_backup(self.root / 'new')

    def test_stable_simulated_capture_verifies_all_files(self):
        target = self.fake_capture()
        result = backup.verify_backup(target)
        self.assertTrue(result['deviceBindingPresent'])
        self.assertEqual(result['filesVerified'], 7)

    def test_serial_only_capture_never_requests_cid(self):
        target = self.fake_capture(serial_binding=True)
        self.assertEqual(backup.verify_backup(target)['deviceBindingScheme'],
                         'freeguy-android-serial-v1')


if __name__ == '__main__':
    unittest.main()
