#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Serial transaction ordering, command limits and interruption behavior."""
import copy
import importlib.util
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import threading
import time
import tty
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("transaction", ROOT / "tools/first-activation-console.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
STOCK = ("console=ttyS0 root=/dev/ram init=/init rw loglevel=0 "
         "androidboot.bootreason=cold lastblval=75 example=abc")
ALLOWED = [module.recovery.fingerprint(STOCK)]


def plan():
    value = module.planner.entry(STOCK, ALLOWED, STOCK, STOCK + " androidboot.extra=fixture")
    value.update(mode="first-activation-plan", deviceAccess=False, deviceWritesIssued=False,
                 activationAuthorized=False, liveConsoleVerified=False,
                 verificationBootId="12345678-1234-1234-1234-123456789abc")
    for key in ("setupLoaderSha256", "stagingContentSha256", "verificationContentSha256",
                "bootObservationSha256"):
        value[key] = "a" * 64
    return value


class Journal:
    def __init__(self, fail=None):
        self.records, self.count, self.fail = [], 0, fail

    def __call__(self, record):
        self.count += 1
        if self.fail == (self.count, "before"):
            raise OSError("checkpoint failed")
        self.records.append(copy.deepcopy(record))
        if self.fail == (self.count, "after"):
            raise OSError("checkpoint acknowledgement lost")


class FakeConsole:
    def __init__(self, journal, fail=None):
        self.journal, self.fail = journal, fail
        self.value, self.authorized_write = STOCK, None
        self.calls, self.handshakes, self.attempts = [], 0, 0
        self.write_reply, self.save_reply = b"freeguy#", b"done\r\nfreeguy#"
        self.corrupt_ram = False
        self.reads = {
            "version": b"U-Boot2019.04-g43964869 (Feb 25 2025)\r\nfreeguy#",
            "md.b 0x1c040480 1": b"1c040480: 21 \r\nfreeguy#",
            "md.b 0x1c040400 1": b"1c040400: 00 \r\nfreeguy#",
            "printenv makedata factory_mode devicestate":
                b"makedata=true\r\nfactory_mode=0\r\ndevicestate=lock\r\nfreeguy#",
        }

    def handshake(self):
        assert self.journal.records[-1]["pending"] == "fresh-console-prompt"
        self.handshakes += 1

    def command(self, command):
        assert self.journal.records[-1]["pending"] == command
        self.attempts += 1
        if self.fail == (self.attempts, "before"):
            raise TimeoutError("before simulated command")
        self.calls.append(command)
        if command in self.reads:
            reply = self.reads[command]
        elif command == "printenv bootargs":
            reply = ("bootargs=" + self.value + "\r\nfreeguy#").encode()
        elif command == self.authorized_write:
            assert command == module.recovery.encode_setenv(plan()["temporaryBootargs"])
            self.value = STOCK if self.corrupt_ram else plan()["temporaryBootargs"]
            reply = self.write_reply
        elif command == "saveenv":
            assert self.authorized_write is not None
            reply = self.save_reply
        else:
            raise AssertionError("Unexpected command")
        if self.fail == (self.attempts, "after"):
            raise TimeoutError("after simulated command")
        return reply


class TransactionTests(unittest.TestCase):
    def setup_transaction(self, journal_fail=None, console_fail=None):
        journal = Journal(journal_fail)
        console = FakeConsole(journal, console_fail)
        transaction = module.Transaction(console, plan(), ALLOWED, journal)
        return transaction, console, journal

    def test_inspect_has_no_write_authority_and_is_not_activation(self):
        t, c, j = self.setup_transaction()
        result = t.inspect()
        self.assertEqual(c.handshakes, 1)
        self.assertTrue(result["inspectionComplete"])
        self.assertFalse(result["armAttempted"])
        self.assertFalse(result["writeMayHaveOccurred"])
        self.assertTrue(all(cmd in module.recovery.READS for cmd in c.calls))
        self.assertIsNone(c.authorized_write)
        with self.assertRaises(ValueError):
            t.inspect()

    def test_single_set_save_and_readback_without_boot(self):
        t, c, j = self.setup_transaction()
        t.inspect()
        result = t.arm_after_preflight()
        writes = [cmd for cmd in c.calls if cmd not in module.recovery.READS]
        self.assertEqual(writes, [module.recovery.encode_setenv(plan()["temporaryBootargs"]), "saveenv"])
        self.assertEqual(result["phase"], "armed-not-booted")
        self.assertTrue(result["saveAcknowledged"])
        self.assertTrue(result["ramAfterSaveVerified"])
        for key in ("persistentReadbackVerified", "subsequentBootVerified", "rebootIssued",
                    "firmwareImagesWritten", "slotOrRetryWriteIssued"):
            self.assertFalse(result[key])
        self.assertIsNone(c.authorized_write)
        count = len(c.calls)
        with self.assertRaises(ValueError):
            t.arm_after_preflight()
        self.assertEqual(len(c.calls), count)

    def test_all_command_interruption_positions_stop_without_retry(self):
        t, c, j = self.setup_transaction()
        t.inspect(); t.arm_after_preflight()
        positions = c.attempts
        for index in range(1, positions + 1):
            for side in ("before", "after"):
                with self.subTest(index=index, side=side):
                    t, c, j = self.setup_transaction(console_fail=(index, side))
                    with self.assertRaises(TimeoutError):
                        t.inspect(); t.arm_after_preflight()
                    self.assertFalse(j.records[-1]["complete"])
                    self.assertEqual(c.attempts, index)
                    self.assertLessEqual(c.calls.count("saveenv"), 1)
                    self.assertIsNone(c.authorized_write)
                    with self.assertRaises(ValueError):
                        t.arm_after_preflight()
                    self.assertEqual(c.attempts, index)

    def test_all_journal_interruption_positions_block_further_commands(self):
        t, c, j = self.setup_transaction()
        t.inspect(); t.arm_after_preflight()
        count = j.count
        for index in range(1, count + 1):
            for side in ("before", "after"):
                with self.subTest(index=index, side=side):
                    journal = Journal((index, side))
                    console = FakeConsole(journal)
                    t = None
                    with self.assertRaises(OSError):
                        t = module.Transaction(console, plan(), ALLOWED, journal)
                        t.inspect(); t.arm_after_preflight()
                    after = len(console.calls)
                    if t is not None:
                        self.assertFalse(t.record["complete"])
                        with self.assertRaises(ValueError):
                            t.arm_after_preflight()
                    self.assertEqual(len(console.calls), after)
                    self.assertLessEqual(console.calls.count("saveenv"), 1)
                    self.assertIsNone(console.authorized_write)

    def test_plan_drift_or_unknown_undo_fails_before_console_access(self):
        for change in ({"stockBootargs": STOCK + " other=1"}, {"undoBootargs": "wrong"},
                       {"temporaryBootargs": STOCK}, {"reservedMarginBytes": 0},
                       {"appendedBytes": True}, {"conservativeTargetBytes": 1},
                       {"activationAuthorized": True}, {"setupLoaderSha256": ""},
                       {"verificationBootId": "invalid"}):
            journal, console = Journal(), FakeConsole(Journal())
            with self.subTest(change=change), self.assertRaises(ValueError):
                module.Transaction(console, dict(plan(), **change), ALLOWED, journal)
            self.assertEqual(console.handshakes, 0)
            self.assertEqual(console.calls, [])
            self.assertEqual(journal.records, [])

    def test_changed_stock_or_B_flags_before_write_refused(self):
        for key in ("bootargs", *module.IDENTITY_READS):
            t, c, j = self.setup_transaction()
            t.inspect()
            if key == "bootargs":
                c.value = STOCK.replace("lastblval=75", "lastblval=74")
            else:
                c.reads[key] = b"changed\r\nfreeguy#"
            with self.subTest(key=key), self.assertRaises(ValueError):
                t.arm_after_preflight()
            self.assertTrue(all(cmd in module.recovery.READS for cmd in c.calls))
            self.assertFalse(j.records[-1]["writeMayHaveOccurred"])

    def test_ram_error_does_not_save_and_save_error_does_not_retry(self):
        for mode in ("write-error", "bad-ram", "save-error", "missing-save-ack"):
            t, c, j = self.setup_transaction()
            t.inspect()
            if mode == "write-error": c.write_reply = b"ERROR\r\nfreeguy#"
            if mode == "bad-ram": c.corrupt_ram = True
            if mode == "save-error": c.save_reply = b"done\r\nwrite failed\r\nfreeguy#"
            if mode == "missing-save-ack": c.save_reply = b"freeguy#"
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                t.arm_after_preflight()
            expected = 0 if mode in ("write-error", "bad-ram") else 1
            self.assertEqual(c.calls.count("saveenv"), expected)
            self.assertTrue(j.records[-1]["needsRecoveryReview"])
            self.assertFalse(j.records[-1]["saveAcknowledged"])

    def test_change_between_set_and_save_is_not_persisted(self):
        t, c, j = self.setup_transaction()
        t.inspect()
        original = c.command
        def changed(command):
            reply = original(command)
            if command == c.authorized_write:
                c.reads["md.b 0x1c040400 1"] = b"1c040400: 01 \r\nfreeguy#"
            return reply
        c.command = changed
        with self.assertRaises(ValueError):
            t.arm_after_preflight()
        self.assertNotIn("saveenv", c.calls)
        self.assertTrue(j.records[-1]["needsRecoveryReview"])

    def test_after_save_drift_keeps_uncertainty_and_never_reboots(self):
        for mode in ("ram", "slot"):
            t, c, j = self.setup_transaction()
            t.inspect()
            original = c.command
            def changed(command):
                reply = original(command)
                if command == "saveenv":
                    if mode == "ram":
                        c.value = STOCK
                    else:
                        c.reads["md.b 0x1c040480 1"] = b"1c040480: 20 \r\nfreeguy#"
                return reply
            c.command = changed
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                t.arm_after_preflight()
            self.assertEqual(c.calls.count("saveenv"), 1)
            result = j.records[-1]
            self.assertTrue(result["saveAcknowledged"])
            self.assertTrue(result["needsRecoveryReview"])
            self.assertFalse(result["complete"])
            self.assertFalse(result["rebootIssued"])
            self.assertFalse(result["persistentReadbackVerified"])

    def test_cli_does_not_expose_arm_or_boot(self):
        script = str(ROOT / "tools/first-activation-console.py")
        result = subprocess.run([sys.executable, "-B", script, "--help"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0)
        self.assertIn("{inspect}", result.stdout)
        for action in ("arm", "boot", "restore"):
            result = subprocess.run([sys.executable, "-B", script, action],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid choice", result.stderr)

    def test_transaction_over_real_local_serial_stream(self):
        # Exercise Console's actual chunked transport against a local PTY.
        # No physical UART, TV, environment partition or reset is involved.
        master, slave = pty.openpty()
        tty.setraw(slave)
        os.set_blocking(master, False)
        self.addCleanup(os.close, slave)
        self.addCleanup(os.close, master)
        journal = Journal()
        peer_state = FakeConsole(journal)
        peer_state.authorized_write = module.recovery.encode_setenv(plan()["temporaryBootargs"])
        stop, failures, received = threading.Event(), [], []
        def peer():
            pending = bytearray()
            try:
                while not stop.is_set():
                    if not select.select([slave], [], [], .1)[0]:
                        continue
                    pending.extend(os.read(slave, 4096))
                    while b"\r" in pending:
                        raw, _, pending = pending.partition(b"\r")
                        command = raw.decode("ascii")
                        received.append(command)
                        response = b"\r\nfreeguy#" if command == "" else peer_state.command(command)
                        # Fragment output, including the final console prompt.
                        midpoint = len(response) // 2
                        os.write(slave, response[:midpoint])
                        time.sleep(.005)
                        os.write(slave, response[midpoint:])
            except BaseException as exc:
                failures.append(exc)
        worker = threading.Thread(target=peer, daemon=True)
        with tempfile.TemporaryDirectory() as temp:
            console = module.recovery.Console.__new__(module.recovery.Console)
            console.fd, console.output = master, Path(temp)
            console.index, console.authorized_write = 0, None
            transaction = module.Transaction(console, plan(), ALLOWED, journal)
            worker.start()
            try:
                transaction.inspect()
                result = transaction.arm_after_preflight()
            finally:
                stop.set()
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(received[0], "")
            self.assertEqual(received.count("saveenv"), 1)
            self.assertEqual(peer_state.value, plan()["temporaryBootargs"])
            self.assertEqual(result["phase"], "armed-not-booted")
            self.assertEqual(result["pending"], None)


if __name__ == "__main__":
    unittest.main()
