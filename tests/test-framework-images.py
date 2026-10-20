#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate framework change boundaries and metadata parser rejection paths."""
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
spec = importlib.util.spec_from_file_location('framework',
    Path(__file__).resolve().parents[1] / 'tools/build-framework-images.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class FrameworkTests(unittest.TestCase):
    def test_preserves_existing_manifest_bytes(self):
        source = b'<manifest version="1.0"><hal>keep</hal><vendor-ndk><version>34</version></vendor-ndk></manifest>'
        result = build.add_vndk_manifest(source)
        insertion = b'    <vendor-ndk>\n        <version>30</version>\n    </vendor-ndk>\n'
        self.assertEqual(result.replace(insertion, b''), source)
        with self.assertRaises(ValueError):
            build.add_vndk_manifest(result)

    def test_missing_duplicate_and_wrong_revision_rejected(self):
        for xml in (b'<manifest/>', b'<manifest></manifest>',
                    b'<manifest><vendor-ndk><version>33</version></vendor-ndk></manifest>',
                    b'<manifest><vendor-ndk><version>34</version></vendor-ndk><vendor-ndk><version>34</version></vendor-ndk></manifest>',
                    b'<notmanifest><vendor-ndk><version>34</version></vendor-ndk></notmanifest>'):
            with self.subTest(xml=xml), self.assertRaises(ValueError):
                build.add_vndk_manifest(xml)

    def test_invalid_erofs_header_rejected_without_assertions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.img'
            data = bytearray(4096)
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                build.ErofsMetadata(path)
            struct.pack_into('<I', data, 1024, 0xe0f5e1e2)
            data[1036] = 12
            struct.pack_into('<I', data, 1104, 2)
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                build.ErofsMetadata(path)


if __name__ == '__main__':
    unittest.main()
