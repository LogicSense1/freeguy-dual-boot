#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reject accidental permission or accessibility expansion in the helper build."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('helper_build', ROOT / 'tools/build-adb-helper.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
A = build.ANDROID


class HelperBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'helper'
        shutil.copytree(ROOT / 'adb-helper', self.source)

    def change_manifest(self, change):
        file = self.source / 'AndroidManifest.xml'
        tree = ET.parse(file)
        change(tree.getroot())
        tree.write(file)

    def test_checked_manifest_and_both_locales(self):
        self.assertEqual(build.check_manifest(self.source), 4)

    def test_network_permission_cannot_be_added_silently(self):
        self.change_manifest(lambda root: ET.SubElement(
            root, 'uses-permission', {A + 'name': 'android.permission.INTERNET'}))
        with self.assertRaisesRegex(ValueError, 'permission set'):
            build.check_manifest(self.source)

    def test_service_binding_permission_cannot_be_removed(self):
        def edit(root):
            for node in root.findall('application/service'):
                if node.get(A + 'name') == '.ConnectionService':
                    node.attrib.pop(A + 'permission')
        self.change_manifest(edit)
        with self.assertRaisesRegex(ValueError, 'service protection'):
            build.check_manifest(self.source)

    def test_window_access_and_ui_events_are_refused(self):
        file = self.source / 'res/xml/connection_service.xml'
        original = file.read_bytes()
        for name, value in (('canRetrieveWindowContent', 'true'),
                            ('canPerformGestures', 'true'),
                            ('accessibilityEventTypes', 'typeAllMask')):
            file.write_bytes(original)
            tree = ET.parse(file)
            tree.getroot().set(A + name, value)
            tree.write(file)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'capabilities'):
                build.check_manifest(self.source)

    def test_incomplete_translation_is_refused(self):
        file = self.source / 'res/values-zh/strings.xml'
        tree = ET.parse(file)
        tree.getroot().remove(tree.getroot()[0])
        tree.write(file)
        with self.assertRaisesRegex(ValueError, 'Localization'):
            build.check_manifest(self.source)


if __name__ == '__main__':
    unittest.main()
