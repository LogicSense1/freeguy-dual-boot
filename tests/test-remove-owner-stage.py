#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


m = load("remove_stage", ROOT / "tools/remove-owner-stage.py")
fixture = load("owner_fixture", ROOT / "tests/test-stage-owner-capture.py")


class Device(fixture.fixtures.FakeDevice):
    normal = True

    def normal_start(self):
        if not self.normal:
            raise ValueError("still in capture boot")

    def directory_names(self):
        return [Path(p).name for p in self.nodes if str(Path(p).parent) == m.owner.inventory.LOADER]

    def unlink(self, path):
        self.write(lambda: self.nodes.pop(path))

    def remove_directory(self):
        assert not self.directory_names()
        self.write(lambda: self.nodes.pop(m.owner.inventory.LOADER))


class Tests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.Tests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.plan = self.fixture.plan()

    def staged(self, name):
        d = Device()
        d.boot = "12345678-1234-1234-1234-123456789abc"
        out = self.root / name
        out.mkdir()
        staged = m.stage.stage(d, self.plan, out, True)
        # Captured output is a separate, retained directory, not part of cleanup.
        d.nodes[m.owner.CAPTURE] = d.node(900, 0o40700)
        d.writes = 0
        capture = dict(complete=True, bootId="87654321-4321-4321-4321-cba987654321",
                       deviceBindingSha256=self.plan["deviceBinding"],
                       deviceBindingScheme=self.plan["deviceBindingScheme"])
        return d, staged, capture

    def run_remove(self, d, s, c, name, apply=True):
        out = self.root / name
        out.mkdir()
        return m.remove(d, self.plan, s, c, out, apply)

    def test_complete_removes_only_two_files_and_empty_directory(self):
        d, s, c = self.staged("stage")
        before = set(d.nodes)
        r = self.run_remove(d, s, c, "remove")
        self.assertTrue(r["complete"])
        self.assertEqual(before - set(d.nodes),
                         {m.owner.inventory.LOADER, m.owner.inventory.LOADER + "/envdaily", m.owner.MARKER})
        self.assertIn(m.owner.CAPTURE, d.nodes)
        self.assertEqual(d.nodes["/unrelated-owner-data"]["data"], b"keep unchanged")

    def test_inspection_has_no_mutations(self):
        d, s, c = self.staged("stage")
        r = self.run_remove(d, s, c, "inspect", False)
        self.assertTrue(r["inspectionComplete"])
        self.assertEqual(d.writes, 0)

    def test_every_removal_and_sync_boundary_can_fail_without_completion(self):
        d, s, c = self.staged("reference-stage")
        self.run_remove(d, s, c, "reference-remove")
        for i in range(1, d.writes + 1):
            for when in ("before", "after"):
                x, staged, capture = self.staged(f"stage-{i}-{when}")
                x.fault = (i, when)
                name = f"remove-{i}-{when}"
                with self.assertRaises(RuntimeError):
                    self.run_remove(x, staged, capture, name)
                r = json.loads((self.root / name / "journal.json").read_text())
                self.assertFalse(r["complete"])
                self.assertIsNotNone(r["pending"])
                self.assertIn(m.owner.CAPTURE, x.nodes)

    def test_live_capture_boot_extra_files_and_changed_loader_refused(self):
        for case in ("same-boot", "temporary-args", "extra-file", "changed-loader", "wrong-owner"):
            d, s, c = self.staged("stage-" + case)
            if case == "same-boot":
                c["bootId"] = d.boot
            elif case == "temporary-args":
                d.normal = False
            elif case == "extra-file":
                d.nodes[m.owner.inventory.LOADER + "/owner-file"] = d.node(999, 0o100600, data=b"keep")
            elif case == "wrong-owner":
                c["deviceBindingSha256"] = "0" * 64
            else:
                p = m.owner.inventory.LOADER + "/envdaily"
                d.nodes[p]["data"] = b"x" * d.nodes[p]["bytes"]
            with self.assertRaises(ValueError):
                self.run_remove(d, s, c, "remove-" + case)
            self.assertEqual(d.writes, 0)


if __name__ == "__main__":
    unittest.main()
