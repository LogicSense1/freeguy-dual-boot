#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
import base64
import hashlib
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('fresh', Path(__file__).resolve().parents[1] / 'tools/create-data-images.py')
fresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fresh)


class FreshImagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        raw = bytearray(524)
        struct.pack_into('<I', raw, 0, 64)
        struct.pack_into('<I', raw, 520, 65537)
        self.key = self.root / 'fixture.pub'
        self.key.write_bytes(base64.b64encode(raw) + b' synthetic-fixture\n')
        self.digest = hashlib.sha256(self.key.read_bytes()).hexdigest()
        self.output = self.root / 'new'

    def build(self, **changes):
        args = dict(key_path=self.key, key_sha256=self.digest, docker=Path('/unused/docker'),
                    container='local/fixture', output=self.output)
        args.update(changes)
        return fresh.build(**args)

    def test_repository_output_and_alias_rejected_before_tools(self):
        alias = self.root / 'checkout'
        alias.symlink_to(fresh.ROOT, target_is_directory=True)
        for output in (fresh.ROOT / 'private-data-fixture', alias / 'private-data-fixture'):
            with patch.object(fresh.subprocess, 'run') as run, \
                 self.assertRaisesRegex(ValueError, 'outside the repository'):
                self.build(output=output)
            run.assert_not_called()
            self.assertFalse(output.exists())

    def test_existing_output_is_preserved_without_running_tools(self):
        self.output.mkdir()
        sentinel = self.output / 'userdata.img'
        sentinel.write_bytes(b'existing private data')
        with patch.object(fresh.subprocess, 'run') as run, self.assertRaises(ValueError):
            self.build()
        run.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b'existing private data')

    def test_dangling_output_symlink_is_refused(self):
        self.output.symlink_to(self.root / 'absent')
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse((self.root / 'absent').exists())

    def test_key_hash_mismatch_does_not_create_output(self):
        with self.assertRaises(ValueError):
            self.build(key_sha256='0' * 64)
        self.assertFalse(self.output.exists())

    def test_private_key_or_symlink_is_refused(self):
        alias = self.root / 'alias.pub'
        alias.symlink_to(self.key)
        with self.assertRaises(ValueError):
            self.build(key_path=alias)
        self.key.write_bytes(b'not an Android public key\n')
        with self.assertRaises(ValueError):
            self.build(key_sha256=hashlib.sha256(self.key.read_bytes()).hexdigest())
        self.assertFalse(self.output.exists())

    def test_container_and_csv_mount_injection_are_rejected(self):
        with self.assertRaises(ValueError):
            self.build(container='local/fixture; touch /outside')
        with self.assertRaises(ValueError):
            self.build(output=self.root / 'x,readonly')
        self.assertFalse(self.output.exists())

    def test_disk_reserve_failure_happens_before_any_output(self):
        with patch.object(fresh.shutil, 'disk_usage') as disk:
            disk.return_value.free = 1024
            with self.assertRaises(ValueError):
                self.build()
        self.assertFalse(self.output.exists())

    def test_labels_are_file_backed_not_lossy_command_arguments(self):
        body = fresh.commands('userdata', 'system_data_file')
        self.assertEqual(body.count('ea_set -f '), 4)
        self.assertNotIn('u:object_r:', body)
        self.assertIn('write /work/adb_keys /misc/adb/adb_keys', body)
        self.assertEqual(fresh.commands('cache', 'cache_file').count('write '), 0)

    def test_fixed_geometry_is_the_existing_runtime_contract(self):
        self.assertEqual(fresh.IMAGES, (('userdata', 6144, 'system_data_file'),
                                       ('metadata', 64, 'metadata_file'), ('cache', 64, 'cache_file')))


if __name__ == '__main__':
    unittest.main()
