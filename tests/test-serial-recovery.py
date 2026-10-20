#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import io
import json
import os
from pathlib import Path
import pty
import select
import stat
import struct
import tempfile
import threading
import time
import tty
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("serial_recovery", ROOT / "tools/serial-recovery.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)

# Synthetic command line: no device identities or vendor firmware data.
STOCK = ("console=ttyS0 root=/dev/ram init=/init rw loglevel=0 "
         "androidboot.bootreason=cold lastblval=75 example=abc")
TRIAL = ("console=ttyS0 root=/dev/mmcblk0p42 init=/local/tmp/rd-root/envdaily "
         "ro loglevel=7 androidboot.bootreason=cold lastblval=75 example=abc"
         + recovery.SUFFIX)
ALLOWED = [recovery.fingerprint(STOCK)]


class FakeConsole:
    def __init__(self, value=TRIAL):
        self.value, self.calls, self.authorized_write = value, [], None
        self.reads = {
            "version": b"U-Boot2019.04-g43964869 (Feb 25 2025)\r\nfreeguy#",
            "md.b 0x1c040480 1": b"1c040480: 21 \r\nfreeguy#",
            "md.b 0x1c040400 1": b"1c040400: 00 \r\nfreeguy#",
            "printenv makedata factory_mode devicestate":
                b"makedata=true\r\nfactory_mode=0\r\ndevicestate=lock\r\nfreeguy#",
        }
        self.save_ok, self.readback_ok, self.drift = True, True, False
        self.bootargs_reads = 0

    def command(self, command):
        self.calls.append(command)
        if command in self.reads:
            return self.reads[command]
        if command == "printenv bootargs":
            self.bootargs_reads += 1
            suffix = " changed=1" if self.drift and self.bootargs_reads > 1 else ""
            return ("bootargs=" + self.value + suffix + "\r\nfreeguy#").encode()
        if command == self.authorized_write:
            if self.readback_ok:
                self.value = STOCK
            return b"freeguy#"
        if command == "saveenv":
            return b"done\r\nfreeguy#" if self.save_ok else b"write error\r\nfreeguy#"
        raise AssertionError("Unexpected command: " + command)


class SerialRecoveryTest(unittest.TestCase):
    def test_inspection_never_writes(self):
        c = FakeConsole()
        result = recovery.recover(c, ALLOWED)
        self.assertTrue(result["restorationAvailable"])
        self.assertFalse(result["environmentSaved"])
        self.assertTrue(all(cmd in recovery.READS for cmd in c.calls))

    def test_exact_restore_and_no_reboot(self):
        c = FakeConsole()
        result = recovery.recover(c, ALLOWED, write=True)
        self.assertTrue(result["environmentSaved"])
        self.assertFalse(result["persistentReadbackVerified"])
        self.assertFalse(result["subsequentBootVerified"])
        self.assertEqual(c.value, STOCK)
        self.assertEqual([cmd for cmd in c.calls if cmd not in recovery.READS],
                         [recovery.encode_setenv(STOCK), "saveenv"])

    def test_already_stock_restore_is_read_only(self):
        c = FakeConsole(STOCK)
        result = recovery.recover(c, ALLOWED, write=True)
        self.assertTrue(result["alreadyStock"])
        self.assertFalse(result["environmentSaved"])
        self.assertTrue(all(cmd in recovery.READS for cmd in c.calls))

    def test_dynamic_fields_preserved(self):
        trial = TRIAL.replace("reason=cold", "reason=reboot").replace("lastblval=75", "lastblval=12")
        target = recovery.restore_plan(trial, ALLOWED)
        self.assertIn("reason=reboot", target)
        self.assertIn("lastblval=12", target)

    def test_rejects_unrecognized_configurations(self):
        for value in (
            TRIAL.replace("envdaily", "another-loader"),
            TRIAL.replace("rootflags=norecovery", "rootflags=other"),
            TRIAL.replace("root=/dev/mmcblk0p42", "root=/dev/mmcblk0p41"),
            TRIAL.replace("example=abc", "example=different"),
            TRIAL + " root=/dev/ram",
            TRIAL.replace(" rw ", " ro ") + " rw",
            TRIAL.replace("lastblval=75", "lastblval=999"),
            TRIAL.replace("reason=cold", "reason=unknown"),
            TRIAL.replace("lastblval=75", "lastblval=75 lastblval=1"),
            TRIAL + "\nreset",
            " " + TRIAL,
            TRIAL + " ",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                recovery.restore_plan(value, ALLOWED)

    def test_console_identity_guards_before_mutation(self):
        changes = {
            "version": b"U-Boot2019.04-old\r\nfreeguy#",
            "md.b 0x1c040480 1": b"1c040480: 20 \r\nfreeguy#",
            "md.b 0x1c040400 1": b"1c040400: 01 \r\nfreeguy#",
            "printenv makedata factory_mode devicestate":
                b"makedata=false\r\nfactory_mode=0\r\ndevicestate=lock\r\nfreeguy#",
        }
        for key, value in changes.items():
            c = FakeConsole()
            c.reads[key] = value
            with self.subTest(command=key), self.assertRaises(ValueError):
                recovery.recover(c, ALLOWED, write=True)
            self.assertTrue(all(cmd in recovery.READS for cmd in c.calls))

    def test_config_change_prevents_write(self):
        c = FakeConsole()
        c.drift = True
        with self.assertRaises(ValueError):
            recovery.recover(c, ALLOWED, write=True)
        self.assertNotIn("saveenv", c.calls)
        self.assertIsNone(c.authorized_write)

    def test_ram_readback_failure_is_not_saved(self):
        c = FakeConsole()
        c.readback_ok = False
        with self.assertRaises(ValueError):
            recovery.recover(c, ALLOWED, write=True)
        self.assertNotIn("saveenv", c.calls)

    def test_save_error_not_reported_as_success(self):
        c = FakeConsole()
        c.save_ok = False
        with self.assertRaises(ValueError):
            recovery.recover(c, ALLOWED, write=True)
        self.assertEqual(c.calls[-1], "saveenv")

    def test_ambiguous_reply_refused(self):
        for value in (b"freeguy#", b"bootargs=a\r\nbootargs=b\r\nfreeguy#"):
            with self.assertRaises(ValueError):
                recovery.extract_bootargs(value)

    def test_shell_metacharacters_escaped(self):
        value = STOCK + r" text=a'b;c$d\q"
        self.assertEqual(recovery.encode_setenv(value),
                         "setenv bootargs " + STOCK + r" text=a\'b\;c\$d\\q")

    def test_prompt_must_end_response_on_its_own_line(self):
        for data in (b"freeguy#", b"\r\nfreeguy# ", b"reply\r\nfreeguy#"):
            self.assertTrue(recovery.has_prompt(data))
        for data in (b"example=freeguy#", b"freeguy# bootm\r\n", b"freeguy#\r\nStarting kernel",
                     b"freeguy#\n", b"freeguy# reset"):
            self.assertFalse(recovery.has_prompt(data))

    def test_done_plus_error_is_not_a_successful_save(self):
        console = FakeConsole()
        original = console.command
        def command(value):
            if value == "saveenv":
                return b"write failed\r\ndone\r\nfreeguy#"
            return original(value)
        console.command = command
        with self.assertRaisesRegex(ValueError, "Save completion"):
            recovery.recover(console, ALLOWED, write=True)


def stored(value=STOCK, extra=()):
    body = b"\0".join([b"bootargs=" + value.encode()] + list(extra)) + b"\0\0"
    body = body.ljust(recovery.ENV_SIZE - 4, b"\0")
    return struct.pack("<I", zlib.crc32(body)) + body


class PersistedTests(unittest.TestCase):
    def setUp(self):
        self.files = {name: stored() for name in ("uboot.env", "uboot.env.bak")}
        self.mode = stat.S_IFREG | 0o600
        self.links = 1
        self.size = recovery.ENV_SIZE
        self.raw = bytes(recovery.PARTITION_SIZE)

    def reader(self, stream):
        self.assertEqual(len(stream.read()), recovery.PARTITION_SIZE)
        def get(path):
            return SimpleNamespace(
                inode=SimpleNamespace(i_mode=self.mode, i_links_count=self.links),
                size=self.size, open=lambda: io.BytesIO(self.files[path.lstrip("/")]))
        return SimpleNamespace(get=get)

    def check(self, raw=None):
        return recovery.check_partition(self.raw if raw is None else raw, ALLOWED, self.reader)

    def test_both_physical_environment_files_must_match_profile(self):
        result = self.check()
        self.assertEqual(len(result["files"]), 2)
        self.assertTrue(all(row["crcValid"] and row["stockProfileMatched"] for row in result["files"]))
        self.files["uboot.env.bak"] = stored(STOCK.replace("example=abc", "example=other"))
        with self.assertRaises(ValueError):
            self.check()
        self.files["uboot.env.bak"] = stored(TRIAL)
        with self.assertRaisesRegex(ValueError, "temporary"):
            self.check()

    def test_corrupt_crc_duplicate_key_padding_and_missing_bootargs_rejected(self):
        good = stored()
        bad_padding = bytearray(good)
        bad_padding[-1] = 1
        bad_padding[:4] = struct.pack("<I", zlib.crc32(bad_padding[4:]))
        missing = bytearray(good.replace(b"bootargs=", b"not_args=", 1))
        missing[:4] = struct.pack("<I", zlib.crc32(missing[4:]))
        for raw in (good[:-1], b"xxxx" + good[4:], stored(extra=(b"bootargs=other",)),
                    stored(extra=(b"invalid",)), bytes(bad_padding), bytes(missing)):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                recovery.stored_bootargs(raw)

    def test_partition_size_and_file_metadata_rejected(self):
        with self.assertRaises(ValueError):
            self.check(self.raw[:-1])
        for field, value in (("mode", stat.S_IFLNK | 0o777), ("links", 2), ("size", 64)):
            old = getattr(self, field)
            setattr(self, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.check()
            setattr(self, field, old)

    def fake_capture(self, *, wrong_path=False, writable=False, boot_change=False,
                     data_change=False, wrong_image=False):
        calls = []
        boot_reads, data_reads = 0, 0
        props = dict(zip(("ro.product.device", "ro.build.version.release", "ro.boot.slot_suffix",
                          "ro.bootloader", "sys.boot_completed"),
                         ("ladybird", "14", "_b", "01.01.250225", "1")))
        def command(argv, **kwargs):
            nonlocal boot_reads, data_reads
            self.assertEqual(argv[:3], ["/unused/adb", "-s", "fixture-device"])
            calls.append(argv[3:])
            if argv[3:] == ["exec-out", "cat", "/dev/block/by-name/uenv"]:
                data_reads += 1
                return self.raw if not data_change or data_reads == 1 else b"x" + self.raw[1:]
            self.assertEqual(argv[3], "shell")
            cmd = argv[4]
            if cmd.startswith("getprop "):
                return props[cmd.split()[1]]
            if cmd == "cat /proc/sys/kernel/random/boot_id":
                boot_reads += 1
                return ("12345678-1234-1234-1234-123456789ab" +
                        ("d" if boot_change and boot_reads > 1 else "c"))
            if cmd.startswith("readlink -f /dev/block/by-name/"):
                name = cmd.split(";")[0].rsplit("/", 1)[1]
                expected = ("uenv", "/dev/block/mmcblk0p3", recovery.PARTITION_SIZE) if name == "uenv" \
                    else ("boot_b", "/dev/block/mmcblk0p26", 78643200)
                self.assertIn(name, ("uenv", "boot_b"))
                path = "/dev/block/loop7" if wrong_path else expected[1]
                return path + "\n" + str(expected[2]) + "\n" + ("0" if writable else "1")
            if cmd == "sha256sum /dev/block/by-name/boot_b":
                return ("0" * 64 if wrong_image else recovery.STOCK_BOOT_SHA256) + "  /dev/block/by-name/boot_b"
            self.fail("Unexpected device command: " + cmd)
        original = recovery.check_partition
        with patch.object(recovery.subprocess, "check_output", side_effect=command), \
             patch.object(recovery, "check_partition",
                          side_effect=lambda raw, allowed: original(raw, allowed, self.reader)):
            result = recovery.verify_persisted(Path("/unused/adb"), "fixture-device", ALLOWED)
        self.assertEqual(data_reads, 2)
        return result

    def test_capture_reads_fixed_physical_storage_and_checks_again(self):
        result = self.fake_capture()
        self.assertTrue(result["persistentReadbackVerified"])
        self.assertTrue(result["originalBBootImageMatched"])
        self.assertFalse(result["deviceWritesIssued"])
        self.assertFalse(result["subsequentBootVerified"])
        self.assertFalse(result["coldPowerTested"])

    def test_loop_substitute_writable_device_drift_and_image_mismatch_rejected(self):
        for key in ("wrong_path", "writable", "boot_change", "data_change", "wrong_image"):
            with self.subTest(case=key), self.assertRaises(ValueError):
                self.fake_capture(**{key: True})

    def test_private_output_stays_outside_repo(self):
        with tempfile.TemporaryDirectory() as temp:
            alias = Path(temp) / "checkout"
            alias.symlink_to(ROOT, target_is_directory=True)
            for path in (ROOT / "private-recovery-fixture", alias / "private-recovery-fixture"):
                with self.assertRaisesRegex(ValueError, "outside the repository"):
                    recovery.private_output(path)
                self.assertFalse(path.exists())

    def test_profile_requires_exact_model_and_valid_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "profile.json"
            record = dict(device="freeguy", model="L75MA-SPL", bootloader="01.01.250225",
                          normalizedBootargsSha256=ALLOWED)
            path.write_text(json.dumps(record))
            self.assertEqual(recovery.read_profile(path), ALLOWED)
            for key, value in (("device", "ladybird"), ("bootloader", "old"),
                               ("normalizedBootargsSha256", ["invalid"])):
                path.write_text(json.dumps(dict(record, **{key: value})))
                with self.assertRaises(ValueError):
                    recovery.read_profile(path)


class ConsoleTransportTests(unittest.TestCase):
    def test_real_stream_handshake_chunking_and_embedded_prompt(self):
        # Local pseudo-terminal only: no USB device, bootloader or disk writes.
        master, slave = pty.openpty()
        tty.setraw(slave)
        os.set_blocking(master, False)
        self.addCleanup(os.close, slave)
        self.addCleanup(os.close, master)
        received, errors = [], []
        def peer():
            try:
                pending = bytearray()
                for reply in (b"\r\nfreeguy# ", b"version-body\r\nfreeguy# ",
                              b"bootargs=example=freeguy# more-data\r\nfreeguy# ",
                              b"\r\nfreeguy# "):
                    deadline = time.monotonic() + 5
                    while b"\r" not in pending:
                        if time.monotonic() > deadline:
                            raise AssertionError("No terminal command received")
                        if select.select([slave], [], [], .1)[0]:
                            pending.extend(os.read(slave, 4096))
                    command, _, remaining = pending.partition(b"\r")
                    received.append(bytes(command))
                    pending = bytearray(remaining)
                    if b"example=freeguy#" in reply:
                        cut = reply.index(b" more-data")
                        os.write(slave, reply[:cut])
                        time.sleep(.06)
                        os.write(slave, reply[cut:])
                    else:
                        os.write(slave, reply)
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=peer, daemon=True)
        with tempfile.TemporaryDirectory() as temp:
            console = recovery.Console.__new__(recovery.Console)
            console.fd, console.output = master, Path(temp)
            console.index, console.authorized_write = 0, None
            worker.start()
            console.handshake()
            self.assertIn(b"version-body", console.command("version"))
            reply = console.command("printenv bootargs")
            self.assertIn(b"more-data", reply)
            self.assertTrue(recovery.has_prompt(reply))
            console.authorized_write = recovery.encode_setenv(STOCK)
            console.command(console.authorized_write)
            worker.join(timeout=6)
            self.assertFalse(worker.is_alive())
        self.assertFalse(errors, errors)
        self.assertEqual(received, [b"", b"version", b"printenv bootargs",
                                    recovery.encode_setenv(STOCK).encode()])


if __name__ == "__main__":
    unittest.main()
