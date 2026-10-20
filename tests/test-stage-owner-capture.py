#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Owner-stage integration with synthetic roots and the shared upload model."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


m = load("owner_stage", ROOT / "tools/stage-owner-capture.py")
fixtures = load("stage_fixtures", ROOT / "tests/test-first-install-stage.py")


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stock = self.root / "boot.img"
        with self.stock.open("wb") as f:
            f.truncate(75 * 1024**2)  # Sparse synthetic input; no OEM bytes.
        self.capture = self.root / "capture"
        self.capture.mkdir()
        (self.capture / "owner-capture").write_bytes(b"fixture")
        self.loader = self.root / "loader"
        self.loader.mkdir()
        (self.loader / "loader").write_bytes(b"synthetic owner loader")
        self.rows = [fixtures.fixture.entry("init", b"synthetic")]
        self.source = {"captureSha256": m.sha(b"fixture")}
        self.mock = patch.object(m.owner, "prepare", return_value=(self.rows, self.source))
        self.mock.start()
        self.addCleanup(self.mock.stop)
        read_bytes = Path.read_bytes
        reader = patch.object(Path, "read_bytes",
                              lambda p: b"synthetic stock" if p == self.stock else read_bytes(p))
        reader.start()
        self.addCleanup(reader.stop)
        components = {n: m.sha((ROOT / "runtime" / n).read_bytes()) for n in
                      ("internal-env-inspect.h", "internal-env-transform.c", "internal-env-once.h")}
        common = dict(sha256=m.sha((self.loader / "loader").read_bytes()),
                      sourceSha256=m.sha((ROOT / "runtime/loader.c").read_bytes()),
                      componentSources=components)
        self.manifest = dict(common, rootKind="owner-capture", ownerCapture=self.source,
                             entries=m.owner.bootstrap.archive.table(self.rows)[2])
        self.audit = dict(common, passed=True, originalEntriesVerified=len(self.rows),
                          cases=[dict(case=name) for name in sorted(m.inventory.AUDIT_CASES)])
        next(c for c in self.audit["cases"] if c["case"] == "not-pid1").update(
            end="unprivileged-exit", syscalls=2, environmentWrites=0)
        self.save()

    def save(self):
        (self.loader / "manifest.json").write_text(json.dumps(self.manifest))
        (self.loader / "audit.json").write_text(json.dumps(self.audit))

    def plan(self):
        return m.plan(self.stock, self.capture, self.loader,
                      fixtures.fixture.BINDING, fixtures.fixture.SCHEME)

    def run_stage(self, d, name, apply=True):
        out = self.root / name
        out.mkdir()
        return m.stage.stage(d, self.plan(), out, apply)

    def test_scope_and_complete_upload(self):
        plan = self.plan()
        self.assertEqual([r["target"] for r in plan["files"]], [m.inventory.LOADER + "/envdaily"])
        self.assertIn(m.CAPTURE, plan["mustBeAbsent"])
        self.assertIn(m.inventory.BASE, plan["mustBeAbsent"])
        d = fixtures.FakeDevice()
        original = copy.deepcopy(d.nodes)
        self.assertTrue(self.run_stage(d, "stage")["complete"])
        expected = {m.inventory.LOADER, m.inventory.LOADER + "/envdaily", m.MARKER}
        self.assertEqual(set(d.nodes) - set(original), expected)
        self.assertEqual(d.nodes["/unrelated-owner-data"], original["/unrelated-owner-data"])

    def test_inspection_never_writes(self):
        d = fixtures.FakeDevice()
        r = self.run_stage(d, "inspect", False)
        self.assertTrue(r["inspectionComplete"])
        self.assertEqual(d.writes, 0)

    def test_installed_or_partial_paths_refused(self):
        for i, path in enumerate(self.plan()["mustBeAbsent"]):
            d = fixtures.FakeDevice()
            d.nodes[path] = d.node(800, 0o40700)
            with self.assertRaises(ValueError):
                self.run_stage(d, "existing" + str(i))
            self.assertEqual(d.writes, 0)

    def test_every_write_interruption_keeps_incomplete_record(self):
        d = fixtures.FakeDevice()
        self.run_stage(d, "reference")
        for i in range(1, d.writes + 1):
            for when in ("before", "after"):
                x = fixtures.FakeDevice(fault=(i, when))
                name = f"fault-{i}-{when}"
                with self.assertRaises(RuntimeError):
                    self.run_stage(x, name)
                r = json.loads((self.root / name / "journal.json").read_text())
                self.assertFalse(r["complete"])
                self.assertIsNotNone(r["pending"])
                self.assertEqual(x.nodes["/unrelated-owner-data"]["data"], b"keep unchanged")

    def test_wrong_root_and_incomplete_audit_refused(self):
        original = copy.deepcopy(self.manifest)
        for key, value in (("rootKind", "daily"), ("entries", []), ("sourceSha256", "0" * 64)):
            self.manifest = dict(original, **{key: value})
            self.save()
            with self.assertRaises(ValueError):
                self.plan()
        self.manifest = original
        self.audit["cases"].pop()
        self.save()
        with self.assertRaises(ValueError):
            self.plan()

    def test_shared_verifier_accepts_only_complete_stage(self):
        d = fixtures.FakeDevice()
        d.boot = "12345678-1234-1234-1234-123456789abc"
        staged = self.run_stage(d, "staged")
        out = self.root / "verified"
        out.mkdir()
        def probe(device, loader, boot, event):
            event(dict(finished=True, remotePid=200))
            return 90
        r = m.verify.verify(d, self.plan(), staged, out, execute=True, runner=probe)
        self.assertTrue(r["complete"])
        self.assertEqual(r["filesVerified"], 2)
        self.assertEqual(r["probeExit"], 90)
        staged["complete"] = False
        with self.assertRaises(ValueError):
            m.verify.check_journal(staged, self.plan())


if __name__ == "__main__":
    unittest.main()
