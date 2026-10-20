#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""First-install script adaptation: preserve the daily runtime and other files."""
from copy import deepcopy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load("first_install", ROOT / "tools/build-first-install-root.py")
startup = load("startup_fixture", ROOT / "tests/test-runtime-policy-start.py")


def row(name, data=b"", directory=False):
    return {"name": name, "data": data,
            "fields": [1, 0o40700 if directory else 0o100700, 0, 0, 1, 123, 0, 0, 0, 0, 0, 0, 0]}


def fixture():
    entries = [row("first_stage_ramdisk", directory=True)]
    for prefix in ("", "first_stage_ramdisk/"):
        entries.append(row(prefix + "codex", directory=True))
        for name, (original, _) in build.scripts().items():
            entries.append(row(prefix + name, original))
        entries.append(row(prefix + "codex/unrelated", b"retain exactly"))
    return entries


class FirstInstallTests(unittest.TestCase):
    def test_exactly_four_contents_change_with_all_metadata_preserved(self):
        original = fixture()
        before = deepcopy(original)
        changed, report = build.transform(original)
        self.assertEqual(original, before)
        self.assertEqual(len(report), 4)
        changed_names = {r["path"] for r in report}
        for old, new in zip(original, changed):
            self.assertEqual(old["name"], new["name"])
            self.assertEqual(old["fields"], new["fields"])
            self.assertEqual(old["data"] == new["data"], old["name"] not in changed_names)
        root = {r["name"]: r["data"] for r in changed}
        for name in build.scripts():
            self.assertEqual(root[name], root["first_stage_ramdisk/" + name])

    def test_drifted_missing_duplicate_or_already_adapted_scripts_refused(self):
        for case in ("drift", "missing", "duplicate", "adapted"):
            entries = fixture()
            if case == "drift":
                entries[2]["data"] += b"# unexpected\n"
            elif case == "missing":
                entries.pop(2)
            elif case == "duplicate":
                entries.append(deepcopy(entries[2]))
            else:
                entries, _ = build.transform(entries)
            with self.subTest(case=case), self.assertRaises(ValueError):
                build.transform(entries)

    def test_trial_has_no_shared_state_or_daily_voice_dependency(self):
        daily, first = build.scripts()["codex/entry.sh"]
        self.assertIn(b"/codex/helper watchdog-daily ", daily)
        self.assertIn(b'cat "$SHARED/fault"', daily)
        self.assertIn(b"/codex/helper watchdog ", first)
        self.assertNotIn(b"watchdog-daily", first)
        self.assertNotIn(b'shared-default-v1', first)
        self.assertNotIn(b'mount --bind "$SHARED"', first)
        self.assertNotIn(b"/dev/codex-autovoice/start.sh >", first)
        self.assertIn(b'test ! -e "$H/local/codex-system-default-v1"', first)
        self.assertIn(b'test ! -L "$H/local/codex-system-default-v1"', first)
        self.assertIn(b"cut -d . -f 1 /proc/uptime > /dev/codex-health-heartbeat.tmp", first)
        self.assertIn(b'cat /dev/codex-runtime-policy-ready', first)
        self.assertIn(b"sha256sum -c /codex/immutable.sha256", first)
        self.assertIn(b"blockdev --setro", first)
        self.assertIn(b"host-adb-key", first)

    def test_output_scripts_parse_as_shell(self):
        for _, data in build.scripts().values():
            subprocess.run(["sh", "-n"], input=data, check=True, capture_output=True)

    def test_first_policy_runs_after_boot_and_rejects_daily_marker(self):
        source = build.scripts()["codex/runtime-policy-start.sh"][1].decode()
        fixture = startup.StartupTests()
        result = fixture.run_case(source=source, marker_name="codex-first-install-v1",
                                  profile="first-install-v1", boot_after=3)
        self.assertEqual(result, (0, "ready\n", ["sleep", "sleep", "timeout", "policy"]))
        code, _, events = fixture.run_case(source=source, marker_name="codex-first-install-v1")
        self.assertNotEqual(code, 0)
        self.assertEqual(events, [])

    def test_failed_first_policy_does_not_publish_health(self):
        source = build.scripts()["codex/runtime-policy-start.sh"][1].decode()
        for options in ({"policy_exit": 7}, {"timeout_exit": 124}, {"boot_after": 1000}):
            code, marker, _ = startup.StartupTests().run_case(
                source=source, marker_name="codex-first-install-v1",
                profile="first-install-v1", **options)
            self.assertNotEqual(code, 0)
            self.assertIsNone(marker)

    def test_cli_private_build_and_input_output_refusals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "daily.cpio.gz"
            raw = gzip.compress(build.components.pack(fixture()), mtime=0)
            source.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()

            def run(path, out, sha=digest):
                argv = ["build-first-install-root.py", "--daily-ramdisk", str(path),
                        "--daily-sha256", sha, "--output", str(out)]
                with patch("sys.argv", argv), patch("builtins.print"):
                    build.main()

            run(source, root / "built")
            report = json.loads((root / "built/manifest.json").read_text())
            self.assertEqual(len(report["changedEntries"]), 4)
            self.assertFalse(report["deployEligible"])
            self.assertFalse(report["dailySessionCapChanged"])
            before = (root / "built/ramdisk.cpio.gz").read_bytes()
            for out in (root / "built", ROOT / "never-build-here"):
                with self.assertRaises(ValueError):
                    run(source, out)
            self.assertEqual((root / "built/ramdisk.cpio.gz").read_bytes(), before)
            linked = root / "linked"
            linked.symlink_to(source)
            with self.assertRaises(ValueError):
                run(linked, root / "symlink-result")
            with self.assertRaises(ValueError):
                run(source, root / "bad-hash", "0" * 64)
            self.assertFalse((root / "symlink-result").exists())
            self.assertFalse((root / "bad-hash").exists())


if __name__ == "__main__":
    unittest.main()
