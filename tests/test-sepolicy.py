#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reject unsupported policy gaps and preserve scoped permission mappings."""
import importlib.util
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('policy_build',
    Path(__file__).resolve().parents[1] / 'tools/build-sepolicy.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def fixture():
    inputs = {name: '' for name in build.ORDER}
    inputs[build.ORDER[0]] = '(type existing)\n(typeattribute service_manager_type)\n'
    contexts = {'contexts': '\n'.join('name u:object_r:' + t + ':s0' for t in
                ('existing', 'hdmi_audio', 'pq_repository_service', 'vendor_mitv_type'))}
    vendor = ('(genfscon fuseblk / (u object_r ntfs ((s0) (s0))))\n'
              '(allow client ntfs (file (read)))\n(allow client other (file (write)))\n')
    donor = '\n'.join('(type ' + t + ')\n(roletype object_r ' + t + ')\n'
                      '(typeattributeset service_manager_type (' + t + '))' for t in
                      ('hdmi_audio', 'pq_repository_service', 'vendor_mitv_type'))
    return inputs, contexts, vendor, donor


class PolicyTests(unittest.TestCase):
    def test_scoped_mirror_and_original_rules_retained(self):
        value, report = build.reconcile(*fixture())
        self.assertIn('(allow client ntfs (file (read)))', value)
        self.assertIn('(allow client fuseblk (file (read)))', value)
        self.assertEqual(value.count('(allow client other (file (write)))'), 1)
        self.assertNotIn('(allow client fuseblk (file (write)))', value)
        self.assertNotIn('(genfscon fuseblk /', value)
        self.assertEqual(report['mirroredVendorNtfsRules'], 1)
        self.assertEqual(set(report['addedTypes']), {'hdmi_audio', 'pq_repository_service', 'vendor_mitv_type'})

    def test_additional_context_gap_refused(self):
        inputs, contexts, vendor, donor = fixture()
        contexts['new'] = 'unknown u:object_r:unexpected_type:s0'
        with self.assertRaises(ValueError):
            build.reconcile(inputs, contexts, vendor, donor)

    def test_missing_or_duplicate_fuseblk_label_refused(self):
        inputs, contexts, vendor, donor = fixture()
        for bad in (vendor.replace('genfscon fuseblk', 'genfscon otherfs'), vendor + vendor.splitlines()[0] + '\n'):
            with self.assertRaises(ValueError):
                build.reconcile(inputs, contexts, bad, donor)

    def test_unknown_or_compound_donor_membership_refused(self):
        inputs, contexts, vendor, donor = fixture()
        for bad in (donor.replace('service_manager_type', 'unknown_attribute'),
                    donor.replace('(hdmi_audio))', '(and hdmi_audio existing))'),
                    donor.replace('(roletype object_r hdmi_audio)', '')):
            with self.assertRaises(ValueError):
                build.reconcile(inputs, contexts, vendor, bad)


if __name__ == '__main__':
    unittest.main()
