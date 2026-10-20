#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Owner-capture evidence must not activate a different or stale staged loader."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load("owner_activation", ROOT / "tools/plan-owner-activation.py")
fixtures = load("owner_stage_fixtures", ROOT / "tests/test-stage-owner-capture.py")
console = load("activation_console", ROOT / "tools/first-activation-console.py")
STOCK = ("console=ttyS0 root=/dev/ram init=/init rw loglevel=0 "
         "androidboot.bootreason=cold lastblval=75 example=fixture")
ALLOWED = [m.activation.recovery.fingerprint(STOCK)]
BOOT = "12345678-1234-1234-1234-123456789abc"


class Tests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.Tests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.plan = self.f.plan()
        self.device = fixtures.fixtures.FakeDevice()
        self.device.boot = BOOT
        self.staged = self.f.run_stage(self.device, "staged")
        out = self.f.root / "probe"
        out.mkdir()

        def probe(device, loader, boot, event):
            event(dict(finished=True, remotePid=200))
            return 90

        self.checked = m.owner.verify.verify(self.device, self.plan, self.staged, out,
                                             execute=True, runner=probe)
        self.observed = dict(stockBootargs=STOCK, observedBootargs=STOCK,
                             kernelCmdline=STOCK + " androidboot.example=fixture",
                             deviceBindingSha256=self.plan["deviceBinding"],
                             deviceBindingScheme=self.plan["deviceBindingScheme"], bootId=BOOT)

    def assemble(self, plan=None, staged=None, checked=None, observed=None):
        return m.assemble(plan or self.plan, staged or self.staged, checked or self.checked,
                          observed or self.observed, hashlib.sha256(b"synthetic evidence").hexdigest(),
                          ALLOWED)

    def test_shared_console_accepts_exact_owner_entry_without_authorizing_it(self):
        r = self.assemble()
        stock, target = console.check_plan(r, ALLOWED)
        self.assertEqual(stock, STOCK)
        self.assertEqual(m.activation.recovery.restore_plan(target, ALLOWED), STOCK)
        self.assertEqual(r["purpose"], "owner-capture")
        self.assertEqual(r["ownerCapture"], self.plan["ownerCapture"])
        self.assertFalse(r["activationAuthorized"])
        self.assertFalse(r["deviceAccess"])
        self.assertFalse(r["deviceWritesIssued"])

    def test_other_owner_boot_or_missing_observation_identity_refused(self):
        for key, value in (("deviceBindingSha256", "0" * 64),
                           ("deviceBindingScheme", "other"), ("bootId", "other"),
                           ("bootId", None)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.assemble(observed=dict(self.observed, **{key: value}))

    def test_changed_loader_or_staging_and_incomplete_probe_refused(self):
        plan = copy.deepcopy(self.plan)
        plan["files"][0]["sha256"] = "0" * 64
        staged = copy.deepcopy(self.staged)
        staged["files"][0]["metadata"]["ino"] += 1
        for change in (dict(plan=plan), dict(staged=staged),
                       dict(plan=dict(self.plan, mode="first-install-plan")),
                       dict(checked=dict(self.checked, complete=False)),
                       dict(checked=dict(self.checked, probeExit=0)),
                       dict(checked=dict(self.checked, startupChanged=True))):
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                self.assemble(**change)

    def test_existing_temporary_entry_is_not_a_fresh_capture(self):
        trial = self.assemble()["temporaryBootargs"]
        observed = dict(self.observed, observedBootargs=trial,
                        kernelCmdline=trial + " androidboot.example=fixture")
        with self.assertRaisesRegex(ValueError, "original stock"):
            self.assemble(observed=observed)


if __name__ == "__main__":
    unittest.main()
