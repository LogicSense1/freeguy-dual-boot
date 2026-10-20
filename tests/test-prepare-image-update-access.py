#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Synthetic device state; no shell or television connection."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("access", ROOT / "tools/prepare-image-update-access.py")
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)


def entry(ino, directory=False, size=4096):
    return dict(dev=1, ino=ino, mode=0o40755 if directory else 0o100640,
                uid=0, gid=1000, links=2 if directory else 1, size=size,
                label="u:object_r:shell_data_file:s0")


class FakeDevice:
    def __init__(self, plan):
        self.bound = dict(boot="same-boot", watchdog="100", board="same-board")
        self.entries = {}
        self.hashes = access.fixed_files(plan)
        for i, path in enumerate(access.DIRECTORIES + list(self.hashes) + access.DATA):
            self.entries[path] = entry(i + 10, path in access.DIRECTORIES)
        self.writes = []
        self.fail_at = None
        self.after_write = False

    def guard(self):
        pass

    def stat(self, path):
        return deepcopy(self.entries.get(path))

    def digest(self, path):
        return self.hashes[path]

    def mutate(self, kind, path, fn):
        number = len(self.writes) + 1
        if number == self.fail_at and not self.after_write:
            raise RuntimeError("injected interruption")
        fn()
        self.writes.append((kind, path))
        if number == self.fail_at:
            raise RuntimeError("injected interruption")

    def set_metadata(self, path, target):
        def change():
            for key in ("uid", "gid", "mode", "label"):
                self.entries[path][key] = target[key]
        self.mutate("metadata", path, change)

    def create_directory(self):
        def change():
            self.entries[access.UPDATE] = dict(entry(1000, True), mode=0o40700,
                                               uid=1000, gid=1000, label=access.LABEL)
        self.mutate("mkdir", access.UPDATE, change)

    def create_file(self, path, content, mode):
        def change():
            if path in self.entries:
                raise ValueError("occupied")
            self.entries[path] = dict(entry(2000 + len(self.entries), size=len(content)),
                                      mode=0o100000 | mode, uid=1000, label=access.LABEL)
            self.hashes[path] = hashlib.sha256(content).hexdigest()
            parent = str(Path(path).parent)
            if parent in self.entries:
                self.entries[parent]["size"] += 64
        self.mutate("create", path, change)

    def flush(self):
        self.mutate("flush", access.UPDATE, lambda: None)


