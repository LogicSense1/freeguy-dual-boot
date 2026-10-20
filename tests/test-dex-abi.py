#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Synthetic declaration fixtures; no OEM APK, implementation or credentials."""
import hashlib
import importlib.util
from pathlib import Path
import struct
import sys
import unittest
import zlib
sys.dont_write_bytecode = True
TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
from dex_abi import Dex
spec = importlib.util.spec_from_file_location('voice_build', TOOLS / 'build-voice.py')
voice = importlib.util.module_from_spec(spec)
spec.loader.exec_module(voice)


def finish(raw):
    struct.pack_into('<I', raw, 32, len(raw))
    raw[12:32] = hashlib.sha1(raw[32:]).digest()
    struct.pack_into('<I', raw, 8, zlib.adler32(raw[12:]))
    return bytes(raw)


def fixture():
    """Metadata-only fixture with one public native void accept(byte[]) method."""
    strings = [b'Lfixture/Voice;', b'Ljava/lang/Object;', b'V', b'VL', b'[B', b'accept']
    raw = bytearray(204)
    raw[:8] = b'dex\n035\0'
    struct.pack_into('<II', raw, 36, 112, 0x12345678)
    for header, count, offset in ((56, 6, 112), (64, 4, 136), (72, 1, 152),
                                  (80, 0, 0), (88, 1, 164), (96, 1, 172)):
        struct.pack_into('<II', raw, header, count, offset)
    for n, text in enumerate(strings):
        struct.pack_into('<I', raw, 112 + n * 4, len(raw))
        raw.extend(bytes([len(text)]) + text + b'\0')
    struct.pack_into('<4I', raw, 136, 0, 1, 2, 4)
    raw.extend(b'\0' * (-len(raw) % 4))
    parameters = len(raw)
    raw.extend(struct.pack('<IH', 1, 3))
    struct.pack_into('<III', raw, 152, 3, 2, parameters)
    struct.pack_into('<HHI', raw, 164, 0, 0, 5)
    declarations = len(raw)
    raw.extend(bytes([0, 0, 0, 1, 0, 0x81, 2, 0]))
    struct.pack_into('<8I', raw, 172, 0, 1, 1, 0, 0xffffffff, 0, declarations, 0)
    struct.pack_into('<II', raw, 104, len(raw) - 204, 204)
    return finish(raw)


class AbiTests(unittest.TestCase):
    def test_declared_signature_without_method_body(self):
        flags, methods = Dex(fixture()).class_methods(b'Lfixture/Voice;')
        self.assertEqual(flags, 1)
        self.assertEqual(methods, {(b'accept', b'V', (b'[B',)): 0x101})

    def test_truncation(self):
        raw = fixture()
        for n in range(len(raw)):
            with self.assertRaises(ValueError):
                Dex(raw[:n])

    def test_checksums_and_unsupported_container(self):
        raw = bytearray(fixture())
        raw[-1] ^= 1
        with self.assertRaises(ValueError):
            Dex(bytes(raw))
        raw[4:7] = b'041'
        with self.assertRaises(ValueError):
            Dex(finish(raw))

    def test_table_and_prototype_bounds(self):
        for offset, value in ((60, 0xfffffff0), (72, 0xffffffff), (160, 0xfffffff0), (196, 0xfffffff0)):
            raw = bytearray(fixture())
            struct.pack_into('<I', raw, offset, value)
            with self.assertRaises(ValueError):
                Dex(finish(raw)).class_methods(b'Lfixture/Voice;')

    def test_wrong_owner(self):
        raw = bytearray(fixture())
        struct.pack_into('<H', raw, 164, 1)
        with self.assertRaises(ValueError):
            Dex(finish(raw)).class_methods(b'Lfixture/Voice;')

    def test_duplicate_method(self):
        raw = bytearray(fixture())
        position = struct.unpack_from('<I', raw, 196)[0]
        raw[position:] = bytes([0, 0, 1, 1, 0, 0x81, 2, 0, 0, 0x81, 2, 0])
        with self.assertRaises(ValueError):
            Dex(finish(raw)).class_methods(b'Lfixture/Voice;')

    def test_uleb_overflow_and_truncation(self):
        for raw in (b'\x80', b'\xff\xff\xff\xff\x10', b'\x80\x80\x80\x80\x80'):
            d = Dex.__new__(Dex)
            d.raw = raw
            with self.assertRaises(ValueError):
                d.uleb(0)

    def test_required_abi_rejects_changed_access(self):
        class Fake:
            def class_methods(self, descriptor):
                methods = dict(voice.REQUIRED['MitvVoiceCallback'])
                methods[(b'notifyAudioStart', b'V', ())] = 1
                return 1, methods
        with self.assertRaises(ValueError):
            voice.declaration(Fake(), 'MitvVoiceCallback')

    def test_generated_declarations_only(self):
        class Fake:
            def class_methods(self, descriptor):
                return 1, dict(voice.REQUIRED['MitvVoiceCallback'])
        text = voice.declaration(Fake(), 'MitvVoiceCallback')
        self.assertEqual(text.count('throw new UnsupportedOperationException'), 7)
        self.assertNotIn('JADX', text)


if __name__ == '__main__':
    unittest.main()
