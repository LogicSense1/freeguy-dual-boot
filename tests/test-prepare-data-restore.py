#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Recovery preparation must never overwrite existing data or accept bad backups."""
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
spec = importlib.util.spec_from_file_location('restore', Path(__file__).resolve().parents[1] /
                                             'tools/prepare-data-restore.py')
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'backup'
        self.source.mkdir()
        self.output = self.root / 'new'
        self.scheme = 'freeguy-android-serial-v1'
        self.binding = '1' * 64
        self.record = dict(complete=True, stockBVerified=True, googleImagesInactive=True,
                           deviceWritesIssued=False, deviceBindingSha256=self.binding,
                           deviceBindingScheme=self.scheme, files=[])
        self.payloads = {}
        for i, name in enumerate(restore.backup.NAMES):
            data = bytes([i]) * 4096
            self.payloads[name] = data
            archive = self.source / (name + '.gz')
            archive.write_bytes(gzip.compress(data))
            self.record['files'].append(dict(name=name, backupFile=archive.name, bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                compressedSha256=restore.backup.digest_file(archive), readbackVerified=True))
        self.save()

    def save(self):
        (self.source / 'manifest.json').write_text(json.dumps(self.record))

    def run_prepare(self):
        return restore.prepare(self.source, self.output, self.binding, self.scheme)

    def test_repository_output_and_alias_rejected_before_backup_read(self):
        root = restore.backup.ROOT
        alias = self.root / 'checkout'
        alias.symlink_to(root, target_is_directory=True)
        for output in (root / 'private-restore-fixture', alias / 'private-restore-fixture'):
            with patch.object(restore.backup, 'verify_backup') as read, \
                 self.assertRaisesRegex(ValueError, 'outside the repository'):
                restore.prepare(self.source, output, self.binding, self.scheme)
            read.assert_not_called()
            self.assertFalse(output.exists())

    def test_complete_exact_readback_no_device_commands(self):
        with patch.object(restore.backup.subprocess, 'run') as run, \
             patch.object(restore.backup.subprocess, 'check_output') as read:
            result = self.run_prepare()
            run.assert_not_called()
            read.assert_not_called()
        self.assertTrue(result['complete'])
        self.assertFalse(result['installed'])
        self.assertFalse(result['currentDeviceVerified'])
        self.assertFalse(result['filesystemConsistencyVerified'])
        for name, data in self.payloads.items():
            self.assertEqual((self.output / name).read_bytes(), data)
            self.assertEqual((self.output / name).stat().st_mode & 0o777, 0o600)

    def test_existing_output_untouched(self):
        self.output.mkdir()
        keep = self.output / 'userdata.img'
        keep.write_bytes(b'existing personal data')
        with self.assertRaisesRegex(ValueError, 'new local output'):
            self.run_prepare()
        self.assertEqual(keep.read_bytes(), b'existing personal data')

    def test_dangling_output_symlink_rejected(self):
        self.output.symlink_to(self.root / 'absent')
        with self.assertRaises(ValueError):
            self.run_prepare()
        self.assertFalse((self.root / 'absent').exists())

    def test_wrong_or_legacy_binding_rejected_before_expansion(self):
        for field, value in (('deviceBindingSha256', '2' * 64),
                             ('deviceBindingSha256', None), ('deviceBindingScheme', None)):
            original = self.record[field]
            self.record[field] = value
            self.save()
            with self.assertRaisesRegex(ValueError, 'binding mismatch'):
                self.run_prepare()
            self.assertFalse(self.output.exists())
            self.record[field] = original

    def test_corrupt_archive_rejected_without_output(self):
        p = self.source / 'userdata.img.gz'
        p.write_bytes(p.read_bytes()[:-5])
        with self.assertRaises(ValueError):
            self.run_prepare()
        self.assertFalse(self.output.exists())

    def test_incomplete_capture_rejected(self):
        self.record['complete'] = False
        self.save()
        with self.assertRaises(ValueError):
            self.run_prepare()
        self.assertFalse(self.output.exists())

    def test_disk_reserve_rejected(self):
        with patch.object(restore.shutil, 'disk_usage') as disk:
            disk.return_value.free = 0
            with self.assertRaisesRegex(ValueError, 'reserve'):
                self.run_prepare()
        self.assertFalse(self.output.exists())

    def test_failed_readback_never_marked_complete(self):
        real = restore.backup.digest_file
        def digest(path):
            return '0' * 64 if path.suffix == '.partial' else real(path)
        with patch.object(restore.backup, 'digest_file', side_effect=digest):
            with self.assertRaisesRegex(ValueError, 'readback mismatch'):
                self.run_prepare()
        report = json.loads((self.output / 'manifest.json').read_text())
        self.assertFalse(report['complete'])
        self.assertFalse((self.output / 'uenv.img').exists())

    def test_source_mutation_after_verification_is_rejected(self):
        real = restore.backup.verify_backup
        def verify(folder):
            result = real(folder)
            p = folder / 'uenv.img.gz'
            p.write_bytes(gzip.compress(b'x' * 4096))
            return result
        with patch.object(restore.backup, 'verify_backup', side_effect=verify):
            with self.assertRaisesRegex(ValueError, 'changed before expansion'):
                self.run_prepare()
        report = json.loads((self.output / 'manifest.json').read_text())
        self.assertFalse(report['complete'])
        self.assertEqual(report['files'], [])


if __name__ == '__main__':
    unittest.main()
