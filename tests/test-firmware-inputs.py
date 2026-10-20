#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Synthetic package checks; no network or device access."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location(
    "firmware_inputs", Path(__file__).resolve().parents[1] / "tools/firmware-inputs.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "input.zip"
        self.write("freeguy", "synthetic-build", "AB")

    def write(self, device, build, kind):
        with zipfile.ZipFile(self.path, "w") as z:
            z.writestr("META-INF/com/android/metadata",
                       f"pre-device={device}\npost-build={build}\nota-type={kind}\n")
        self.record = {"bytes": self.path.stat().st_size,
                       "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
                       "device": "freeguy", "build": "synthetic-build"}

    def test_complete_package(self):
        result = tool.verify(self.path, self.record)
        self.assertTrue(result["verified"])
        self.assertFalse(result["signatureTrustEstablished"])
        self.assertFalse(result["installationPerformed"])

    def test_reject_size_and_content_change(self):
        original = self.path.read_bytes()
        self.path.write_bytes(original[:-1])
        with self.assertRaises(ValueError):
            tool.verify(self.path, self.record)
        changed = bytearray(original)
        changed[10] ^= 1
        self.path.write_bytes(changed)
        with self.assertRaises(ValueError):
            tool.verify(self.path, self.record)

    def test_reject_wrong_target_build_or_type(self):
        for args in (("ladybird", "synthetic-build", "AB"),
                     ("freeguy", "different-build", "AB"),
                     ("freeguy", "synthetic-build", "BLOCK")):
            self.write(*args)
            with self.assertRaises(ValueError):
                tool.verify(self.path, self.record)

    def test_reject_symlink(self):
        link = self.path.with_name("alias.zip")
        link.symlink_to(self.path)
        with self.assertRaises(ValueError):
            tool.verify(link, self.record)

    def test_distribution_hosts(self):
        for url, package in (
            ("https://ota-mitv.cdn.pandora.xiaomi.com/a.zip", "hyperos"),
            ("https://android.googleapis.com/a.zip", "google-tv-donor"),
            ("https://rr1.example.gvt1.com/a.zip", "google-tv-donor"),
        ):
            self.assertEqual(tool.official(url, package), url)
        for url in ("http://android.googleapis.com/a.zip",
                    "https://android.googleapis.com.evil.invalid/a.zip",
                    "https://user:password@android.googleapis.com/a.zip",
                    "https://android.googleapis.com:444/a.zip",
                    "https://example.invalid/a.zip"):
            with self.assertRaises(ValueError):
                tool.official(url, "google-tv-donor")
        with self.assertRaises(ValueError):
            tool.official("https://rr1.example.gvt1.com/a.zip", "hyperos")


if __name__ == "__main__":
    unittest.main()
