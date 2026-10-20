#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Synthetic serial export and independent local-copy verification."""
import copy
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("prefix", ROOT / "tools/backup-boot-prefix.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
STOCK = "root=/dev/ram init=/init rw loglevel=0 androidboot.bootreason=cold lastblval=75 example=fixture"
BINDING, SCHEME = "a" * 64, "freeguy-emmc-cid-serial-v1"
DIRECTORY = "/freeguy-backup-fixture01"


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.table = [(i, i * 2, i * 2 + 1, "fixture" + str(i)) for i in range(1, 43)]
        self.table_bytes = b"Partition Map for MMC device 0 -- Partition Type: EFI\n"
        self.table_bytes += b"\n".join(f'{i} 0x{lo:x} 0x{hi:x} "{name}"'.encode()
                                        for i, lo, hi, name in self.table)
        digest = hashlib.sha256(json.dumps(self.table, separators=(",", ":")).encode()).hexdigest()
        for module, field, value in ((m, "PREFIX_SECTORS", 4), (m, "CHUNK_SECTORS", 2),
                                      (m, "GEOMETRY_SHA", digest), (m.a, "PREFIX_BYTES", 2048)):
            patcher = patch.object(module, field, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.calls, self.exported, self.attempts = [], {}, 0
        self.memory = b""
        self.fail = None
        self.payloads = [b"a" * 1024, b"b" * 1024]
        self.allowed = [m.recovery.fingerprint(STOCK)]
        self.output_count = 0

    def handshake(self):
        pass

    def close(self):
        pass

    def command(self, command):
        self.attempts += 1
        if self.fail == (self.attempts, "before"):
            raise TimeoutError("synthetic pre-command failure")
        self.calls.append(command)
        fixed = {
            "version": b"U-Boot2019.04-g43964869 (Feb 25 2025)\nfreeguy#",
            "md.b 0x1c040480 1": b"1c040480: 21 \nfreeguy#",
            "md.b 0x1c040400 1": b"1c040400: 00 \nfreeguy#",
            "printenv makedata factory_mode devicestate":
                b"makedata=true\nfactory_mode=0\ndevicestate=lock\nfreeguy#",
            "printenv bootargs": ("bootargs=" + STOCK + "\nfreeguy#").encode(),
            "mmc part": self.table_bytes + b"\nfreeguy#",
            "usb start": b"USB started\nfreeguy#",
            "fatls usb 0:1 " + DIRECTORY: b"0 file(s), 2 dir(s)\nfreeguy#"}
        if command in fixed:
            raw = fixed[command]
        elif command.startswith("mmc read "):
            offset = int(command.split()[3], 16)
            self.memory = self.payloads[offset // 2]
            raw = (command + "\r\nfreeguy# ").encode()
        elif command.startswith("crc32 "):
            raw = f"crc32 ==> {zlib.crc32(self.memory):08x}\nfreeguy#".encode()
        elif command.startswith("fatwrite "):
            name = command.split()[4].rsplit("/", 1)[1]
            self.assertNotIn(name, self.exported)
            self.exported[name] = self.memory
            raw = f"{len(self.memory)} bytes written\nfreeguy#".encode()
        else:
            raise AssertionError(command)
        if self.fail == (self.attempts, "after"):
            raise TimeoutError("synthetic lost response")
        return raw

    def capture(self, name="capture"):
        output = self.root / name
        output.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            return m.capture(self, DIRECTORY, self.allowed, BINDING, SCHEME, output)

    def prepare_copy(self):
        record = self.capture()
        source = self.root / "usb"
        source.mkdir()
        for name, data in self.exported.items():
            (source / name).write_bytes(data)
        return record, source

    def test_capture_then_verified_independent_copy(self):
        record, source = self.prepare_copy()
        self.assertFalse(record["internalWritesIssued"])
        self.assertFalse(record["hostReadbackVerified"])
        result = m.verify(record, source, self.root / "copy", BINDING, SCHEME)
        self.assertEqual(result["prefixBytes"], 2048)
        for row in result["chunks"]:
            self.assertEqual((self.root / "copy" / row["file"]["path"]).read_bytes(),
                             (source / row["file"]["path"]).read_bytes())
        self.assertTrue((self.root / "copy/backup.json").exists())
        self.assertFalse(any(c.startswith(("saveenv", "setenv", "reset", "mmc write")) for c in self.calls))

    def test_command_and_path_limits(self):
        console = m.PrefixConsole.__new__(m.PrefixConsole)
        console.allowed = m.commands(DIRECTORY)
        console.authorized_write = None
        with patch.object(m.recovery.Console, "command", return_value=b"freeguy#") as send:
            for text in ("saveenv", "reset", "mmc write 0x2c000000 0 1",
                         "mmc read 0x27400000 0 1", "fatwrite mmc 0:1 0x2c000000 /x 0x400"):
                with self.subTest(text=text), self.assertRaises(ValueError):
                    console.command(text)
            send.assert_not_called()
            console.command("mmc part")
            send.assert_called_once_with("mmc part", timeout=180)
            self.assertIsNone(console.authorized_write)
        for path in ("/", "/old", "/freeguy-backup-../../x", DIRECTORY + ";reset"):
            with self.assertRaises(ValueError):
                m.commands(path)

    def test_changed_geometry_refused_before_usb_write(self):
        self.table_bytes = self.table_bytes.replace(b'"fixture1"', b'"other"')
        with self.assertRaises(ValueError):
            self.capture()
        self.assertEqual(self.exported, {})
        self.assertNotIn("usb start", self.calls)

    def test_all_command_interruptions_leave_incomplete_export_without_retry(self):
        self.capture("reference")
        total = self.attempts
        for position in range(1, total + 1):
            for side in ("before", "after"):
                self.attempts, self.calls, self.exported = 0, [], {}
                self.fail = position, side
                name = f"interrupted-{position}-{side}"
                with self.subTest(position=position, side=side), self.assertRaises(TimeoutError):
                    self.capture(name)
                self.assertEqual(self.attempts, position)
                record = json.loads((self.root / name / "capture.json").read_bytes())
                self.assertFalse(record["complete"])
                self.assertIsNotNone(record["pending"])

    def test_bad_backup_identity_geometry_and_crc_are_rejected(self):
        record, source = self.prepare_copy()
        changes = [dict(record, complete=False), dict(record, deviceBindingSha256="b" * 64),
                   dict(record, geometrySha256="0" * 64)]
        bad = copy.deepcopy(record)
        bad["chunks"][0]["offset"] = 512
        changes.append(bad)
        for i, value in enumerate(changes):
            with self.assertRaises(ValueError):
                m.verify(value, source, self.root / f"bad-{i}", BINDING, SCHEME)
            self.assertFalse((self.root / f"bad-{i}").exists())
        (source / "disk-prefix-000.img").write_bytes(b"x" * 1024)
        with self.assertRaises(ValueError):
            m.verify(record, source, self.root / "corrupt", BINDING, SCHEME)
        self.assertFalse((self.root / "corrupt/backup.json").exists())

    def test_existing_copy_extra_files_and_symlinks_refused(self):
        record, source = self.prepare_copy()
        destination = self.root / "existing"
        destination.mkdir()
        sentinel = destination / "keep"
        sentinel.write_bytes(b"owner data")
        with self.assertRaises(ValueError):
            m.verify(record, source, destination, BINDING, SCHEME)
        self.assertEqual(sentinel.read_bytes(), b"owner data")
        (source / "extra").write_text("unexpected")
        with self.assertRaises(ValueError):
            m.verify(record, source, self.root / "extra-copy", BINDING, SCHEME)
        (source / "extra").unlink()
        path = source / "disk-prefix-000.img"
        path.unlink()
        path.symlink_to(source / "disk-prefix-001.img")
        with self.assertRaises(ValueError):
            m.verify(record, source, self.root / "link-copy", BINDING, SCHEME)

    def test_quiet_read_requires_exact_expected_response(self):
        original = self.command
        def failed(command):
            if command.startswith("mmc read "):
                return b"unexpected read status\nfreeguy#"
            return original(command)
        with patch.object(self, "command", side_effect=failed), self.assertRaises(ValueError):
            self.capture()
        self.assertEqual(self.exported, {})

    def test_cli_capture_and_verify_complete_without_internal_writes(self):
        out = self.root / "cli-capture"
        args = ["backup-boot-prefix.py", "capture", "--port", "/dev/fixture",
                "--usb-directory", DIRECTORY, "--recovery-profile", "/private/fixture",
                "--known-device-binding", BINDING, "--known-device-binding-scheme", SCHEME,
                "--output", str(out)]
        with patch.object(sys, "argv", args), \
             patch.object(m.recovery, "read_profile", return_value=self.allowed), \
             patch.object(m, "PrefixConsole", return_value=self), \
             contextlib.redirect_stdout(io.StringIO()):
            m.main()
        source = self.root / "cli-usb"
        source.mkdir()
        for name, data in self.exported.items():
            (source / name).write_bytes(data)
        raw = (out / "capture.json").read_bytes()
        args = ["backup-boot-prefix.py", "verify", "--capture-record", str(out / "capture.json"),
                "--capture-record-sha256", hashlib.sha256(raw).hexdigest(), "--source", str(source),
                "--known-device-binding", BINDING, "--known-device-binding-scheme", SCHEME,
                "--output", str(self.root / "cli-copy")]
        with patch.object(sys, "argv", args), patch.object(m, "PrefixConsole") as port, \
             contextlib.redirect_stdout(io.StringIO()):
            m.main()
            port.assert_not_called()
        self.assertTrue((self.root / "cli-copy/backup.json").is_file())


if __name__ == "__main__":
    unittest.main()
