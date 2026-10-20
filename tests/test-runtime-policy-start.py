#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the real startup script with isolated command/boot-state fixtures."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def run_case(self, *, boot_after=1, policy_exit=0, timeout_exit=0,
                 uid=0, profile='shared-default-v1', release=14, fileboot=1):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            commands = base / 'commands'
            commands.mkdir()
            dev = base / 'dev'
            dev.mkdir()
            (dev / 'codex-daily-v1').write_text(profile + '\n')
            # A stale marker must not mask a failed policy execution.
            (dev / 'codex-runtime-policy-ready').write_text('stale\n')
            (dev / 'codex-runtime-policy.sh').write_text(
                'echo policy >> "$FIXTURE/events"\nexit "$POLICY_EXIT"\n')
            fixtures = {
                'id': 'echo "$OWNER_UID"',
                'sleep': 'echo sleep >> "$FIXTURE/events"',
                'getprop': '''case "$1" in
ro.codex.fileboot) echo "$FILEBOOT";;
ro.build.version.release) echo "$RELEASE";;
sys.boot_completed)
 N=$(cat "$FIXTURE/reads" 2>/dev/null || echo 0); N=$((N+1))
 echo "$N" > "$FIXTURE/reads"
 if [ "$N" -ge "$BOOT_AFTER" ]; then echo 1; else echo 0; fi;;
*) exit 2;; esac''',
                'timeout': '''[ "$1 $2 $3" = '-s TERM 90' ] || exit 98
shift 3
echo timeout >> "$FIXTURE/events"
[ "$TIMEOUT_EXIT" = 0 ] || exit "$TIMEOUT_EXIT"
exec "$@"''',
            }
            for command, body in fixtures.items():
                path = commands / command
                path.write_text('#!/bin/sh\nset -eu\n' + body + '\n')
                path.chmod(0o700)
            source = (ROOT / 'runtime/runtime-policy-start.sh').read_text()
            source = source.replace('/system/bin/sh', '/bin/sh').replace('/dev/codex-', str(dev / 'codex-'))
            script = base / 'start.sh'
            script.write_text(source)
            env = dict(os.environ, FIXTURE=str(base), OWNER_UID=str(uid), RELEASE=str(release),
                       FILEBOOT=str(fileboot), BOOT_AFTER=str(boot_after), POLICY_EXIT=str(policy_exit),
                       TIMEOUT_EXIT=str(timeout_exit), PATH=str(commands) + ':/usr/bin:/bin')
            result = subprocess.run(['/bin/sh', str(script)], env=env, capture_output=True, timeout=10)
            marker = dev / 'codex-runtime-policy-ready'
            events = (base / 'events').read_text().splitlines() if (base / 'events').exists() else []
            return result.returncode, marker.read_text() if marker.exists() else None, events

    def test_success_after_delayed_boot(self):
        code, marker, events = self.run_case(boot_after=3)
        self.assertEqual((code, marker), (0, 'ready\n'))
        self.assertEqual(events, ['sleep', 'sleep', 'timeout', 'policy'])

    def test_unfinished_boot_never_runs_policy(self):
        code, marker, events = self.run_case(boot_after=1000)
        self.assertNotEqual(code, 0)
        self.assertIsNone(marker)
        self.assertEqual(events, ['sleep'] * 240)

    def test_failed_policy_cannot_claim_health(self):
        code, marker, events = self.run_case(policy_exit=7)
        self.assertEqual(code, 7)
        self.assertIsNone(marker)
        self.assertEqual(events, ['timeout', 'policy'])

    def test_timeout_cannot_claim_health(self):
        code, marker, events = self.run_case(timeout_exit=124)
        self.assertEqual(code, 124)
        self.assertIsNone(marker)
        self.assertEqual(events, ['timeout'])

    def test_wrong_environment_does_not_run_commands(self):
        for kwargs in ({'uid': 2000}, {'release': 11}, {'fileboot': 0}, {'profile': 'wrong'}):
            with self.subTest(kwargs=kwargs):
                code, marker, events = self.run_case(**kwargs)
                self.assertNotEqual(code, 0)
                self.assertEqual(events, [])
                self.assertEqual(marker, 'stale\n')


if __name__ == '__main__':
    unittest.main()
