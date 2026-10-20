#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""First-install staging refusals, interrupted writes and real shell noclobber."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage = load("stager", ROOT / "tools/stage-first-install.py")
fixture = load("fixtures", ROOT / "tests/test-first-install-plan.py")
sha = lambda b: hashlib.sha256(b).hexdigest()


class FakeDevice:
    def __init__(self, fault=None):
        self.nodes = {}
        self.writes = 0
        self.fault = fault
        self.space = 1 << 40
        self.boot = "fixture-original-B"
        self.guard_calls = 0
        self.reboot_at = None
        self.corrupt = False
        for i, path in enumerate(stage.PARENTS):
            self.nodes[path] = self.node(i + 1, 0o40771, uid=0, gid=0)
        self.nodes["/unrelated-owner-data"] = self.node(99, 0o100600, data=b"keep unchanged")

    @staticmethod
    def node(ino, mode, uid=2000, gid=2000, data=None):
        return dict(dev=1, ino=ino, mode=mode, uid=uid, gid=gid, links=1,
                    bytes=64 if data is None else len(data), data=data)

    def write(self, action):
        self.writes += 1
        if self.fault == (self.writes, "before"):
            raise RuntimeError("injected before write")
        action()
        if self.fault == (self.writes, "after"):
            raise RuntimeError("injected after write")

    def guard(self):
        self.guard_calls += 1
        if self.reboot_at and self.guard_calls >= self.reboot_at:
            raise ValueError("rebooted")
        return {"bootId": self.boot}

    def capabilities(self): pass
    def searchable(self, path): pass
    def free_bytes(self): return self.space

    def metadata(self, path):
        row = self.nodes.get(path)
        return None if row is None else {k: v for k, v in row.items() if k != "data"}

    def mkdir(self, path):
        if path in self.nodes:
            raise ValueError("existing")
        self.write(lambda: self.nodes.update({path: self.node(len(self.nodes) + 10, 0o40700)}))

    def fsync(self, path):
        assert path in self.nodes
        self.write(lambda: None)

    def digest(self, path):
        return sha(self.nodes[path]["data"])

    def chmod_loader(self, path):
        assert path == stage.inventory.LOADER + "/envdaily"
        self.write(lambda: self.nodes[path].update(mode=0o100700))

    def upload(self, path, stream, event):
        assert path not in self.nodes
        event(dict(localAdbPid=100, remotePid=200, finished=False))
        data = stream.read()
        if self.corrupt: data = data[:1]
        self.write(lambda: self.nodes.update({path: self.node(len(self.nodes) + 10, 0o100600, data=data)}))
        event(dict(localAdbPid=100, remotePid=200, finished=True))


class StageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        inputs = fixture.fixture(self.root)
        self.plan = stage.inventory.plan(inputs, self.root, fixture.BINDING, fixture.SCHEME)

    def run_stage(self, device, name, apply=True):
        out = self.root / name
        out.mkdir()
        return stage.stage(device, self.plan, out, apply=apply)

    def test_full_stage_readback_and_inspect_has_no_writes(self):
        device = FakeDevice()
        original = copy.deepcopy(device.nodes)
        inspected = self.run_stage(device, "inspect", apply=False)
        self.assertTrue(inspected["inspectionComplete"])
        self.assertFalse(inspected["deviceWritesIssued"])
        self.assertEqual(device.nodes, original)
        result = self.run_stage(device, "apply")
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["files"]), 22)
        self.assertIsNone(result["pending"])
        for field in ("startupChanged", "loaderExecuted", "rebootIssued"):
            self.assertFalse(result[field])
        for path, value in original.items():
            self.assertEqual(device.nodes[path], value)
        allowed = set(original) | set(self.plan["directories"]) | {
            row["target"] for row in self.plan["files"] + self.plan["markers"]}
        self.assertEqual(set(device.nodes), allowed)

    def test_existing_roots_shared_state_symlinks_and_low_space_fail_before_writes(self):
        for i, path in enumerate(self.plan["mustBeAbsent"]):
            for mode in (0o40700, 0o120777, 0o100600):
                device = FakeDevice()
                device.nodes[path] = device.node(500, mode)
                before = copy.deepcopy(device.nodes)
                with self.subTest(path=path, mode=mode), self.assertRaises(ValueError):
                    self.run_stage(device, "occupied-" + str(i) + "-" + str(mode))
                self.assertEqual(device.writes, 0)
                self.assertEqual(before, device.nodes)
        device = FakeDevice()
        device.space = self.plan["totalBytes"] + self.plan["freeSpaceReserveBytes"] - 1
        with self.assertRaises(ValueError):
            self.run_stage(device, "space")
        self.assertEqual(device.writes, 0)

    def test_parent_symlink_or_other_filesystem_refused(self):
        for field, value in (("mode", 0o120777), ("dev", 2)):
            device = FakeDevice()
            device.nodes["/data/local/tmp"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.run_stage(device, "parent-" + field)
            self.assertEqual(device.writes, 0)

    def test_metadata_distinguishes_missing_denied_and_malformed_results(self):
        device = stage.Device("unused-adb", "fixture", self.plan)
        path = stage.inventory.LOADER
        for message in ("stat: '" + path + "': Permission denied\nCTV_STAT_RC 1",
                        "stat: '" + path + "': Input/output error\nCTV_STAT_RC 1",
                        "unknown\nCTV_STAT_RC 1", "1 2 41c0 2000 2000 1 64"):
            with patch.object(device, "shell", return_value=message), self.assertRaises(ValueError):
                device.metadata(path)
        with patch.object(device, "shell", return_value="stat: '" + path +
                          "': No such file or directory\nCTV_STAT_RC 1"):
            self.assertIsNone(device.metadata(path))
        with patch.object(device, "shell", return_value="1 2 41c0 2000 2000 1 64\nCTV_STAT_RC 0"):
            self.assertEqual(device.metadata(path)["mode"], 0o40700)

    def test_every_mutation_interruption_retains_journal_and_unrelated_data(self):
        accepted = FakeDevice()
        self.run_stage(accepted, "count")
        count = accepted.writes
        self.assertEqual(count, 75)
        for point in range(1, count + 1):
            for when in ("before", "after"):
                device = FakeDevice((point, when))
                before = copy.deepcopy(device.nodes)
                name = "fault-" + str(point) + "-" + when
                with self.subTest(point=point, when=when), self.assertRaises(RuntimeError):
                    self.run_stage(device, name)
                journal = json.loads((self.root / name / "journal.json").read_text())
                self.assertFalse(journal["complete"])
                self.assertIsNotNone(journal["pending"])
                self.assertEqual(journal["failure"], "RuntimeError")
                for path, value in before.items():
                    self.assertEqual(device.nodes[path], value)
                self.assertFalse(journal["startupChanged"])
                self.assertFalse(journal["loaderExecuted"])

    def test_reboot_corrupt_upload_and_changed_local_file_stop(self):
        device = FakeDevice()
        device.reboot_at = 4
        with self.assertRaises(ValueError):
            self.run_stage(device, "reboot")
        device = FakeDevice()
        device.corrupt = True
        with self.assertRaises(ValueError):
            self.run_stage(device, "corrupt")
        self.assertNotIn(self.plan["markers"][0]["target"], device.nodes)
        path = Path(self.plan["files"][0]["source"])
        path.write_bytes(b"changed")
        device = FakeDevice()
        with self.assertRaises(ValueError):
            self.run_stage(device, "local-change")
        self.assertNotIn(self.plan["files"][0]["target"], device.nodes)

    def test_final_recheck_catches_earlier_file_change_and_reserve_loss(self):
        for failure in ("earlier-file", "reserve"):
            device = FakeDevice()
            original = device.upload
            marker = self.plan["markers"][0]["target"]
            def upload(path, stream, event):
                original(path, stream, event)
                if path == marker:
                    if failure == "earlier-file":
                        node = device.nodes[self.plan["files"][0]["target"]]
                        node["data"] = b"x" * node["bytes"]
                    else:
                        device.space = 0
            device.upload = upload
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                self.run_stage(device, failure)
            journal = json.loads((self.root / failure / "journal.json").read_text())
            self.assertIn(marker, device.nodes)
            self.assertFalse(journal["complete"])

    def test_real_host_shell_upload_preserves_existing_file_and_symlink(self):
        # This shim executes only the tool's upload shell in a temporary host
        # directory. It never invokes adb or references a physical device.
        shim = self.root / "adb-fixture"
        shim.write_text("#!/usr/bin/env python3\nimport os,sys\n"
                        "assert sys.argv[1:5]==['-s','fixture','shell','-T']\n"
                        "os.execv('/bin/sh',['sh','-c',sys.argv[5]])\n")
        shim.chmod(0o700)
        device = stage.Device(shim, "fixture", self.plan)
        target = self.root / "binary-target"
        device.allowed.add(str(target))
        source = self.root / "binary-source"
        content = bytes(range(256)) * 4096
        source.write_bytes(content)
        events = []
        with source.open("rb") as stream:
            device.upload(str(target), stream, events.append)
        self.assertEqual(target.read_bytes(), content)
        self.assertTrue(events[-1]["finished"])
        for test_target in (target, self.root / "symlink", self.root / "dangling"):
            if test_target != target:
                test_target.symlink_to(target if test_target.name == "symlink" else self.root / "absent")
            device.allowed.add(str(test_target))
            with source.open("rb") as stream, self.assertRaises(ValueError):
                device.upload(str(test_target), stream, lambda _: None)
            self.assertEqual(target.read_bytes(), content)
        self.assertFalse((self.root / "absent").exists())


if __name__ == "__main__":
    unittest.main()
