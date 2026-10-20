#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""First-install startup settings preserve other services and refuse active defaults."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("provision", ROOT / "tools/provision-switch-startup.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
OTHER = "example.reader/.ReadService:local.codex.tvadb/.ConnectionService"


class Device:
    apk_hash = "a" * 64

    def __init__(self):
        self.state = dict(services=OTHER, accessibility="1", idleWhitelisted=False,
                          RUN_IN_BACKGROUND="default", RUN_ANY_IN_BACKGROUND="ignore")
        self.calls = 0
        self.fail_at = self.reboot_at = self.shared_at = None
        self.shared = False
        self.boot = "one"
        self.actions = []

    def tick(self):
        self.calls += 1
        if self.calls == self.fail_at:
            raise OSError("interrupted")
        if self.calls == self.reboot_at:
            self.boot = "two"
        if self.calls == self.shared_at:
            self.shared = True

    def guard(self):
        self.tick()
        m.need(not self.shared, "Existing defaults")
        return dict(bootId=self.boot, deviceBinding="b" * 64)

    def snapshot(self):
        self.tick()
        return self.state.copy()

    def open_app(self):
        self.tick()
        self.actions.append("open")

    def apply(self, key, value):
        self.tick()
        assert not self.shared
        assert self.actions[0] == "open"
        self.actions.append(key)
        self.state[key] = value


class Tests(unittest.TestCase):
    def test_preserve_existing_components_and_aliases(self):
        self.assertEqual(m.service_target(OTHER), OTHER + ":" + m.COMPONENT)
        for component in (m.COMPONENT, m.FULL_COMPONENT):
            value = OTHER + ":" + component
            self.assertEqual(m.service_target(value), value)
        for bad in (OTHER + ":", "x", "a/.b:a/.b", m.COMPONENT + ":" + m.FULL_COMPONENT,
                    OTHER + "\n", "a/.b;reboot"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                m.service_target(bad)

    def test_inspection_never_opens_or_mutates(self):
        with tempfile.TemporaryDirectory() as directory:
            d = Device()
            r = m.provision(d, Path(directory) / "out")
            self.assertTrue(r["inspectionComplete"])
            self.assertFalse(r["complete"])
            self.assertEqual(d.actions, [])

    def test_configure_preserves_adb_helper_and_does_not_claim_runtime_success(self):
        with tempfile.TemporaryDirectory() as directory:
            d = Device()
            r = m.provision(d, Path(directory) / "out", True)
            self.assertTrue(r["complete"])
            self.assertEqual(d.state["services"], OTHER + ":" + m.COMPONENT)
            self.assertEqual(d.actions[0], "open")
            for key in ("runtimeSwitchVerified", "startupBindingVerified",
                        "environmentWritesIssued", "firmwareWritesIssued", "rebootIssued"):
                self.assertFalse(r[key])

    def test_every_interruption_stays_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = Device()
            m.provision(reference, root / "reference", True)
            for point in range(1, reference.calls + 1):
                d = Device()
                d.fail_at = point
                out = root / str(point)
                with self.subTest(point=point), self.assertRaises(OSError):
                    m.provision(d, out, True)
                if (out / "journal.json").exists():
                    r = json.loads((out / "journal.json").read_text())
                    self.assertFalse(r["complete"])
                self.assertTrue(d.state["services"].startswith(OTHER))

    def test_reboot_or_defaults_appearing_stops_before_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            for attribute in ("reboot_at", "shared_at"):
                d = Device()
                setattr(d, attribute, 3)
                with self.subTest(attribute=attribute), self.assertRaises(ValueError):
                    m.provision(d, Path(directory) / attribute, True)
                self.assertEqual(d.actions, [])
            d = Device()
            d.shared = True
            with self.assertRaises(ValueError):
                m.provision(d, Path(directory) / "existing", True)
            self.assertEqual(d.actions, [])

    def test_concurrent_accessibility_edit_is_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            d = Device()
            original = d.open_app
            def changed():
                original()
                d.state["services"] = OTHER + ":another.app/.Reader"
            d.open_app = changed
            with self.assertRaises(ValueError):
                m.provision(d, Path(directory) / "out", True)
            self.assertEqual(d.state["services"], OTHER + ":another.app/.Reader")
            self.assertEqual(d.actions, ["open"])

    def test_device_guard_checks_absence_and_exact_installed_build(self):
        d = m.Device(Path("/synthetic/adb"), "fixture", "b" * 64,
                     "freeguy-android-serial-v1", "a" * 64, 5)
        replies = {
            "am get-current-user": "0",
            "pm path " + m.PACKAGE: "package:/data/app/fixture/base.apk",
            "sha256sum /data/app/fixture/base.apk": "a" * 64 + "  /data/app/fixture/base.apk",
            "dumpsys package " + m.PACKAGE: "versionCode=5 minSdk=28\n" + "\n".join(
                m.PACKAGE + "/." + name for name in
                ("SwitchActivity", "DefaultStartupService")),
        }
        d.shell = lambda command: replies[command]
        d.metadata = lambda path: None
        with patch.object(m.stage.Device, "guard", return_value={"bootId": "one"}):
            self.assertEqual(d.guard(), {"bootId": "one"})
            d.metadata = lambda path: {"mode": 0o40700}
            with self.assertRaisesRegex(ValueError, "Existing defaults"):
                d.guard()
            d.metadata = lambda path: None
            replies["sha256sum /data/app/fixture/base.apk"] = "c" * 64 + "  /data/app/fixture/base.apk"
            with self.assertRaisesRegex(ValueError, "Installed APK"):
                d.guard()


if __name__ == "__main__":
    unittest.main()
