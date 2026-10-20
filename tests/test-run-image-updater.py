#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Synthetic logs only: no ADB, shell launch, or television writes."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runner", ROOT / "tools/run-image-updater.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.r = dict(schema=1, nonce="1" * 32, plan="2" * 16,
                      dexSha256="3" * 64, serial="test-device",
                      remoteDex=runner.REMOTE + "/" + "3" * 64 + ".dex",
                      boot="11111111-2222-3333-4444-555555555555",
                      action="apply", stopAfter=None)

    def line(self, pid, message, level="I"):
        return f"{level}/CodexImageUpdate({pid:5d}): {message}"

    def logs(self):
        n, p = self.r["nonce"], self.r["plan"]
        return [
            self.line(101, f"RUN_START {n} 100"),
            self.line(102, f"BEFORE [ORIGINAL, ORIGINAL, ORIGINAL, ORIGINAL] plan={p}"),
            self.line(103, f"RUN_CHILD {n} 100 102"),
            self.line(102, f"VERIFIED [INSTALLED, INSTALLED, INSTALLED, INSTALLED] plan={p}"),
            self.line(102, "COMPLETE action=apply moves=8 bootWrites=false dataWrites=false"),
            self.line(104, f"RUN_DONE {n} 100 0")]

    def parse(self, lines):
        return runner.parse_result("\n".join(lines), self.r)

    def test_success_with_distinct_log_process_pids_and_child_early_output(self):
        result = self.parse(self.logs())
        self.assertTrue(result["terminal"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["childPid"], 102)

    def test_another_child_cannot_supply_missing_verification(self):
        lines = self.logs()
        lines[3] = lines[3].replace("(  102)", "(  999)")
        self.assertFalse(self.parse(lines)["passed"])

    def test_wrong_plan_or_partial_final_state_is_not_success(self):
        for value in (self.logs()[3].replace("2" * 16, "4" * 16),
                      self.logs()[3].replace("INSTALLED", "SAVED"),
                      self.logs()[3].replace(", INSTALLED", "", 1)):
            lines = self.logs()
            lines[3] = value
            self.assertFalse(self.parse(lines)["passed"])

    def test_completion_from_wrong_shell_or_old_nonce_does_not_end_run(self):
        for replacement in (" 999 0", " 100 0"):
            lines = self.logs()
            lines[-1] = self.line(104, "RUN_DONE " +
                ("4" * 32 if replacement == " 100 0" else self.r["nonce"]) + replacement)
            self.assertFalse(self.parse(lines)["terminal"])

    def test_missing_done_or_start_stays_unresolved(self):
        for lines in (self.logs()[:-1], self.logs()[1:], []):
            self.assertFalse(self.parse(lines)["terminal"])

    def test_duplicate_start_or_reversed_markers_stays_unresolved(self):
        lines = self.logs()
        self.assertFalse(self.parse([lines[0], *lines])["terminal"])
        lines[2], lines[-1] = lines[-1], lines[2]
        self.assertFalse(self.parse(lines)["terminal"])

    def test_stopped_or_nonzero_exit_refuses_success(self):
        for lines in (
            self.logs()[:-1] + [self.line(102, "STOPPED plan=" + self.r["plan"]), self.logs()[-1]],
            self.logs()[:-1] + [self.logs()[-1][:-1] + "9"]):
            self.assertTrue(self.parse(lines)["terminal"])
            self.assertFalse(self.parse(lines)["passed"])

    def test_requested_interruption_requires_matching_child_exception(self):
        self.r["stopAfter"] = 4
        lines = self.logs()[:3] + [
            self.line(102, "STOPPED plan=" + self.r["plan"], "E"),
            self.line(102, "java.io.InterruptedIOException: Requested stop after durable rename 4", "E"),
            self.logs()[-1]]
        self.assertTrue(self.parse(lines)["passed"])
        lines[4] = lines[4].replace("rename 4", "rename 1")
        self.assertFalse(self.parse(lines)["passed"])

    def test_shell_preflight_failure_is_terminal_failure(self):
        lines = self.logs()[:1] + [
            self.line(105, "RUN_FAILED " + self.r["nonce"] + " 100 dex", "E")]
        self.assertEqual(self.parse(lines), dict(terminal=True, passed=False, reason="shell-preflight-failed"))

    def test_rollback_requires_original_and_inspect_accepts_known_mixed_state(self):
        self.r["action"] = "rollback"
        lines = [l.replace("action=apply", "action=rollback") for l in self.logs()]
        self.assertFalse(self.parse(lines)["passed"])
        lines[3] = lines[3].replace("INSTALLED", "ORIGINAL")
        self.assertTrue(self.parse(lines)["passed"])
        self.r["action"] = "inspect"
        lines[3] = lines[3].replace("ORIGINAL", "SAVED", 1)
        lines[4] = lines[4].replace("action=rollback", "action=inspect")
        self.assertTrue(self.parse(lines)["passed"])

    def test_script_rejects_shell_injection_or_unpinned_path(self):
        for key, value in (("remoteDex", runner.REMOTE + "/other.dex"),
                           ("nonce", "$(id)"), ("boot", "x; reboot"),
                           ("serial", "-d"), ("action", "apply; reboot"),
                           ("stopAfter", True), ("stopAfter", 9)):
            r = deepcopy(self.r)
            r[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                runner.script(r)
        body = runner.script(self.r)
        self.assertEqual(body.count(" app_process "), 1)
        self.assertIn('wait "$child"', body)
        self.assertNotIn("reboot", body)
        self.assertNotIn("chmod", body)
        self.assertNotIn("setenv", body)

    def test_output_must_be_new_and_outside_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            alias = root / "alias"
            alias.symlink_to(ROOT)
            for path in (ROOT / "run", alias / "run", root):
                with self.assertRaises(ValueError):
                    runner.private_path(path, new=True)
            runner.private_path(root / "new", new=True)

    def test_launch_timeout_records_attempt_and_never_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "run"
            argv = ["run-image-updater.py", "start", "--serial", "test-device",
                    "--build", tmp, "--old-profile", tmp, "--new-profile", tmp,
                    "--action", "inspect", "--output", str(output)]
            responses = ["Result: Parcel(00000000 00000001)", subprocess.TimeoutExpired("adb", 20)]
            with patch("sys.argv", argv), patch.object(runner, "checked_build", return_value=(
                    self.r["plan"], self.r["dexSha256"])), patch.object(
                    runner, "identity", return_value=self.r["boot"]), patch.object(
                    runner, "shell", side_effect=responses) as adb:
                with self.assertRaises(subprocess.TimeoutExpired):
                    runner.main()
                self.assertEqual(adb.call_count, 2)
            saved = json.loads((output / "record.json").read_text())
            self.assertEqual(saved["dispatch"], "launching")
            self.assertNotIn("observation", saved)

    def test_observe_never_dispatches_and_checks_boot_after_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            changed = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            with patch.object(runner, "identity", side_effect=[self.r["boot"], changed]), \
                    patch.object(runner, "shell", return_value="\n".join(self.logs())) as adb:
                self.assertFalse(runner.observe(root, self.r, 1))
            self.assertEqual(adb.call_count, 1)
            self.assertEqual(adb.call_args.args[1], "logcat -d -v brief -s CodexImageUpdate")
            self.assertFalse(json.loads((root / "record.json").read_text())[
                "observation"]["sameStockBoot"])

    def test_local_plan_and_dex_must_both_match(self):
        spec = importlib.util.spec_from_file_location("builder", ROOT / "tools/build-image-updater.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        spec = importlib.util.spec_from_file_location("switch", ROOT / "tools/build-switch-app.py")
        switch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(switch)
        old = dict(model="L75MA-SPL", device="freeguy", bootloader="01.01.250225",
                   immutableFiles={p: "1"*64 for p in switch.EXPECTED},
                   normalizedBootargsSha256=["2"*64], serialUndoVerified=True)
        new = deepcopy(old)
        for path in builder.CHANGED:
            new["immutableFiles"][path] = "3"*64
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dex").mkdir()
            (root / "dex/classes.dex").write_bytes(b"synthetic-dex-fixture")
            digest = hashlib.sha256(b"synthetic-dex-fixture").hexdigest()
            old_path, new_path = root / "old.json", root / "new.json"
            old_path.write_text(json.dumps(old))
            new_path.write_text(json.dumps(new))
            plan = builder.plan(old, new, False)
            plan["dexSha256"] = digest
            (root / "plan.json").write_text(json.dumps(plan))
            self.assertEqual(runner.checked_build(root, old_path, new_path, False),
                             (plan["id"], digest))
            plan["rows"][0]["active"] = "/dev/block/by-name/boot_b"
            (root / "plan.json").write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError, "Plan differs"):
                runner.checked_build(root, old_path, new_path, False)
            plan = builder.plan(old, new, False)
            plan["dexSha256"] = "0"*64
            (root / "plan.json").write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                runner.checked_build(root, old_path, new_path, False)


if __name__ == "__main__":
    unittest.main()
