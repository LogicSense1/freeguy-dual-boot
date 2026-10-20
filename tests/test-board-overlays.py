#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Overlay transformation contracts using synthetic, non-firmware fixtures."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('overlays',
    Path(__file__).resolve().parents[1] / 'tools/render-board-overlays.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def inputs():
    return {
        'stock_vendor_props': b'keep.this=yes\nro.adb.secure=0\nro.adb.secure=0\n',
        'stock_board_init': b'on boot\n    loglevel 3\n    start set_mcu_ota\n    start keep_service\n'
            b'service set_mcu_ota /vendor/bin/unwanted\n    disabled\n'
            b'service ubootlog /vendor/bin/unwanted\n    disabled\n'
            b'service keep_service /vendor/bin/keep\n    disabled\n',
        'stock_audio_policy': b'<audioPolicyConfiguration><globalConfiguration engine_library="configurable"/>'
            b'<mixPort name="primary input"><profile channelMasks="AUDIO_CHANNEL_IN_STEREO"/></mixPort>'
            b'<devicePort tagName="Built-In Mic"><profile channelMasks="AUDIO_CHANNEL_IN_STEREO"/></devicePort>'
            b'<mixPort name="other"><profile channelMasks="AUDIO_CHANNEL_IN_STEREO"/></mixPort></audioPolicyConfiguration>',
        'stock_ssl_init': b'on early-init && property:a=1\n    exec_start test1\n'
            b'on early-init && property:b=1\n    exec_start test2\n    reboot_on_failure reboot,bad\n',
        'stock_keylayout': b'key 1 DIRECT_INPUT\nkey 2 DPAD_UP\n',
        'google_system_init': b'on boot\n    exec_start update_verifier\n'
            b'    exec - system system -- /system/bin/vdc checkpoint prepareCheckpoint\n'
            b'    start keep_service\n',
        'stock_panel_module': b'synthetic-module-not-firmware',
    }


class OverlayTests(unittest.TestCase):
    def render(self, data=None, loops=None):
        data = inputs() if data is None else data
        with patch.object(build, 'PANEL_SHA256', hashlib.sha256(inputs()['stock_panel_module']).hexdigest()):
            return build.render(data, {'data': 15, 'metadata': 16, 'cache': 17} if loops is None else loops)

    def test_unrelated_services_and_channels_preserved(self):
        out = self.render()
        self.assertIn(b'start keep_service', out['init.mt9653.rc'])
        self.assertIn(b'service keep_service /vendor/bin/keep', out['init.mt9653.rc'])
        self.assertNotIn(b'set_mcu_ota', out['init.mt9653.rc'])
        self.assertNotIn(b'ubootlog', out['init.mt9653.rc'])
        self.assertEqual(out['audio_policy_configuration.xml'].count(b'AUDIO_CHANNEL_INDEX_MASK_4'), 2)
        self.assertEqual(out['audio_policy_configuration.xml'].count(b'AUDIO_CHANNEL_IN_STEREO'), 1)
        self.assertIn(b'key 2 DPAD_UP', out['remote-32b8.kl'])
        self.assertIn(b'keep.this=yes', out['vendor-build.prop'])
        self.assertEqual(out['vendor-build.prop'].count(b'ro.adb.secure='), 1)
        self.assertIn(b'ro.adb.secure=1\n', out['vendor-build.prop'])
        self.assertEqual(out['mik-logo-detection.ko'], inputs()['stock_panel_module'])

    def test_modified_panel_module_rejected(self):
        data = inputs()
        data['stock_panel_module'] += b'changed'
        with self.assertRaises(ValueError):
            self.render(data)

    def test_missing_or_ambiguous_patch_locations_rejected(self):
        for key, old, new in (
            ('stock_keylayout', b'DIRECT_INPUT', b'DIRECT_INPUT DIRECT_INPUT'),
            ('stock_audio_policy', b'name="primary input"', b'name="other input"'),
            ('stock_board_init', b'loglevel 3', b'loglevel 7'),
            ('stock_ssl_init', b'on early-init &&', b'on init &&'),
            ('google_system_init', b'prepareCheckpoint', b'changedCheckpoint')):
            data = inputs()
            data[key] = data[key].replace(old, new)
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.render(data)

    def test_loop_targets_are_fixed_and_unique(self):
        for loops in ({'data': 15, 'metadata': 15, 'cache': 17},
                      {'data': '../disk', 'metadata': 16, 'cache': 17},
                      {'system': 15, 'metadata': 16, 'cache': 17},
                      {'data': True, 'metadata': 16, 'cache': 17},
                      {'data': 256, 'metadata': 16, 'cache': 17}):
            with self.assertRaises(ValueError):
                self.render(loops=loops)


if __name__ == '__main__':
    unittest.main()
