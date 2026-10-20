#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Join real-shaped evidence; no TV, firmware or serial device is used."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load("activate", ROOT / "tools/activate-first-entry.py")
fixture = load("owner_fixture", ROOT / "tests/test-plan-owner-activation.py")


class Tests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.Tests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.f.root
        self.plan = self.f.plan
        self.binding = dict(deviceBindingSha256=self.plan["deviceBinding"],
                            deviceBindingScheme=self.plan["deviceBindingScheme"])
        self.stock = fixture.STOCK
        self.allowed = fixture.ALLOWED
        self.target = self.f.assemble()["temporaryBootargs"]
        small = patch.object(m, "PREFIX_BYTES", 1024)
        small.start()
        self.addCleanup(small.stop)
        self.backup = dict(version=1, **self.binding, prefixBytes=1024, chunks=[
            dict(offset=0, file=self.write("chunk0", b"a" * 512)),
            dict(offset=512, file=self.write("chunk1", b"b" * 512))])
        b = lambda value: ("bootargs=" + value + "\r\nfreeguy#").encode()
        raw = {
            "handshake.bin": b"\r\nfreeguy#",
            "00.bin": b"U-Boot2019.04-g43964869 (Feb 25 2025)\r\nfreeguy#",
            "01.bin": b"1c040480: 21 \r\nfreeguy#",
            "02.bin": b"1c040400: 00 \r\nfreeguy#",
            "03.bin": b"makedata=true\r\nfactory_mode=0\r\ndevicestate=lock\r\nfreeguy#",
            "04.bin": b(self.target), "05.bin": b(self.target),
            "06.bin": b"\r\nfreeguy#", "07.bin": b(self.stock),
            "08.bin": b"done\r\nfreeguy#", "09.bin": b(self.stock)}
        self.undo_result = dict(environmentSaved=True, restorationAvailable=True,
                               alreadyStock=False, rebootIssued=False, firmwareWritten=False,
                               beforeSha256=m.sha(self.target.encode()),
                               restoredSha256=m.sha(self.stock.encode()))
        raw["result.json"] = json.dumps(self.undo_result).encode()
        self.undo = dict(version=1, **self.binding, files={
            name: self.write("undo-" + name, data) for name, data in raw.items()})
        self.preflight = dict(version=1, **self.binding,
                              staging=self.write_json("staging", self.f.staged),
                              verification=self.write_json("verification", self.f.checked),
                              bootObservation=self.write_json("observation", self.f.observed),
                              backup=self.write_json("backup", self.backup),
                              serialUndo=self.write_json("undo", self.undo))
        self.count = 0

    def write(self, name, raw):
        path = self.root / name
        path.write_bytes(raw)
        return dict(path=str(path), bytes=len(raw), sha256=m.sha(raw))

    def write_json(self, name, value):
        return self.write(name, json.dumps(value).encode())

    def output(self):
        self.count += 1
        out = self.root / ("out-" + str(self.count))
        out.mkdir()
        return out

    def prepare(self):
        return m.prepare(self.plan, self.preflight, self.root, self.allowed,
                         self.output(), m.Inputs())

    def test_complete_owner_evidence_remains_non_authorizing(self):
        evidence, report = self.prepare()
        self.assertEqual(m.console_tool.check_plan(evidence, self.allowed),
                         (self.stock, self.target))
        self.assertEqual(evidence["purpose"], "owner-capture")
        self.assertTrue(report["backup"]["integrityChecked"])
        self.assertTrue(report["serialUndo"]["exactInverseChecked"])
        self.assertFalse(report["operationalBootVerified"])
        self.assertFalse(report["physicalDeviceAssociationAttested"])
        self.assertFalse(evidence["activationAuthorized"])

    def test_backup_gap_overlap_truncation_duplicate_and_bad_hash_refused(self):
        changes = []
        for offset in (0, 513):
            value = copy.deepcopy(self.backup)
            value["chunks"][1]["offset"] = offset
            changes.append(value)
        value = copy.deepcopy(self.backup)
        value["chunks"].pop()
        changes.append(value)
        value = copy.deepcopy(self.backup)
        value["chunks"][1]["file"] = value["chunks"][0]["file"]
        changes.append(value)
        value = copy.deepcopy(self.backup)
        value["chunks"][0]["file"]["sha256"] = "0" * 64
        changes.append(value)
        for value in changes:
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.check_backup(value, self.root, m.Inputs(), **self.binding_args())

    def binding_args(self):
        return dict(expected=self.binding["deviceBindingSha256"],
                    scheme=self.binding["deviceBindingScheme"])

    def test_undo_requires_write_success_and_complete_exact_transcript(self):
        for name, data in (("08.bin", b"freeguy#"),
                           ("08.bin", b"write failed\r\ndone\r\nfreeguy#"),
                           ("06.bin", b"ERROR\r\nfreeguy#"),
                           ("09.bin", ("bootargs=" + self.target + "\r\nfreeguy#").encode()),
                           ("00.bin", b"U-Boot other\r\nfreeguy#"),
                           ("04.bin", ("bootargs=" + self.stock + "\r\nfreeguy#").encode()),
                           ("result.json", json.dumps(dict(self.undo_result, alreadyStock=True)).encode())):
            value = copy.deepcopy(self.undo)
            value["files"][name] = self.write("replacement", data)
            with self.subTest(name=name, data=data), self.assertRaises(ValueError):
                m.check_undo(value, self.root, m.Inputs(), **self.binding_args(),
                             allowed=self.allowed, stock=self.stock)
        value = copy.deepcopy(self.undo)
        del value["files"]["06.bin"]
        with self.assertRaises(ValueError):
            m.check_undo(value, self.root, m.Inputs(), **self.binding_args(),
                         allowed=self.allowed, stock=self.stock)

    def test_wrong_owner_stale_boot_and_failed_probe_refused(self):
        for name, original, update in (
            ("bootObservation", self.f.observed, {"bootId": "different"}),
            ("bootObservation", self.f.observed, {"deviceBindingSha256": "0" * 64}),
            ("verification", self.f.checked, {"complete": False}),
            ("serialUndo", self.undo, {"deviceBindingSha256": "0" * 64}),
            ("backup", self.backup, {"deviceBindingScheme": "unknown"})):
            before = self.preflight[name]
            self.preflight[name] = self.write_json("changed-" + name, dict(original, **update))
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.prepare()
            self.preflight[name] = before

    def test_mutated_input_after_validation_is_refused(self):
        files = m.Inputs()
        record = self.write("changing", b"original")
        files.file(record, self.root)
        (self.root / "changing").write_bytes(b"modified")
        with self.assertRaises(ValueError):
            files.unchanged()

    def test_filesystem_check_is_pinned_offline_readonly_and_failure_stops(self):
        record = self.write("filesystem.img", b"synthetic")
        files = m.Inputs()
        path = files.file(record, self.root)
        plan = dict(mode="first-install-plan", files=[
            dict(source=str(path), filesystem="ext4", target="fixture.img", sha256=record["sha256"])])
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            kwargs["stdout"].write(b"fixture check")
            self.assertEqual(kwargs["timeout"], 600)
            return SimpleNamespace(returncode=0)
        result = m.filesystem_checks(plan, Path("/docker"), "sha256:" + "a" * 64,
                                     self.output(), files, run)
        self.assertEqual(len(result), 1)
        for flag in ("--pull=never", "--network=none", "--read-only", "--cap-drop=ALL"):
            self.assertIn(flag, calls[0])
        self.assertEqual(calls[0][-3:], ["sha256:" + "a" * 64, "-fn", "/input.img"])
        self.assertTrue(calls[0][calls[0].index("--mount") + 1].endswith(",readonly"))
        with self.assertRaises(ValueError):
            m.filesystem_checks(plan, Path("/docker"), "mutable-tag", self.output(), files, run)
        with self.assertRaises(ValueError):
            m.filesystem_checks(plan, Path("/docker"), "sha256:" + "a" * 64,
                                self.output(), files, lambda *a, **kw: SimpleNamespace(returncode=4))
        self.assertEqual(len(calls), 1)

    def cli(self, action, output):
        preflight = self.write_json("preflight.json", self.preflight)
        return ["activate-first-entry.py", action, "--preflight", preflight["path"],
                "--preflight-sha256", preflight["sha256"], "--recovery-profile", "/private/fixture",
                "--known-device-binding", self.binding["deviceBindingSha256"],
                "--known-device-binding-scheme", self.binding["deviceBindingScheme"],
                "--stock-boot", str(self.f.f.stock), "--capture-build", str(self.f.f.capture),
                "--loader-build", str(self.f.f.loader), "--output", str(output)]

    def test_cli_inspect_and_arm_use_full_evidence_before_console(self):
        for action in ("inspect", "arm"):
            output = self.root / ("cli-" + action)
            args = self.cli(action, output)
            if action == "arm":
                args += ["--port", "/dev/fixture"]
            with patch.object(sys, "argv", args), \
                 patch.object(m.owner, "plan", return_value=self.plan), \
                 patch.object(m.recovery, "read_profile", return_value=self.allowed), \
                 patch.object(m.recovery, "Console") as port, \
                 patch.object(m.console_tool, "Transaction") as transaction:
                transaction.return_value.arm_after_preflight.return_value = dict(
                    phase="armed-not-booted", armAttempted=True, saveAcknowledged=True,
                    rebootIssued=False, persistentReadbackVerified=False, subsequentBootVerified=False)
                m.main()
                self.assertTrue((output / "preflight.json").is_file())
                self.assertTrue((output / "plan/journal.json").is_file())
                if action == "inspect":
                    port.assert_not_called()
                    transaction.assert_not_called()
                else:
                    transaction.return_value.inspect.assert_called_once()
                    transaction.return_value.arm_after_preflight.assert_called_once()
                    port.return_value.close.assert_called_once()

    def test_cli_bad_evidence_cannot_open_a_serial_port(self):
        self.preflight["backup"]["sha256"] = "0" * 64
        args = self.cli("arm", self.root / "bad-cli") + ["--port", "/dev/fixture"]
        with patch.object(sys, "argv", args), \
             patch.object(m.owner, "plan", return_value=self.plan), \
             patch.object(m.recovery, "read_profile", return_value=self.allowed), \
             patch.object(m.recovery, "Console") as port:
            with self.assertRaises(ValueError):
                m.main()
            port.assert_not_called()

    def test_cli_real_transaction_and_change_during_console_inspection(self):
        for change in (False, True):
            output = self.root / ("transaction-" + str(change))
            args = self.cli("arm", output) + ["--port", "/dev/fixture"]
            outer = self
            class Port:
                authorized_write = None
                def __init__(self, path, directory):
                    self.directory = directory
                    self.current, self.calls, self.closed = outer.stock, [], False
                def close(self):
                    self.closed = True
                def handshake(self):
                    if change:
                        (outer.root / "chunk0").write_bytes(b"changed" * 100)
                def command(self, command):
                    record = json.loads((self.directory / "journal.json").read_bytes())
                    outer.assertEqual(record["pending"], command)
                    self.calls.append(command)
                    if command in m.console_tool.IDENTITY_READS:
                        index = m.console_tool.IDENTITY_READS.index(command)
                        return Path(outer.undo["files"][f"{index:02}.bin"]["path"]).read_bytes()
                    if command == "printenv bootargs":
                        return ("bootargs=" + self.current + "\r\nfreeguy#").encode()
                    if command == self.authorized_write:
                        outer.assertEqual(command, m.recovery.encode_setenv(outer.target))
                        self.current = outer.target
                        return b"freeguy#"
                    if command == "saveenv":
                        return b"done\r\nfreeguy#"
                    raise AssertionError(command)
            ports = []
            def create(*args):
                port = Port(*args)
                ports.append(port)
                return port
            with patch.object(sys, "argv", args), \
                 patch.object(m.owner, "plan", return_value=self.plan), \
                 patch.object(m.recovery, "read_profile", return_value=self.allowed), \
                 patch.object(m.recovery, "Console", side_effect=create):
                if change:
                    with self.assertRaises(ValueError):
                        m.main()
                else:
                    m.main()
            self.assertTrue(ports[0].closed)
            writes = [c for c in ports[0].calls if c not in m.recovery.READS]
            self.assertEqual(writes, [] if change else
                             [m.recovery.encode_setenv(self.target), "saveenv"])


if __name__ == "__main__":
    unittest.main()
