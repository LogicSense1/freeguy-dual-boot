#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Policy extraction must keep roles distinct and refuse non-text inputs."""
import hashlib
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'policy_inputs', Path(__file__).resolve().parents[1] / 'tools/extract-policy-inputs.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class PolicyInputsTests(unittest.TestCase):
    def test_profile_binds_every_input_to_its_own_bytes(self):
        reads = []

        def read(image, path):
            reads.append((image, path))
            return (image + ':' + path + '\n').encode()

        files, profile = build.assemble(read)
        self.assertEqual(len(files), 29)
        self.assertEqual(len(reads), len(set(reads)))
        self.assertNotEqual(profile['stockVendor']['sha256'], profile['donorVendor']['sha256'])
        for record in [*profile['inputs'].values(), *profile['contexts'].values(),
                       profile['stockVendor'], profile['donorVendor']]:
            self.assertEqual(record['sha256'], hashlib.sha256(files[record['path']]).hexdigest())
            self.assertEqual(Path(record['path']).name, record['path'])

    def test_invalid_or_excessive_text_is_rejected(self):
        for data in (b'\0', b'\xff', b'a' * (build.MAX_FILE + 1), 'not bytes'):
            with self.subTest(kind=type(data).__name__), self.assertRaises((ValueError, UnicodeError)):
                build.assemble(lambda image, path: data)

    def test_empty_compatibility_files_are_valid(self):
        files, profile = build.assemble(lambda image, path: b'')
        self.assertEqual(len(files), 29)
        self.assertEqual(profile['inputs']['system_ext-30.0.compat.cil']['sha256'],
                         hashlib.sha256(b'').hexdigest())


if __name__ == '__main__':
    unittest.main()
