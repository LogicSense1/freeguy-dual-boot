#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Failure contracts for local input pinning and boot-policy integration."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('component_build',
    Path(__file__).resolve().parents[1] / 'tools/assemble-runtime-components.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

ENTRY = b'''#!/system/bin/sh
# watchdog-daily TRANSITION_GUARD_PID shared-default-v1
mkdir -p /dev/codex-autovoice
    /system/bin/sh /dev/codex-autovoice/start.sh > /data/local/tmp/codex-fileboot-logs/remote-voice-startup.txt 2>&1 &
            if [ "$(getprop sys.boot_completed)" = 1 ] &&
               pidof system_server; then echo healthy; fi
'''


class IntegrationTests(unittest.TestCase):
    def test_changed_local_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            path = base / 'input'
            path.write_bytes(b'before')
            record = {'path': 'input', 'sha256': hashlib.sha256(b'before').hexdigest()}
            self.assertEqual(build.read_input(record, base), b'before')
            path.write_bytes(b'after')
            with self.assertRaises(ValueError):
                build.read_input(record, base)

    def test_symlink_or_invalid_input_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            (base / 'actual').write_bytes(b'payload')
            (base / 'link').symlink_to(base / 'actual')
            record = {'path': 'link', 'sha256': hashlib.sha256(b'payload').hexdigest()}
            with self.assertRaises(ValueError):
                build.read_input(record, base)
            for value in ({}, {'path': 'actual', 'sha256': 'not-a-hash'}, {**record, 'extra': 1}):
                with self.assertRaises(ValueError):
                    build.read_input(value, base)

    def test_incompatible_or_repeated_integration_is_rejected(self):
        patched = build.patch_entry(ENTRY)
        with self.assertRaises(ValueError):
            build.patch_entry(patched)
        for bad in (ENTRY.replace(b'watchdog-daily', b'watchdog'),
                    ENTRY + ENTRY,
                    ENTRY.replace(b'mkdir -p /dev/codex-autovoice\n', b''),
                    ENTRY.replace(b'sys.boot_completed', b'other.property')):
            with self.assertRaises(ValueError):
                build.patch_entry(bad)

    def test_unrelated_startup_lines_are_preserved(self):
        text = b'critical-before\n' + ENTRY + b'critical-after\n'
        patched = build.patch_entry(text)
        self.assertTrue(patched.startswith(b'critical-before\n'))
        self.assertTrue(patched.endswith(b'critical-after\n'))
        self.assertIn(b'runtime-policy-start.sh > ', patched)
        self.assertIn(b'|| exit 1', patched)
        self.assertIn(b'codex-runtime-policy-ready 2>/dev/null)', patched)

    def test_archive_writer_preserves_modes_owners_and_symlinks(self):
        def e(name, mode, uid, gid, data):
            return {'name': name, 'fields': [1, mode, uid, gid, 1, 123, 0, 0, 0, 0, 0, 0, 0], 'data': data}
        rows = [e('root', 0o40700, 1000, 1000, b''),
                e('root/code', 0o100700, 1000, 2000, b'\x00\xffbinary'),
                e('init', 0o120777, 0, 0, b'/root/code')]
        decoded = build.archive.entries(build.pack(rows))
        for old, new in zip(rows, decoded):
            self.assertEqual((old['name'], old['fields'][1:6], old['data']),
                             (new['name'], new['fields'][1:6], new['data']))


if __name__ == '__main__':
    unittest.main()
