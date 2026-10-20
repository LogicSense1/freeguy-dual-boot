#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Staging-evidence checks and single-dispatch non-PID-1 probe tests."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = load("verifier", ROOT / "tools/verify-first-install.py")
fixtures = load("staging_fixtures", ROOT / "tests/test-first-install-stage.py")
BOOT = "12345678-1234-1234-1234-123456789abc"
sha = lambda data: hashlib.sha256(data).hexdigest()


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        inputs = fixtures.fixture.fixture(self.root)
        (self.root / "inputs.json").write_text(json.dumps(inputs))
        path = self.root / "build/audit.json"
        audit = json.loads(path.read_text())
        case = next(case for case in audit["cases"] if case["case"] == "not-pid1")
        case.update(end="unprivileged-exit", syscalls=2, environmentWrites=0)
        path.write_text(json.dumps(audit))
        self.plan = verify.inventory.plan(inputs, self.root, fixtures.fixture.BINDING,
                                          fixtures.fixture.SCHEME)
        self.device = fixtures.FakeDevice()
        self.device.boot = BOOT
        folder = self.root / "staging"
        folder.mkdir()
        self.staged = verify.stager.stage(self.device, self.plan, folder, apply=True)

    def check(self, name, execute=False, runner=None):
        out = self.root / name
        out.mkdir()
        args = {} if runner is None else {"runner": runner}
        return verify.verify(self.device, self.plan, self.staged, out, execute=execute, **args)

    def test_inspection_rechecks_all_files_without_executing_or_writing(self):
        before, writes = copy.deepcopy(self.device.nodes), self.device.writes
        runner = Mock(side_effect=AssertionError("must not execute"))
        result = self.check("inspect", runner=runner)
        self.assertTrue(result["complete"])
        self.assertEqual(result["filesVerified"], 22)
        self.assertFalse(result["probeStarted"])
        self.assertFalse(result["bootActivationAuthorized"])
        self.assertEqual(result["stagingContentSha256"], verify.content_hash(self.staged))
        runner.assert_not_called()
        self.assertEqual(self.device.writes, writes)
        self.assertEqual(self.device.nodes, before)

    def test_probe_is_once_and_keeps_non_pid1_separate_from_boot_acceptance(self):
        before = copy.deepcopy(self.device.nodes)
        calls = []
        def runner(device, loader, boot, event):
            calls.append((loader["target"], boot))
            event(dict(localAdbPid=100, remotePid=200, finished=True))
            return 90
        result = self.check("probe", execute=True, runner=runner)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["probeExit"], 90)
        self.assertTrue(result["complete"])
        self.assertFalse(result["runtimeVerified"])
        self.assertFalse(result["bootActivationAuthorized"])
        self.assertEqual(self.device.nodes, before)

    def test_incomplete_altered_or_old_journal_refused(self):
        for name, value in (("complete", False), ("applied", False), ("pending", {}),
                            ("parents", {}), ("directories", {}), ("files", []), ("plan", {})):
            changed = dict(self.staged, **{name: value})
            with self.subTest(name=name), self.assertRaises(ValueError):
                verify.check_journal(changed, self.plan)
        changed = dict(self.staged, failure="TimeoutExpired")
        with self.assertRaises(ValueError):
            verify.check_journal(changed, self.plan)

    def test_mid_check_reboot_file_mutation_and_parent_replacement_refuse_probe(self):
        original_nodes = copy.deepcopy(self.device.nodes)
        for mode in ("boot", "file", "parent"):
            self.device.nodes = copy.deepcopy(original_nodes)
            self.device.boot = BOOT
            self.device.reboot_at = None
            if mode == "boot": self.device.reboot_at = self.device.guard_calls + 2
            elif mode == "file":
                row = self.device.nodes[self.plan["files"][0]["target"]]
                row["data"] = b"x" * row["bytes"]
            else: self.device.nodes[verify.inventory.LOADER]["ino"] += 1
            runner = Mock(side_effect=AssertionError("must not execute"))
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.check(mode, execute=True, runner=runner)
            runner.assert_not_called()

    def test_later_stock_boot_requires_full_recheck_and_gets_fresh_identity(self):
        self.device.boot = "abcdef12-1234-1234-1234-123456789abc"
        result = self.check("later-boot")
        self.assertTrue(result["complete"])
        self.assertEqual(result["filesVerified"], 22)
        self.assertEqual(result["identity"]["bootId"], self.device.boot)
        self.assertEqual(result["stagingBootId"], BOOT)
    def test_audit_must_prove_exact_getpid_exit_only_path(self):
        path = self.root / "build/audit.json"
        original = json.loads(path.read_text())
        for key, value in (("syscalls", 3), ("environmentWrites", 1), ("end", "other")):
            audit = copy.deepcopy(original)
            next(case for case in audit["cases"] if case["case"] == "not-pid1")[key] = value
            path.write_text(json.dumps(audit))
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify.probe_audit(self.plan)

    def test_timeout_wrong_result_and_loader_change_never_retry(self):
        before = copy.deepcopy(self.device.nodes)
        for mode in ("timeout", "wrong-exit", "loader-change", "unknown-completion"):
            self.device.nodes = copy.deepcopy(before)
            calls = []
            def runner(device, loader, boot, event):
                calls.append(1)
                event(dict(localAdbPid=100, remotePid=200,
                           finished=mode not in ("timeout", "unknown-completion")))
                if mode == "timeout":
                    raise subprocess.TimeoutExpired("fixture", 10)
                if mode == "loader-change":
                    device.nodes[loader["target"]]["ino"] += 1
                return 0 if mode == "wrong-exit" else 90
            with self.subTest(mode=mode), self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                self.check(mode, execute=True, runner=runner)
            self.assertEqual(len(calls), 1)
            report = json.loads((self.root / mode / "journal.json").read_text())
            self.assertFalse(report["complete"])
            self.assertEqual(report["pending"], "non-pid1-probe")

    def test_host_shell_probe_accepts_only_90_and_no_extra_output(self):
        # The shim substitutes a temporary script and fixture UID/boot values.
        # It does not call adb, create /data or execute any ARM/OEM binary.
        target = self.root / "loader-fixture"
        shim = self.root / "adb-fixture"
        fixed = verify.inventory.LOADER + "/envdaily"
        shim.write_text("#!/usr/bin/env python3\nimport os,sys\n"
                        "assert sys.argv[1:5]==['-s','fixture','shell','-T']\n"
                        "cmd=sys.argv[5].replace('$(id -u)','2000').replace("
                        "'$(cat /proc/sys/kernel/random/boot_id)'," + repr(BOOT) + ")\n"
                        "cmd=cmd.replace(" + repr(fixed) + "," + repr(str(target)) + ")\n"
                        "cmd=cmd.replace('sha256sum','/usr/bin/shasum -a 256')\n"
                        "os.execv('/bin/sh',['sh','-c',cmd])\n")
        shim.chmod(0o700)
        device = verify.stager.Device(shim, "fixture", self.plan)
        for name, body in (("good", "exit 90\n"), ("wrong", "exit 0\n"),
                           ("extra", "printf 'unexpected\\n'; exit 90\n")):
            target.write_text("#!/bin/sh\n" + body)
            target.chmod(0o700)
            loader = dict(target=fixed, sha256=sha(target.read_bytes()))
            events = []
            if name == "good":
                self.assertEqual(verify.run_probe(device, loader, BOOT, events.append), 90)
                self.assertTrue(events[-1]["finished"])
            else:
                with self.assertRaises(ValueError):
                    verify.run_probe(device, loader, BOOT, events.append)

    def test_cli_inspection_pins_journal_before_device_access(self):
        staged = self.root / "staging/journal.json"
        digest = sha(staged.read_bytes())
        def args(output, pin):
            return ["verify-first-install.py", "inspect", "--adb", "/unused/adb",
                    "--device", "fixture", "--inputs", str(self.root / "inputs.json"),
                    "--known-device-binding", fixtures.fixture.BINDING,
                    "--known-device-binding-scheme", fixtures.fixture.SCHEME,
                    "--staging-record", str(staged), "--staging-record-sha256", pin,
                    "--output", str(output)]
        with patch("sys.argv", args(self.root / "bad", "0" * 64)), \
             patch.object(verify.stager, "Device") as factory, self.assertRaises(ValueError):
            verify.main()
        factory.assert_not_called()
        self.assertFalse((self.root / "bad").exists())
        with patch("sys.argv", args(self.root / "cli", digest)), \
             patch.object(verify.stager, "Device", return_value=self.device), \
             patch("builtins.print"):
            verify.main()
        result = json.loads((self.root / "cli/journal.json").read_text())
        self.assertTrue(result["complete"])
        self.assertFalse(result["probeStarted"])
        self.assertFalse(result["bootActivationAuthorized"])


if __name__ == "__main__":
    unittest.main()
