#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One ordinary reset, journal consumption and fragmented UART phase handling."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import tempfile
import threading
import tty
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load("first_boot", "tools/boot-first-entry.py")
fixture = load("console_fixture", "tests/test-first-activation-console.py")
NORMAL = (b"SW Reset\r\nHASH1_VERSION=fixture\r\n"
          b"U-Boot2019.04-g43964869 (Feb 25 2025)\r\nStarting kernel ...\r\n")


class Console:
    authorized_write = None

    def __init__(self, target, journal):
        self.target = target
        self.journal = journal
        self.calls = []
        self.fail_send = False
        self.reads = fixture.FakeConsole(fixture.Journal()).reads

    def handshake(self):
        self.calls.append("handshake")

    def command(self, value):
        self.calls.append(value)
        if value == "printenv bootargs":
            return ("bootargs=" + self.target + "\r\nfreeguy#").encode()
        return self.reads[value]

    def send(self, raw):
        assert raw == b"reset\r"
        assert self.journal[-1]["pending"] == "normal-reset"
        assert self.journal[-1]["resetMayHaveBeenIssued"]
        self.calls.append(raw)
        if self.fail_send:
            raise OSError("delivery uncertain")


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        j = fixture.Journal()
        c = fixture.FakeConsole(j)
        transaction = fixture.module.Transaction(c, fixture.plan(), fixture.ALLOWED, j)
        transaction.inspect()
        self.arm = transaction.arm_after_preflight()
        self.plan = fixture.plan()
        self.record = self.root / "arm.json"
        self.record.write_text(json.dumps(self.arm))
        self.digest = hashlib.sha256(self.record.read_bytes()).hexdigest()
        self.journal = []
        self.console = Console(self.plan["temporaryBootargs"], self.journal)

    def start(self, save=None):
        return m.start(self.console, self.plan, self.arm, fixture.ALLOWED,
                       self.record, self.digest,
                       save or (lambda value: self.journal.append(copy.deepcopy(value))))

    def test_normal_boot_at_every_chunk_size_never_requests_input(self):
        for step in range(1, len(NORMAL) + 1):
            trace = m.BootTrace()
            for i in range(0, len(NORMAL), step):
                trace.feed(NORMAL[i:i + step])
                self.assertFalse(trace.should_interrupt())
            self.assertEqual(trace.kernels, 1)
            self.assertFalse(trace.caught)
            self.assertFalse(trace.result()["operationalBootVerified"])

    def test_repeated_early_boot_and_kernel_reboot_are_caught_only_before_kernel(self):
        for first in (NORMAL, b"SW Reset\r\nHASH1_VERSION=fixture\r\n"):
            trace = m.BootTrace()
            trace.feed(first)
            trace.feed(b"SW Reset\r\nHASH1_VERSION=fixture\r\n")
            self.assertTrue(trace.should_interrupt())
            trace.feed(b"Starting kernel ...\r\n")
            self.assertFalse(trace.should_interrupt())
            trace.feed(b"SW Reset\r\n")
            self.assertTrue(trace.should_interrupt())
            trace.feed(b"freeguy# ")
            self.assertTrue(trace.caught)
            self.assertFalse(trace.should_interrupt())

    def test_android_text_and_long_lines_do_not_trigger_early_input(self):
        trace = m.BootTrace()
        trace.feed(NORMAL)
        for text in (b"[ 12.3] SW Reset\r\n", b"INFO HASH1_VERSION=old\r\n",
                     b"U-Boot2019.04 old version text\r\n",
                     b"x" * 10000 + b"SW Reset\r\n"):
            trace.feed(text)
            self.assertFalse(trace.should_interrupt())
        self.assertLessEqual(len(trace.partial), 8192)

    def test_kernel_supersedes_stale_prompt_in_same_chunk(self):
        trace = m.BootTrace()
        trace.feed(b"freeguy#\r\n" + NORMAL)
        self.assertFalse(trace.caught)
        self.assertEqual(trace.phase, "kernel")

    def test_one_reset_claim_and_no_environment_commands(self):
        result = self.start()
        self.assertTrue(result["resetIssued"])
        self.assertFalse(result["environmentWritesIssued"])
        self.assertEqual(self.console.calls.count(b"reset\r"), 1)
        self.assertTrue(self.record.with_name("arm.json.first-boot-issued").is_file())
        with self.assertRaises(FileExistsError):
            self.start()
        self.assertEqual(self.console.calls.count(b"reset\r"), 1)
        self.assertTrue(all(c in m.recovery.READS or c in ("handshake", b"reset\r")
                            for c in self.console.calls))

    def test_changed_console_or_arm_evidence_never_resets(self):
        self.console.target = fixture.STOCK
        with self.assertRaises(ValueError):
            self.start()
        self.assertNotIn(b"reset\r", self.console.calls)
        self.assertFalse(self.record.with_name("arm.json.first-boot-issued").exists())
        for change in (dict(pending="saveenv"), dict(saveAcknowledged=False),
                       dict(phase="inspected"), dict(planSha256="0" * 64),
                       dict(needsRecoveryReview=True)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                m.check_arm(self.plan, dict(self.arm, **change), fixture.ALLOWED)

    def test_uncertain_reset_delivery_cannot_be_retried(self):
        self.console.fail_send = True
        with self.assertRaises(OSError):
            self.start()
        self.console.fail_send = False
        with self.assertRaises(FileExistsError):
            self.start()
        self.assertEqual(self.console.calls.count(b"reset\r"), 1)

    def test_checkpoint_failure_blocks_reset_and_preserves_claim(self):
        calls = [0]
        def save(value):
            calls[0] += 1
            if value["pending"] == "normal-reset":
                raise OSError("journal unavailable")
            self.journal.append(value)
        with self.assertRaises(OSError):
            self.start(save)
        self.assertNotIn(b"reset\r", self.console.calls)
        self.assertTrue(self.record.with_name("arm.json.first-boot-issued").exists())
        with self.assertRaises(FileExistsError):
            self.start()
        self.assertNotIn(b"reset\r", self.console.calls)

    def test_uart_observer_interrupts_repeat_and_stops_at_prompt(self):
        master, slave = os.openpty()
        tty.setraw(slave)
        os.set_blocking(slave, False)
        out = self.root / "uart"
        out.mkdir()
        sent, errors = [], []
        class Port:
            fd = slave
            def send(self, data):
                sent.append(data)
                os.write(slave, data)
        def television():
            try:
                os.write(master, NORMAL + b"SW Reset\r\nHASH1_VERSION=again\r\n")
                ready, _, _ = select.select([master], [], [], 2)
                if not ready or os.read(master, 64) != b" ":
                    raise AssertionError("Expected only a boot interruption space")
                os.write(master, b"\r\nfreeguy# ")
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=television, daemon=True)
        worker.start()
        try:
            result = m.observe(Port(), out, 3, {}, lambda value: None)
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertFalse(errors)
            self.assertEqual(result["outcome"], "console-stopped")
            self.assertTrue(result["unexpectedBoot"])
            self.assertFalse(result["operationalBootVerified"])
            self.assertTrue(sent and all(value == b" " for value in sent))
        finally:
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
