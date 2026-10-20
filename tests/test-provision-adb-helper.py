#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("provision", ROOT / "tools/provision-adb-helper.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
OTHER = "example.reader/.ReadService:example.voice/example.voice.VoiceService"


class Device:
    apk_hash = "b" * 64

    def __init__(self):
        self.state = dict(services=OTHER, accessibility="1", permission=False,
                          idleWhitelisted=False, RUN_IN_BACKGROUND="ignore",
                          RUN_ANY_IN_BACKGROUND="default")
        self.boot = "same-boot"
        self.calls = []
        self.actions = []
        self.fail_at = self.change_at = None
        self.opened = False

    def call(self, name):
        self.calls.append(name)
        if len(self.calls) == self.fail_at:
            raise OSError("interrupted")
        if len(self.calls) == self.change_at:
            self.boot = "different-boot"

    def identity(self):
        self.call("identity")
        return dict(bootId=self.boot, deviceBindingSha256="a" * 64)

    def snapshot(self):
        self.call("snapshot")
        return self.state.copy()

    def apply(self, key, value):
        self.call("apply:" + key)
        if key == "services":
            assert self.opened, "Bind only after activity opt-in"
            assert value.startswith(OTHER + ":")
        self.state[key] = value
        self.actions.append((key, value))

    def open_helper(self):
        self.call("open")
        assert self.state["permission"]
        assert self.state["RUN_IN_BACKGROUND"] == "allow"
        assert self.state["RUN_ANY_IN_BACKGROUND"] == "allow"
        assert self.state["idleWhitelisted"]
        self.opened = True
        self.actions.append(("open", True))


class Tests(unittest.TestCase):
    def test_list_preserves_others_and_recognizes_alias(self):
        self.assertEqual(m.append_service(OTHER), OTHER + ":" + m.COMPONENT)
        for own in (m.COMPONENT, m.FULL_COMPONENT):
            value = OTHER + ":" + own
            self.assertEqual(m.append_service(value), value)
        for value in ("", "null"):
            self.assertEqual(m.append_service(value), m.COMPONENT)
        for value in ("x", OTHER + ":", m.COMPONENT + ":" + m.FULL_COMPONENT,
                      "x/.a:x/.a", "x/.a\n", "x/.a;reboot"):
            with self.assertRaises(ValueError):
                m.append_service(value)

    def test_inspect_has_no_changes_or_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Device()
            r = m.provision(d, "a" * 64, Path(tmp) / "inspect")
            self.assertTrue(r["inspectionComplete"])
            self.assertFalse(r["complete"])
            self.assertEqual(d.actions, [])

    def test_enable_order_and_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Device()
            r = m.provision(d, "a" * 64, Path(tmp) / "enable", True)
            self.assertTrue(r["complete"])
            self.assertEqual(d.state["services"], OTHER + ":" + m.COMPONENT)
            self.assertEqual(d.state["accessibility"], "1")
            self.assertLess(d.calls.index("open"), d.calls.index("apply:services"))
            self.assertFalse(r["bootRecoveryVerified"])
            self.assertFalse(r["environmentWrites"])

    def test_existing_configuration_only_opens_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Device()
            d.state.update(services=OTHER + ":" + m.FULL_COMPONENT, permission=True,
                           idleWhitelisted=True, RUN_IN_BACKGROUND="allow",
                           RUN_ANY_IN_BACKGROUND="allow")
            r = m.provision(d, "a" * 64, Path(tmp) / "enable", True)
            self.assertTrue(r["complete"])
            self.assertEqual(d.actions, [("open", True)])

    def test_interruption_at_every_device_operation_never_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            d = Device()
            m.provision(d, "a" * 64, base / "reference", True)
            count = len(d.calls)
            for point in range(1, count + 1):
                x = Device()
                x.fail_at = point
                out = base / str(point)
                with self.assertRaises(OSError):
                    m.provision(x, "a" * 64, out, True)
                if (out / "journal.json").exists():
                    record = json.loads((out / "journal.json").read_text())
                    self.assertFalse(record["complete"])
                self.assertIn(x.state["services"], (OTHER, OTHER + ":" + m.COMPONENT))

    def test_reboot_or_concurrent_settings_change_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Device()
            d.change_at = 4
            with self.assertRaises(ValueError):
                m.provision(d, "a" * 64, Path(tmp) / "reboot", True)
            self.assertEqual(d.actions, [])
            d = Device()
            old = d.snapshot
            def changed():
                if len(d.calls) >= 3:
                    d.state["services"] = OTHER + ":another.app/.Service"
                return old()
            d.snapshot = changed
            with self.assertRaises(ValueError):
                m.provision(d, "a" * 64, Path(tmp) / "changed", True)
            self.assertEqual(d.actions, [])

    def test_wrong_owner_and_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Device()
            with self.assertRaises(ValueError):
                m.provision(d, "c" * 64, Path(tmp) / "wrong", True)
            self.assertEqual(d.actions, [])
            with self.assertRaises(FileExistsError):
                m.provision(d, "a" * 64, Path(tmp), True)
            self.assertEqual(d.actions, [])


if __name__ == "__main__":
    unittest.main()
