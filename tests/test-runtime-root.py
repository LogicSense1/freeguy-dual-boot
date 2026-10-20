#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reject unsafe runtime inputs before assembling a local firmware archive."""
import base64
import importlib.util
from pathlib import Path
import struct
import sys
import unittest

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('root_build',
    Path(__file__).resolve().parents[1] / 'tools/assemble-runtime-root.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def public_key():
    # Layout fixture, not an operational key or device identifier.
    raw = struct.pack('<I', 64) + b'\0' * 516 + struct.pack('<I', 65537)
    return base64.b64encode(raw) + b' fixture\n'


def immutable():
    return {name: 'a' * 64 for name, _, _, mode in build.LAYOUT if mode == 'ro'}


class RuntimeRootTests(unittest.TestCase):
    def test_only_one_android_public_key(self):
        build.validate_adb_key(public_key())
        for value in (b'', b'-----BEGIN PRIVATE KEY-----\n', public_key() * 2,
                      public_key().replace(b'fixture', b'bad\tcomment'),
                      base64.b64encode(b'invalid structure') + b'\n',
                      b'%%% fixture\n', public_key().rstrip(b'\n')):
            with self.subTest(value_length=len(value)), self.assertRaises(ValueError):
                build.validate_adb_key(value)

    def test_mount_map_cannot_accept_mutable_or_unexpected_hashes(self):
        for changed in ({**immutable(), 'userdata.img': 'b' * 64},
                        {**immutable(), '../system.img': 'b' * 64},
                        {k: v for k, v in immutable().items() if k != 'system.img'}):
            with self.assertRaises(ValueError):
                build.mount_files(changed)
        for value in ('x' * 64, 'a' * 63, 'a' * 64 + '\nreboot', 10):
            with self.assertRaises(ValueError):
                build.mount_files({**immutable(), 'system.img': value})

    def test_board_loop_contract_and_readonly_index(self):
        mounts, index = build.mount_files(immutable())
        self.assertEqual([row[2] for row in build.LAYOUT[15:]], ['/data', '/metadata', '/cache'])
        self.assertEqual(len(mounts.splitlines()), 18)
        self.assertNotIn(b'image_mount system.img', mounts)
        for row in build.LAYOUT:
            self.assertEqual(row[0].encode() in index, row[3] == 'ro')
        self.assertNotIn(b'/dev/block/mmcblk', mounts)

    def call_assemble(self, seed, overlays=None):
        if overlays is None:
            overlays = {name: b'fixture' for name in build.BOARD + build.AUDIO}
        return build.assemble(seed, {'helper': b'expected'}, overlays, b'policy', public_key(),
                              immutable(), b'entry', b'mounts', b'permissions', b'starter')

    def test_unexpected_overlay_is_not_packaged(self):
        with self.assertRaisesRegex(ValueError, 'Unexpected overlay'):
            self.call_assemble([], {'../secret': b'no'})

    def test_seed_cannot_replace_existing_runtime(self):
        for name in ('codex', 'codex/entry.sh', 'first_stage_ramdisk/codex/entry.sh'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'already contains'):
                self.call_assemble([{'name': name}])

    def test_wrong_bootstrap_helper_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'helper mismatch'):
            self.call_assemble([{'name': 'system/bin/init', 'data': b'wrong'}])


if __name__ == '__main__':
    unittest.main()
