#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Synthetic same-device bond fixtures; no real addresses or keys."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    'bond', Path(__file__).resolve().parents[1] / 'tools/prepare-remote-bond.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
ADAPTER = '02:00:00:00:00:01'
REMOTE = '02:00:00:00:00:02'


def stock():
    return ('[Adapter]\nAddress = ' + ADAPTER + '\n' +
            ''.join(k + ' = ' + '11' * 16 + '\n' for k in build.LOCAL_KEYS) +
            '\n[' + REMOTE + ']\nName = ' + build.REMOTE_NAME +
            '\nLE_KEY_PENC = ' + '22' * 28 + '\nHidDescriptor = aabb\n').encode()


def google():
    return ('[Adapter]\nAddress = ' + ADAPTER + '\nName = Keep me\n'
            '\n[Info]\nTime = 123\n').encode()


class BondTests(unittest.TestCase):
    def test_one_bond_and_unrelated_google_values_preserved(self):
        raw = google()
        merged = build.parse(build.merge(stock(), raw))
        self.assertEqual(merged['Adapter']['Name'], 'Keep me')
        self.assertEqual(merged['Info']['Time'], '123')
        self.assertEqual(merged[REMOTE]['LE_KEY_PENC'], '22' * 28)
        self.assertEqual(raw, google())

    def test_other_google_peer_is_not_overwritten(self):
        other = google() + b'\n[02:00:00:00:00:03]\nName = Other device\n'
        with self.assertRaisesRegex(ValueError, 'Other Google bonds'):
            build.merge(stock(), other)

    def test_different_adapter_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'identities differ'):
            build.merge(stock(), google().replace(ADAPTER.encode(), b'02:00:00:00:00:04'))

    def test_missing_keys_and_invalid_descriptor_are_rejected(self):
        for before, after in ((b'LE_LOCAL_KEY_IRK', b'OLD_KEY'),
                              (b'22' * 28, b'22' * 27), (b'aabb', b'aab')):
            with self.subTest(before=before[:15]), self.assertRaises(ValueError):
                build.merge(stock().replace(before, after), google())

    def test_same_remote_is_replaced_not_duplicated(self):
        prior = google() + ('\n[' + REMOTE + ']\nName = Old\n').encode()
        first = build.merge(stock(), prior)
        self.assertEqual(first, build.merge(stock(), first))
        self.assertEqual(build.parse(first).sections().count(REMOTE), 1)

    def test_ambiguous_and_malformed_input_is_rejected_without_echoing_it(self):
        for data in (stock() + b'\n[Adapter]\nSecret = synthetic-private-marker\n',
                     b'[DEFAULT]\nName = synthetic-private-marker\n' + stock(),
                     b'[Adapter]\nAddress = not-an-address\n',
                     b'\xff', b'\0'):
            try:
                build.merge(data, google())
            except ValueError as error:
                self.assertNotIn('synthetic-private-marker', str(error))
            else:
                self.fail('Malformed configuration accepted')

    def test_cli_private_outputs_readback_and_no_input_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            originals = {'stock': stock(), 'conf': google(), 'bak': google()}
            for name, raw in originals.items():
                (base / name).write_bytes(raw)
            command = [sys.executable, str(Path(build.__file__)),
                       '--stock-config', str(base / 'stock'),
                       '--google-config', str(base / 'conf'),
                       '--google-backup', str(base / 'bak')]
            out = base / 'result'
            result = subprocess.run(command + ['--output', str(out)], capture_output=True,
                                    text=True, check=True)
            self.assertFalse(json.loads(result.stdout)['installed'])
            self.assertNotIn(REMOTE, result.stdout)
            self.assertNotIn('22' * 28, result.stdout)
            self.assertEqual(out.stat().st_mode & 0o077, 0)
            for name, raw in originals.items():
                self.assertEqual((base / name).read_bytes(), raw)
            for name in ('conf', 'bak'):
                target = out / ('bt_config.' + name)
                self.assertEqual(target.stat().st_mode & 0o077, 0)
                self.assertEqual(target.read_bytes(), build.merge(stock(), google()))
                self.assertEqual((out / ('before.' + name)).read_bytes(), originals[name])
            again = subprocess.run(command + ['--output', str(out)], capture_output=True)
            self.assertNotEqual(again.returncode, 0)

    def test_cli_refuses_repository_output_before_reading_private_inputs(self):
        command = [sys.executable, str(Path(build.__file__)),
                   '--stock-config', '/nonexistent-private-input',
                   '--google-config', '/nonexistent-private-input',
                   '--google-backup', '/nonexistent-private-input',
                   '--output', str(build.ROOT)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('outside the repository', result.stderr)


if __name__ == '__main__':
    unittest.main()
