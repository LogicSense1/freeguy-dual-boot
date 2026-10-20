#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fixed updater plans must never include data images or startup partitions."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

builder = load("build-image-updater")
switch = load("build-switch-app")


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.old = dict(model="L75MA-SPL", device="freeguy", bootloader="01.01.250225",
                        immutableFiles={p: "1" * 64 for p in switch.EXPECTED},
                        normalizedBootargsSha256=["2" * 64], serialUndoVerified=True)
        self.new = deepcopy(self.old)
        for p in builder.CHANGED:
            self.new["immutableFiles"][p] = "3" * 64

    def test_fixed_four_files_and_retained_backup_names(self):
        plan = builder.plan(self.old, self.new, False)
        self.assertEqual({r["active"] for r in plan["rows"]}, set(builder.CHANGED))
        for row in plan["rows"]:
            self.assertEqual(row["backup"], row["active"] + ".previous-" + plan["id"])
            self.assertTrue(row["candidate"].startswith(builder.CANDIDATE + "/"))
            self.assertEqual(row["oldSha256"], "1" * 64)
            self.assertEqual(row["newSha256"], "3" * 64)
        self.assertEqual(len({p for r in plan["rows"] for p in
                              (r["active"], r["candidate"], r["backup"])}), 12)

    def test_probe_uses_only_disposable_files_and_distinct_id(self):
        actual = builder.plan(self.old, self.new, False)
        probe = builder.plan(self.old, self.new, True)
        self.assertNotEqual(actual["id"], probe["id"])
        for i, row in enumerate(probe["rows"]):
            for key in ("active", "candidate", "backup"):
                self.assertTrue(Path(row[key]).name.startswith(".update-probe-"))
            self.assertEqual(row["oldSha256"], hashlib.sha256(bytes([65+i])*4096).hexdigest())
            self.assertEqual(row["newSha256"], hashlib.sha256(bytes([97+i])*4096).hexdigest())

    def test_unchanged_or_partial_update_refused(self):
        for new in (deepcopy(self.old), deepcopy(self.new)):
            new["immutableFiles"][builder.CHANGED[0]] = "1" * 64
            with self.assertRaisesRegex(ValueError, "four planned"):
                builder.plan(self.old, new, False)

    def test_other_immutable_file_or_data_path_refused(self):
        extra = next(p for p in switch.EXPECTED if p not in builder.CHANGED)
        for path in (extra, builder.BASE + "/userdata.img", "/dev/block/by-name/boot_b"):
            changed = deepcopy(self.new)
            changed["immutableFiles"][path] = "4" * 64
            with self.assertRaises(ValueError):
                builder.plan(self.old, changed, False)

    def test_changed_board_boot_profile_or_missing_undo_refused(self):
        for key, value in (("device", "ladybird"), ("bootloader", "old"),
                           ("normalizedBootargsSha256", ["4" * 64]), ("serialUndoVerified", False)):
            changed = deepcopy(self.new)
            changed[key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                builder.plan(self.old, changed, False)

    def test_malformed_digest_or_input_path_refused(self):
        for digest in ("bad", '"; unwanted(); //', "A"*64):
            changed = deepcopy(self.new)
            changed["immutableFiles"][builder.CHANGED[0]] = digest
            with self.assertRaises(ValueError):
                builder.plan(self.old, changed, False)

    def test_plan_identity_deterministic_but_changes_with_input_hash(self):
        a = builder.plan(self.old, self.new, False)
        b = builder.plan(deepcopy(self.old), deepcopy(self.new), False)
        self.assertEqual(a, b)
        changed = deepcopy(self.new)
        changed["immutableFiles"][builder.CHANGED[0]] = "4" * 64
        self.assertNotEqual(a["id"], builder.plan(self.old, changed, False)["id"])

    def test_private_output_rejects_repo_existing_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            alias = root / "checkout"
            alias.symlink_to(ROOT, target_is_directory=True)
            dangling = root / "dangling"
            dangling.symlink_to(root / "missing")
            keep = root / "keep"
            keep.mkdir()
            sentinel = keep / "existing-data"
            sentinel.write_bytes(b"retain")
            for path in (ROOT / "private-updater-fixture", alias / "private-updater-fixture",
                         dangling, keep):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    builder.private_output(path)
            builder.private_output(root / "new")
            self.assertEqual(sentinel.read_bytes(), b"retain")
            self.assertFalse((root / "new").exists())

    def test_profile_file_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "profile.json"
            path.write_text(json.dumps(self.old))
            self.assertEqual(builder.read_profile(path), self.old)
            alias = root / "alias"
            alias.symlink_to(path)
            with self.assertRaises(ValueError):
                builder.read_profile(alias)
            for data in ("[]", "{}" + " " * 65536):
                path.write_text(data)
                with self.assertRaises(ValueError):
                    builder.read_profile(path)


if __name__ == "__main__":
    unittest.main()
