#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Synthetic profile rejection tests. They do not validate any television."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest
import sys
sys.dont_write_bytecode = True

p = Path(__file__).resolve().parents[1] / 'tools/build-switch-app.py'
spec = importlib.util.spec_from_file_location('switch_build', p)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            'model': 'L75MA-SPL', 'device': 'freeguy', 'bootloader': '01.01.250225',
            'immutableFiles': {p: '1' * 64 for p in build.EXPECTED},
            'normalizedBootargsSha256': ['2' * 64], 'serialUndoVerified': True}

    def test_trial_keeps_default_off(self):
        s = build.pins(self.profile, False)
        self.assertIn('ENABLE_PERSISTENT_DEFAULT=false', s)
        self.assertEqual(s.count('"/data/local/tmp/rd-root/envdaily"'), 1)

    def test_rejects_missing_or_additional_dependency(self):
        for key in build.EXPECTED:
            p = deepcopy(self.profile)
            del p['immutableFiles'][key]
            with self.assertRaises(ValueError):
                build.pins(p, False)
        p = deepcopy(self.profile)
        p['immutableFiles']['/dev/block/mmcblk0'] = '1' * 64
        with self.assertRaises(ValueError):
            build.pins(p, False)

    def test_rejects_nonmatching_device_and_unverified_undo(self):
        for key, bad in [('model', 'another-tv'), ('device', 'ladybird'),
                         ('bootloader', 'old'), ('serialUndoVerified', False)]:
            p = deepcopy(self.profile)
            p[key] = bad
            with self.assertRaises(ValueError):
                build.pins(p, False)

    def test_rejects_source_injection(self):
        for bad in ['";System.exit(0);//', '../private', 'g' * 64, '1' * 63]:
            p = deepcopy(self.profile)
            p['normalizedBootargsSha256'] = [bad]
            with self.assertRaises(ValueError):
                build.pins(p, False)

    def test_requires_matching_long_runtime_for_default(self):
        with self.assertRaises(ValueError):
            build.pins(self.profile, True)
        p = deepcopy(self.profile)
        p['dailyRuntime'] = {'passed': True, 'awakeSeconds': 3701, 'loaderSha256': '1' * 64}
        self.assertIn('ENABLE_PERSISTENT_DEFAULT=true', build.pins(p, True))
        for key, bad in [('passed', False), ('awakeSeconds', 3600), ('loaderSha256', '3' * 64)]:
            q = deepcopy(p)
            q['dailyRuntime'][key] = bad
            with self.assertRaises(ValueError):
                build.pins(q, True)

    def test_clock_model_requires_live_binary_and_all_failure_checks(self):
        p = deepcopy(self.profile)
        evidence = {
            'passed': True, 'method': 'binary-clock-model',
            'loaderSha256': '1' * 64, 'helperSha256': '3' * 64,
            'liveHelperHashMatched': True, 'liveDailyModeConfirmed': True,
            'transitionGuardAbsent': True, 'healthyHourPassed': True,
            'healthyMonthPassed': True, 'startupFailurePassed': True,
            'healthFailurePassed': True}
        p['dailyRuntime'] = evidence
        self.assertIn('ENABLE_PERSISTENT_DEFAULT=true', build.pins(p, True))
        for key in evidence:
            q = deepcopy(p)
            del q['dailyRuntime'][key]
            with self.assertRaises(ValueError, msg=key):
                build.pins(q, True)
        q = deepcopy(p)
        q['dailyRuntime']['helperSha256'] = 'bad'
        with self.assertRaises(ValueError):
            build.pins(q, True)


if __name__ == '__main__':
    unittest.main()
