#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""RAM-only temporary entry, stock-only save, interruption and CLI checks."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load("exercise", ROOT / "tools/exercise-serial-undo.py")
fixture = load("fixture", ROOT / "tests/test-activate-first-entry.py")


class Tests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.Tests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.plan, _ = self.f.prepare()
        self.allowed = self.f.allowed
        self.root = self.f.root
        self.attempts = 0
        self.calls, self.saved, self.ports = [], [], []
        self.current = self.f.stock
        self.fail = None

    def factory(self, directory):
        outer = self
        class Port:
            authorized_write = None
            def __init__(self):
                self.index, self.closed = 0, False
            def handshake(self):
                (directory / "handshake.bin").write_bytes(b"\r\nfreeguy#")
            def command(self, command):
                outer.attempts += 1
                if outer.fail == (outer.attempts, "before"):
                    raise TimeoutError("synthetic before command")
                outer.calls.append(command)
                if command in m.activation.console_tool.IDENTITY_READS:
                    index = m.activation.console_tool.IDENTITY_READS.index(command)
                    raw = Path(outer.f.undo["files"][f"{index:02}.bin"]["path"]).read_bytes()
                elif command == "printenv bootargs":
                    raw = ("bootargs=" + outer.current + "\r\nfreeguy#").encode()
                elif command == self.authorized_write:
                    stock = m.recovery.encode_setenv(outer.f.stock)
                    target = m.recovery.encode_setenv(outer.f.target)
                    outer.assertIn(command, (stock, target))
                    outer.current = outer.f.stock if command == stock else outer.f.target
                    raw = b"\r\nfreeguy#"
                elif command == "saveenv":
                    outer.assertEqual(outer.current, outer.f.stock, "Never save the temporary entry")
                    outer.saved.append(outer.current)
                    raw = b"done\r\nfreeguy#"
                else:
                    raise AssertionError(command)
                (directory / f"{self.index:02}.bin").write_bytes(raw)
                self.index += 1
                if outer.fail == (outer.attempts, "after"):
                    raise TimeoutError("synthetic lost response")
                return raw
            def close(self):
                self.closed = True
        port = Port()
        self.ports.append(port)
        return port

    def run_exercise(self, name):
        out = self.root / name
        out.mkdir()
        return m.exercise(self.plan, self.allowed, out, m.activation.Inputs(), self.factory)

    def test_exact_undo_and_manifest_without_temporary_save_or_reset(self):
        result = self.run_exercise("complete")
        self.assertTrue(result["complete"])
        self.assertFalse(result["temporaryArgumentsSaved"])
        self.assertFalse(result["rebootIssued"])
        self.assertFalse(result["subsequentBootVerified"])
        self.assertEqual(self.saved, [self.f.stock])
        self.assertTrue(all(port.closed for port in self.ports))
        manifest = json.loads((self.root / "complete/serial-undo.json").read_bytes())
        check = m.activation.check_undo(manifest, self.root / "complete", m.activation.Inputs(),
            self.plan["deviceBindingSha256"], self.plan["deviceBindingScheme"],
            self.allowed, self.f.stock)
        self.assertTrue(check["exactInverseChecked"])
        self.assertEqual([c for c in self.calls if c not in m.recovery.READS],
                         [m.recovery.encode_setenv(self.f.target),
                          m.recovery.encode_setenv(self.f.stock), "saveenv"])

    def test_each_command_interruption_stops_with_no_automatic_retry_or_reset(self):
        self.run_exercise("count")
        count = self.attempts
        for position in range(1, count + 1):
            for side in ("before", "after"):
                with self.subTest(position=position, side=side):
                    self.attempts = 0
                    self.current = self.f.stock
                    self.calls, self.saved, self.ports = [], [], []
                    self.fail = position, side
                    name = f"command-{position}-{side}"
                    with self.assertRaises(TimeoutError):
                        self.run_exercise(name)
                    self.assertEqual(self.attempts, position)
                    self.assertTrue(all(p.closed for p in self.ports))
                    record = json.loads((self.root / name / "journal.json").read_bytes())
                    self.assertFalse(record["complete"])
                    self.assertFalse(record["rebootIssued"])
                    self.assertTrue(all(value == self.f.stock for value in self.saved))
                    self.assertLessEqual(self.calls.count("saveenv"), 1)
                    self.assertFalse((self.root / name / "serial-undo.json").exists())

    def test_checkpoint_failures_stop_and_do_not_authorize_more_commands(self):
        original = m.save
        writes = []
        def counter(path, value):
            writes.append(str(path))
            original(path, value)
        with patch.object(m, "save", side_effect=counter):
            self.run_exercise("journal-count")
        for position in range(1, len(writes) + 1):
            for side in ("before", "after"):
                self.current, self.attempts = self.f.stock, 0
                self.calls, self.saved, self.ports = [], [], []
                state = dict(index=0, commands=None)
                def failing(path, value):
                    state["index"] += 1
                    if state["index"] == position and side == "before":
                        state["commands"] = len(self.calls)
                        raise OSError("synthetic checkpoint failure")
                    original(path, value)
                    if state["index"] == position and side == "after":
                        state["commands"] = len(self.calls)
                        raise OSError("synthetic lost checkpoint acknowledgement")
                with self.subTest(position=position, side=side), \
                     patch.object(m, "save", side_effect=failing), self.assertRaises(OSError):
                    self.run_exercise(f"journal-{position}-{side}")
                self.assertEqual(len(self.calls), state["commands"])
                self.assertTrue(all(p.closed for p in self.ports))
                self.assertTrue(all(value == self.f.stock for value in self.saved))

    def test_activation_still_requires_undo_record(self):
        preflight = copy.deepcopy(self.f.preflight)
        del preflight["serialUndo"]
        with self.assertRaises(ValueError):
            m.activation.prepare(self.f.plan, preflight, self.root, self.allowed,
                                 self.f.output(), m.activation.Inputs())

    def test_cli_inspection_and_exercise_with_real_evidence_and_transaction(self):
        preflight = copy.deepcopy(self.f.preflight)
        del preflight["serialUndo"]
        record = self.f.write_json("undo-preflight.json", preflight)
        for action in ("inspect", "exercise"):
            args = ["exercise-serial-undo.py", action,
                "--preflight", record["path"], "--preflight-sha256", record["sha256"],
                "--recovery-profile", "/private/fixture",
                "--known-device-binding", self.f.binding["deviceBindingSha256"],
                "--known-device-binding-scheme", self.f.binding["deviceBindingScheme"],
                "--stock-boot", str(self.f.f.f.stock), "--capture-build", str(self.f.f.f.capture),
                "--loader-build", str(self.f.f.f.loader), "--output", str(self.root / ("cli-" + action))]
            if action == "exercise":
                args += ["--port", "/dev/fixture"]
            with patch.object(sys, "argv", args), \
                 patch.object(m.activation, "PREFIX_BYTES", 1024), \
                 patch.object(m.activation.owner, "plan", return_value=self.f.plan), \
                 patch.object(m.recovery, "read_profile", return_value=self.allowed), \
                 patch.object(m.recovery, "Console", side_effect=lambda port, folder: self.factory(folder)) as port:
                m.main()
                if action == "inspect":
                    port.assert_not_called()
                else:
                    self.assertTrue((self.root / "cli-exercise/serial-undo.json").is_file())
                    self.assertEqual(self.saved, [self.f.stock])


if __name__ == "__main__":
    unittest.main()
