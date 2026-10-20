#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reversible entry, complete command-line limits and evidence-chain refusals."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


activation = load("activation", ROOT / "tools/plan-first-activation.py")
fixtures = load("verification_fixtures", ROOT / "tests/test-first-install-verify.py")
STOCK = ("console=ttyS0 root=/dev/ram init=/init rw loglevel=0 "
         "androidboot.bootreason=cold lastblval=75 example=abc")
ALLOWED = [activation.recovery.fingerprint(STOCK)]
TAIL = " androidboot.example=fixture"


class ActivationPlanTests(unittest.TestCase):
    def test_stock_and_trial_observations_make_same_exact_entry(self):
        first = activation.entry(STOCK, ALLOWED, STOCK, STOCK + TAIL)
        trial = first["temporaryBootargs"]
        second = activation.entry(STOCK, ALLOWED, trial, trial + TAIL)
        for key in ("temporaryBootargs", "undoBootargs", "measuredTargetBytes",
                    "conservativeTargetBytes", "appendedBytes"):
            self.assertEqual(first[key], second[key])
        self.assertEqual(activation.recovery.restore_plan(trial, ALLOWED), STOCK)
        self.assertEqual(first["conservativeTargetBytes"], len(trial) + 626)

    def test_dynamic_fields_preserved_without_permitting_other_changes(self):
        changed = STOCK.replace("cold", "reboot").replace("lastblval=75", "lastblval=5")
        result = activation.entry(changed, ALLOWED, STOCK, STOCK + TAIL)
        self.assertEqual(result["undoBootargs"], changed)
        self.assertIn("lastblval=5", result["temporaryBootargs"])
        with self.assertRaises(ValueError):
            activation.entry(STOCK.replace("example=abc", "example=xyz"), ALLOWED, STOCK, STOCK + TAIL)

    def test_complete_kernel_line_and_no_late_overrides(self):
        for kernel in ("", STOCK, STOCK[:-1] + TAIL, STOCK + TAIL + "\n",
                       STOCK + "  other=1", STOCK + " other=非ASCII",
                       STOCK + " root=/dev/ram", STOCK + " init=/init",
                       STOCK + " rw", STOCK + " rootflags=rw", STOCK + " loglevel=0"):
            with self.subTest(kernel=kernel), self.assertRaises(ValueError):
                activation.entry(STOCK, ALLOWED, STOCK, kernel)
        with self.assertRaises(ValueError):
            activation.entry(STOCK, ALLOWED, STOCK + " changed=1", STOCK + " changed=1" + TAIL)

    def test_bootloader_token_reordering_preserves_complete_matching(self):
        kernel = "cgroup_disable=pressure " + " ".join(reversed(STOCK.split(" "))) + TAIL
        result = activation.entry(STOCK, ALLOWED, STOCK, kernel)
        self.assertEqual(result["appendedBytes"], len(" cgroup_disable=pressure" + TAIL))
        for invalid in (kernel.replace("example=abc", "example=changed"),
                        kernel + " example=abc", kernel + " console=other",
                        kernel + " rw"):
            with self.assertRaises(ValueError):
                activation.entry(STOCK, ALLOWED, STOCK, invalid)

    def test_exact_length_boundary_and_measured_tail_can_raise_floor(self):
        base = activation.entry(STOCK, ALLOWED, STOCK, STOCK + TAIL)
        room = 2047 - 64 - base["conservativeTargetBytes"]
        stock = STOCK + "x" * room
        allowed = [activation.recovery.fingerprint(stock)]
        result = activation.entry(stock, allowed, stock, stock + TAIL)
        self.assertEqual(result["conservativeTargetBytes"] + 64, 2047)
        for extra, tail in (("x", TAIL), ("", " extra=" + "z" * 627)):
            candidate = stock + extra
            with self.assertRaises(ValueError):
                activation.entry(candidate, [activation.recovery.fingerprint(candidate)],
                                 candidate, candidate + tail)

    def test_escaped_console_line_must_fit_even_when_kernel_line_fits(self):
        stock = STOCK + " test=" + "$" * 1000
        with self.assertRaisesRegex(ValueError, "Console command"):
            activation.entry(stock, [activation.recovery.fingerprint(stock)], stock, stock + TAIL)

    def prepare(self):
        fixture = fixtures.VerifyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        def runner(device, loader, boot, event):
            event(dict(localAdbPid=100, remotePid=200, finished=True))
            return 90
        record = fixture.check("probe", execute=True, runner=runner)
        return fixture, record

    def test_completed_probe_links_exact_staging_and_inventory(self):
        fixture, record = self.prepare()
        result = activation.evidence(fixture.plan, fixture.staged, record)
        self.assertEqual(result["verificationBootId"], fixtures.BOOT)
        self.assertEqual(result["verificationContentSha256"], activation.verify.content_hash(record))

    def test_inspection_timeout_or_different_evidence_cannot_pass(self):
        fixture, record = self.prepare()
        variants = [
            {"complete": False}, {"pending": "non-pid1-probe"}, {"failure": "TimeoutExpired"},
            {"probeRequested": False}, {"probeStarted": False}, {"probeExit": 0},
            {"probeExit": True}, {"process": {"finished": False, "remotePid": 200}},
            {"process": {"finished": True, "remotePid": 1}}, {"filesVerified": 21},
            {"stagingContentSha256": "0" * 64}, {"inventorySha256": "0" * 64},
            {"setupLoaderSha256": "0" * 64}, {"startupChanged": True},
            {"stagingBootId": "different"}, {"identity": {"bootId": fixtures.BOOT}},
            {"identity": dict(record["identity"], bootId="not-a-boot")},
            {"bootActivationAuthorized": True}, {"runtimeVerified": True},
        ]
        for change in variants:
            with self.subTest(change=change), self.assertRaises(ValueError):
                activation.evidence(fixture.plan, fixture.staged, dict(record, **change))
        staged = copy.deepcopy(fixture.staged)
        staged["files"][0]["metadata"]["ino"] += 1
        with self.assertRaises(ValueError):
            activation.evidence(fixture.plan, staged, record)

    def test_reviewed_record_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text('{"value":1}')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(activation.pinned(path, digest), {"value": 1})
            path.write_text('{"value":2}')
            with self.assertRaises(ValueError):
                activation.pinned(path, digest)
            with self.assertRaises(ValueError):
                activation.pinned(path, "invalid")

    def test_cli_joins_private_evidence_without_device_access(self):
        fixture, record = self.prepare()
        root = fixture.root
        profile = root / "recovery-profile.json"
        profile.write_text(json.dumps(dict(device="freeguy", model="L75MA-SPL",
                                          bootloader="01.01.250225",
                                          normalizedBootargsSha256=ALLOWED)))
        observed = dict(stockBootargs=STOCK, observedBootargs=STOCK, kernelCmdline=STOCK + TAIL,
                        deviceBindingSha256=fixture.plan["deviceBinding"],
                        deviceBindingScheme=fixture.plan["deviceBindingScheme"])
        output = root / "activation-plan"
        command = [sys.executable, "-B", str(ROOT / "tools/plan-first-activation.py"),
                   "--inputs", str(root / "inputs.json"),
                   "--known-device-binding", fixture.plan["deviceBinding"],
                   "--known-device-binding-scheme", fixture.plan["deviceBindingScheme"],
                   "--recovery-profile", str(profile), "--output", str(output)]
        for name, value in (("staging-record", fixture.staged), ("verification-record", record),
                            ("boot-observation", observed)):
            path = root / (name + ".json")
            path.write_text(json.dumps(value))
            command.extend(["--" + name, str(path), "--" + name + "-sha256",
                            hashlib.sha256(path.read_bytes()).hexdigest()])
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertFalse(summary["activationAuthorized"])
        self.assertFalse(summary["deviceWritesIssued"])
        saved = json.loads((output / "journal.json").read_text())
        self.assertEqual(saved["undoBootargs"], STOCK)
        self.assertFalse(saved["deviceAccess"])
        self.assertEqual((output / "journal.json").stat().st_mode & 0o777, 0o600)
        repeated = subprocess.run(command, capture_output=True, timeout=30)
        self.assertNotEqual(repeated.returncode, 0)


if __name__ == "__main__":
    unittest.main()
