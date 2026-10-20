#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Same-TV pairing transaction and recovery using synthetic addresses and keys."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load("remote_installer", "tools/install-first-remote.py")
fixtures = load("bond_fixtures", "tests/test-remote-bond.py")


class Device:
    def __init__(self, fault=None):
        self.nodes, self.data = {}, {}
        self.index = 1
        self.nodes[m.DIR] = self.node(0o40770, 4096)
        for n in m.NAMES:
            self.data[m.path(n)] = fixtures.google()
            self.nodes[m.path(n)] = self.node(0o100660, len(fixtures.google()))
        self.stock = fixtures.stock()
        self.state, self.process = "ON", True
        self.fault, self.writes = fault, 0
        self.boot = "synthetic-boot"
        self.helpers = False
        self.bound = dict(bootId=self.boot, watchdog="100", deviceBinding="a" * 64, scheme="fixture")
        self.events = []

    def node(self, mode, size):
        self.index += 1
        return dict(dev=1, ino=self.index, uid=1002, gid=1002, mode=mode,
                    links=1, bytes=size, label=m.LABEL)

    def mutation(self, name, operation):
        self.writes += 1
        self.events.append(name)
        if self.fault == (self.writes, "before"):
            raise OSError("interrupted before mutation")
        operation()
        if self.fault == (self.writes, "after"):
            raise OSError("interrupted after mutation")

    def guard(self):
        return dict(self.bound, bootId=self.boot)

    def processes_clear(self):
        m.need(not self.helpers, "Helpers running")

    def metadata(self, value):
        return copy.deepcopy(self.nodes.get(value))

    def read(self, value):
        m.file_ok(self.metadata(value))
        return self.data[value]

    def stock_read(self):
        return self.stock

    def bt_state(self):
        return self.state

    def bt_set(self, enabled):
        def change():
            self.state = "ON" if enabled else "OFF"
            if enabled:
                self.process = True
        self.mutation("enable" if enabled else "disable", change)

    def wait_state(self, target):
        m.need(self.state == target, "Wrong Bluetooth state")

    def stop_process(self):
        self.mutation("stop-process", lambda: setattr(self, "process", False))

    def quiet(self):
        self.processes_clear()
        m.need(self.state == "OFF" and not self.process, "Bluetooth not quiescent")

    def upload(self, value, stream, event):
        self.quiet()
        assert value not in self.nodes
        raw = stream.read()
        event(dict(finished=False, localAdbPid=101, remotePid=202))
        def partial():
            self.data[value] = raw[:len(raw)//2]
            self.nodes[value] = self.node(0o100600, len(self.data[value]))
            self.nodes[value].update(uid=0, gid=0, label="u:object_r:system_data_file:s0")
        self.mutation("upload-partial", partial)
        def complete():
            self.data[value] = raw
            self.nodes[value]["bytes"] = len(raw)
        self.mutation("upload-complete", complete)
        event(dict(finished=True, localAdbPid=101, remotePid=202))

    def set_metadata(self, value, meta):
        for keys in (("uid", "gid"), ("mode",), ("label",)):
            self.mutation("metadata", lambda keys=keys: self.nodes[value].update(
                {k: meta[k] for k in keys}))

    def fsync(self, value):
        self.mutation("flush", lambda: None)

    def rename(self, source, target):
        self.quiet()
        assert target not in self.nodes
        def move():
            self.nodes[target] = self.nodes.pop(source)
            self.data[target] = self.data.pop(source)
        self.mutation("rename", move)

    def bond_listed(self, remote):
        return (remote == fixtures.REMOTE and self.state == "ON"
                and all(remote in m.bond.parse(self.data[m.path(n)]) for n in m.NAMES))


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_inspection_has_no_writes(self):
        d = Device()
        result = m.install(d, self.root / "inspect")
        self.assertTrue(result["inspectionComplete"])
        self.assertFalse(result["complete"])
        self.assertEqual(d.writes, 0)

    def test_success_retains_original_inodes_and_private_copies(self):
        d = Device()
        before = copy.deepcopy(d.nodes)
        result = m.install(d, self.root / "install", True)
        self.assertTrue(result["complete"])
        self.assertTrue(result["bondListed"])
        for n in m.NAMES:
            self.assertEqual(d.nodes[m.path(n, "before")], before[m.path(n)])
            self.assertEqual(d.read(m.path(n, "before")), fixtures.google())
            self.assertEqual(d.read(m.path(n)), m.bond.merge(fixtures.stock(), fixtures.google()))
            self.assertEqual((self.root / "install" / ("before." + n)).read_bytes(), fixtures.google())
            self.assertEqual((self.root / "install" / ("prepared." + n)).stat().st_mode & 0o077, 0)
        self.assertEqual(d.stock, fixtures.stock())
        self.assertFalse(result["physicalRemoteVerified"])
        self.assertFalse(result["stockWritten"])
        self.assertFalse(result["rebootIssued"])
        self.assertGreater(d.events.index("rename"), d.events.index("upload-complete"))

    def test_every_primitive_interruption_can_restore_unchanged_originals(self):
        reference = Device()
        m.install(reference, self.root / "reference", True)
        for point in range(1, reference.writes + 1):
            for when in ("before", "after"):
                with self.subTest(point=point, when=when):
                    d = Device((point, when))
                    initial = copy.deepcopy(d.nodes)
                    output = self.root / f"install-{point}-{when}"
                    with self.assertRaises(OSError):
                        m.install(d, output, True)
                    journal = json.loads((output / "journal.json").read_text())
                    self.assertFalse(journal["complete"])
                    self.assertIsNotNone(journal["pending"])
                    d.fault = None
                    restored = m.restore(d, journal, output, self.root / f"restore-{point}-{when}")
                    self.assertTrue(restored["complete"])
                    self.assertEqual(d.state, "ON")
                    for n in m.NAMES:
                        self.assertEqual(d.nodes[m.path(n)], initial[m.path(n)])
                        self.assertEqual(d.data[m.path(n)], fixtures.google())
                    self.assertEqual(d.stock, fixtures.stock())

    def test_bad_metadata_occupied_predecessor_or_active_helpers_refused(self):
        for i, change in enumerate((
                lambda d: d.nodes[m.path("conf")].update(links=2),
                lambda d: d.nodes[m.path("bak")].update(mode=0o120777),
                lambda d: d.nodes.update({m.path("conf", "before"): d.node(0o100660, 1)}),
                lambda d: setattr(d, "helpers", True))):
            d = Device()
            change(d)
            with self.assertRaises(ValueError):
                m.install(d, self.root / str(i), True)
            self.assertEqual(d.writes, 0)

    def failed_after_commit(self, name):
        d = Device()
        original = d.bt_set
        def fail(enabled):
            if enabled:
                raise OSError("before re-enable")
            original(enabled)
        d.bt_set = fail
        out = self.root / name
        with self.assertRaises(OSError):
            m.install(d, out, True)
        d.bt_set = original
        return d, out, json.loads((out / "journal.json").read_text())

    def test_recovery_refuses_another_boot_and_changed_current_bond(self):
        for name in ("boot", "bond", "source"):
            d, out, journal = self.failed_after_commit(name)
            if name == "boot":
                d.boot = "different"
            elif name == "bond":
                d.data[m.path("conf")] += b"\n# later change\n"
                d.nodes[m.path("conf")]["bytes"] = len(d.data[m.path("conf")])
            else:
                (out / "before.conf").write_bytes(b"changed snapshot")
            before = copy.deepcopy(d.data)
            with self.assertRaises(ValueError):
                m.restore(d, journal, out, self.root / (name + "-restore"))
            self.assertEqual(d.data, before)

    def test_incomplete_pair_does_not_restart_bluetooth_without_recovery(self):
        d, out, journal = self.failed_after_commit("stopped")
        self.assertFalse(journal["complete"])
        self.assertEqual(d.state, "OFF")
        self.assertFalse(d.process)
        self.assertIsNotNone(journal["pending"])

    def test_bluetooth_restart_during_upload_stops_before_original_rename(self):
        d = Device()
        original = d.upload
        def restart(value, stream, event):
            original(value, stream, event)
            d.process = True
        d.upload = restart
        with self.assertRaises(ValueError):
            m.install(d, self.root / "unexpected-restart", True)
        self.assertNotIn("rename", d.events)
        for n in m.NAMES:
            self.assertEqual(d.data[m.path(n)], fixtures.google())

    def test_failed_durable_checkpoint_never_loses_originals(self):
        real_save = m.stage.save
        checkpoints = []
        def count(folder, record):
            checkpoints.append(record["pending"])
            real_save(folder, record)
        with patch.object(m.stage, "save", side_effect=count):
            m.install(Device(), self.root / "count", True)
        for point in range(1, len(checkpoints) + 1):
            with self.subTest(checkpoint=point):
                calls = [0]
                def fail_once(folder, record):
                    calls[0] += 1
                    if calls[0] == point:
                        raise OSError("checkpoint failed")
                    real_save(folder, record)
                d = Device()
                with patch.object(m.stage, "save", side_effect=fail_once), self.assertRaises(OSError):
                    m.install(d, self.root / f"checkpoint-{point}", True)
                for n in m.NAMES:
                    self.assertTrue(any(d.data.get(m.path(n, role)) == fixtures.google()
                                        for role in ("current", "before")))
                self.assertEqual(d.stock, fixtures.stock())


if __name__ == "__main__":
    unittest.main()