class AccessTests(unittest.TestCase):
    def setUp(self):
        builder = access.load("build-image-updater")
        switch = access.load("build-switch-app")
        self.old = dict(model="L75MA-SPL", device="freeguy", bootloader="01.01.250225",
                        immutableFiles={p: "1"*64 for p in switch.EXPECTED},
                        normalizedBootargsSha256=["2"*64], serialUndoVerified=True)
        self.new = deepcopy(self.old)
        for p in builder.CHANGED:
            self.new["immutableFiles"][p] = "3"*64
        self.plan = builder.plan(self.old, self.new, False)
        self.probe = builder.plan(self.old, self.new, True)
        self.artifacts = {access.UPDATE + "/" + hashlib.sha256(b"dex").hexdigest() + ".dex": (b"dex", 0o600)}
        for i, row in enumerate(self.probe["rows"]):
            for key, byte in (("active", 65+i), ("candidate", 97+i)):
                self.artifacts[row[key]] = (bytes([byte]) * 4096, 0o750 if i == 3 else 0o640)
        self.device = FakeDevice(self.plan)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_prepare(self, inspect=False):
        return access.provision(self.device, self.plan, self.artifacts, self.root, inspect)

    def test_inspect_does_not_write_to_device(self):
        report = self.run_prepare(True)
        self.assertTrue(report["inspectOnly"])
        self.assertFalse(report["complete"])
        self.assertEqual(self.device.writes, [])

    def test_prepare_and_restore_preserve_all_existing_bytes_and_data_metadata(self):
        before = deepcopy(self.device.entries)
        hashes = deepcopy(self.device.hashes)
        report = self.run_prepare()
        self.assertTrue(report["complete"])
        for path, digest in hashes.items():
            self.assertEqual(self.device.digest(path), digest)
        for path in access.DATA:
            self.assertEqual(self.device.entries[path], before[path])
        access.restore_metadata(self.device, report, self.root)
        for path in access.DIRECTORIES + list(hashes):
            self.assertTrue(access.same_metadata(self.device.stat(path), before[path]))
        self.assertTrue(report["metadataRestored"])
        for path in self.artifacts:
            self.assertIn(path, self.device.entries)
        self.assertFalse(any(path.startswith("/dev/") or path in access.DATA
                             for _, path in self.device.writes))

    def test_every_write_interruption_leaves_unconfirmed_journal_and_no_auto_restore(self):
        # 11 metadata operations, one directory, nine files and final sync.
        for number in range(1, 23):
            for after in (False, True):
                with self.subTest(number=number, after=after):
                    self.device = FakeDevice(self.plan)
                    original_data = {p: self.device.stat(p) for p in access.DATA}
                    original_hashes = dict(self.device.hashes)
                    self.device.fail_at, self.device.after_write = number, after
                    with self.assertRaisesRegex(RuntimeError, "interruption"):
                        self.run_prepare()
                    record = json.loads((self.root / "journal.json").read_text())
                    self.assertFalse(record["complete"])
                    self.assertEqual(record["operations"][-1]["state"], "unconfirmed")
                    count = len(self.device.writes)
                    with self.assertRaisesRegex(ValueError, "completed preparation"):
                        access.restore_metadata(self.device, record, self.root)
                    self.assertEqual(len(self.device.writes), count)
                    self.assertEqual({p: self.device.stat(p) for p in access.DATA}, original_data)
                    self.assertTrue(all(self.device.digest(p) == h for p, h in original_hashes.items()))

    def test_wrong_hash_or_occupied_backup_refused_before_writes(self):
        self.device.hashes[self.plan["rows"][0]["active"]] = "4"*64
        with self.assertRaisesRegex(ValueError, "hash differs"):
            self.run_prepare()
        self.assertFalse(self.device.writes)
        self.device = FakeDevice(self.plan)
        self.device.entries[self.plan["rows"][0]["backup"]] = entry(9000)
        with self.assertRaisesRegex(ValueError, "Backup path"):
            self.run_prepare()
        self.assertFalse(self.device.writes)

    def test_partial_artifact_is_never_overwritten(self):
        p = next(iter(self.artifacts))
        self.device.entries[p] = dict(entry(2000), mode=0o100600, uid=1000, label=access.LABEL)
        self.device.hashes[p] = "0"*64
        with self.assertRaisesRegex(ValueError, "occupied or incomplete"):
            self.run_prepare()
        self.assertFalse(self.device.writes)

    def test_probe_identity_matches_builder_and_old_probe_backup_is_refused(self):
        self.assertEqual(access.probe_id(self.plan), self.probe["id"])
        self.device.entries[self.probe["rows"][0]["backup"]] = entry(9000)
        with self.assertRaisesRegex(ValueError, "Probe backup"):
            self.run_prepare()
        self.assertFalse(self.device.writes)

    def test_symlink_hardlink_or_bad_permissions_rejected(self):
        path = self.plan["rows"][0]["active"]
        for key, value in (("mode", 0o120777), ("links", 2),
                           ("gid", 0), ("mode", 0o100777), ("uid", 9999)):
            self.device = FakeDevice(self.plan)
            self.device.entries[path][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.run_prepare()
            self.assertFalse(self.device.writes)

    def test_restoration_refuses_changed_inode_image_or_unconfirmed_previous_restore(self):
        for kind in ("inode", "bytes", "pending"):
            self.device = FakeDevice(self.plan)
            record = self.run_prepare()
            path = self.plan["rows"][0]["active"]
            if kind == "inode":
                self.device.entries[path]["ino"] += 100
            elif kind == "bytes":
                self.device.hashes[path] = "4"*64
            else:
                record["restorePending"] = path
            count = len(self.device.writes)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                access.restore_metadata(self.device, record, self.root)
            self.assertEqual(count, len(self.device.writes))

    def test_data_or_partition_path_and_wrong_probe_content_refused(self):
        for path in (access.DATA[0], "/dev/block/by-name/boot_b"):
            self.artifacts[path] = (b"dex", 0o600)
            with self.assertRaisesRegex(ValueError, "destination"):
                self.run_prepare()
            del self.artifacts[path]
        p = self.probe["rows"][0]["active"]
        self.artifacts[p] = (b"B"*4096, 0o640)
        with self.assertRaisesRegex(ValueError, "probe contents"):
            self.run_prepare()
        self.assertFalse(self.device.writes)

    def test_metadata_parser_and_directory_size_changes(self):
        parsed = access.metadata("1 42 81a0 0 1000 1 4096 u:object_r:system_data_file:s0")
        self.assertEqual(parsed["mode"], 0o100640)
        for bad in ("1 42 a1ff 0 1000 1 4096 u:object_r:system_data_file:s0",
                    "1 42 81a0 0 1000 1 4096 bad;reboot"):
            with self.assertRaises(ValueError):
                access.metadata(bad)
        original = entry(42, True)
        changed = dict(original, size=8192)
        self.assertTrue(access.same_metadata(changed, original))
        changed["ino"] += 1
        self.assertFalse(access.same_metadata(changed, original))


if __name__ == "__main__":
    unittest.main()
