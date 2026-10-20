#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reject incomplete or unsafe source trees and malformed APEX containers."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('apex',
    Path(__file__).resolve().parents[1] / 'tools/build-vndk-apex.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class ApexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.files = {'apex_manifest.pb': build.vndk.MANIFEST,
                      'apex_pubkey': b'fixture-public-key', 'lib/fixture.so': b'fixture-library'}
        for name, data in self.files.items():
            path = self.base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.manifest = {'sourceImageSha256': build.vndk.STOCK_SHA, 'sourcePath': build.vndk.SOURCE,
                         'package': 'com.android.vndk.v30', 'format': 'flattened',
                         'files': [{'path': name, 'bytes': len(data), 'sha256': build.sha(data)}
                                   for name, data in self.files.items()]}
        roster = sorted((name, len(data), build.sha(data)) for name, data in self.files.items())
        self.fixture_hash = build.sha(json.dumps(roster, separators=(',', ':')).encode())

    def read(self, manifest):
        with patch.object(build, 'TREE_SHA', self.fixture_hash):
            return build.read_sources(manifest, self.base)

    def test_complete_tree_and_pinned_roster(self):
        self.assertEqual(self.read(self.manifest), self.files)
        with self.assertRaisesRegex(ValueError, 'supported stock revision'):
            build.read_sources(self.manifest, self.base)
        m = copy.deepcopy(self.manifest)
        m['files'].append({'path': 'lib/extra.so', 'bytes': 1, 'sha256': build.sha(b'x')})
        (self.base / 'lib/extra.so').write_bytes(b'x')
        with self.assertRaisesRegex(ValueError, 'supported stock revision'):
            self.read(m)

    def test_escape_duplicate_and_wrong_revision(self):
        for name in ('../secret', '/absolute', 'lib/../secret', 'lib//secret'):
            m = copy.deepcopy(self.manifest)
            m['files'][0]['path'] = name
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.read(m)
        m = copy.deepcopy(self.manifest)
        m['files'].append(m['files'][0])
        with self.assertRaises(ValueError):
            self.read(m)
        with self.assertRaises(ValueError):
            self.read({**self.manifest, 'sourceImageSha256': '0' * 64})

    def test_changed_file_and_symlink_rejected(self):
        path = self.base / 'lib/fixture.so'
        path.write_bytes(b'corruption')
        with self.assertRaises(ValueError):
            self.read(self.manifest)
        path.unlink()
        path.symlink_to(self.base / 'apex_manifest.pb')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.read(self.manifest)

    def container(self, alignment=4096, extra=False):
        path = self.base / 'test.apex'
        with zipfile.ZipFile(path, 'w') as z:
            build.stored_entry(z, 'apex_payload.img', b'payload', alignment)
            build.stored_entry(z, 'apex_pubkey', b'public')
            build.stored_entry(z, 'apex_manifest.pb', b'manifest')
            build.stored_entry(z, 'AndroidManifest.xml', b'xml')
            build.stored_entry(z, 'resources.arsc', b'resources')
            if extra:
                build.stored_entry(z, 'unexpected.bin', b'x')
        return path

    def test_payload_page_alignment(self):
        path = self.container()
        self.assertEqual(build.inspect_container(path, build.sha(b'payload'), b'public', b'manifest'), 4096)
        path = self.container(alignment=4)
        with self.assertRaisesRegex(ValueError, 'page-aligned'):
            build.inspect_container(path, build.sha(b'payload'), b'public', b'manifest')

    def test_changed_payload_or_metadata_rejected(self):
        path = self.container()
        for digest, key, manifest in ((build.sha(b'other'), b'public', b'manifest'),
                                      (build.sha(b'payload'), b'other', b'manifest'),
                                      (build.sha(b'payload'), b'public', b'other')):
            with self.assertRaises(ValueError):
                build.inspect_container(path, digest, key, manifest)

    def test_extra_or_duplicate_entry_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unexpected APEX entries'):
            build.inspect_container(self.container(extra=True), build.sha(b'payload'), b'public', b'manifest')
        with zipfile.ZipFile(self.base / 'duplicate.zip', 'w') as z:
            build.stored_entry(z, 'test', b'x')
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                build.stored_entry(z, 'test', b'y')


if __name__ == '__main__':
    unittest.main()
