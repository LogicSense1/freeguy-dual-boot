#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Issue one normal reset after a checked first-entry transaction and observe UART.

Does not arm an entry or write environments/firmware. Requires a stopped B
console and a completed arm journal. Repeated early boots are interrupted with
spaces; no input is sent while the observed kernel is running.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("first_console", ROOT / "tools/first-activation-console.py")
activation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activation)
recovery, need = activation.recovery, activation.need
MAX_LOG = 64 * 1024**2


class BootTrace:
    """Recognize unprefixed OEM markers; ignore timestamped Android lines."""
    def __init__(self):
        self.partial = b""
        self.discarding = False
        self.kernels = self.resets = self.hashes = 0
        self.unexpected = False
        self.phase = "waiting"
        self.caught = False

    def line(self, value):
        value = value.rstrip(b"\r")
        if value == b"SW Reset":
            self.resets += 1
            self.unexpected |= self.kernels > 0 or self.resets > 1
            self.phase = "early"
            self.caught = False
        elif value.startswith(b"HASH1_VERSION="):
            self.hashes += 1
            self.unexpected |= self.kernels > 0 or self.hashes > 1
            self.phase = "early"
            self.caught = False
        elif value.startswith(b"U-Boot2019.04"):
            # Version text alone is not enough to move a running kernel back
            # into the bootloader phase; it can occur in Android diagnostics.
            if self.phase == "early" and not value.startswith(b"U-Boot2019.04-g43964869 "):
                self.unexpected = True
        elif re.fullmatch(rb"Starting kernel[ .]*", value):
            self.kernels += 1
            self.unexpected |= self.kernels > 1
            self.phase = "kernel"
            self.caught = False
        elif re.fullmatch(rb"freeguy# *", value):
            self.caught = True
            self.phase = "console"

    def feed(self, raw):
        # Bound parser memory even when an arbitrary binary/long line appears.
        for piece in raw.splitlines(keepends=True):
            if not self.discarding:
                self.partial += piece
                if len(self.partial) > 8192:
                    self.partial = b""
                    self.discarding = True
            if piece.endswith(b"\n"):
                if not self.discarding:
                    self.line(self.partial.rstrip(b"\n"))
                self.partial = b""
                self.discarding = False
        if not self.discarding and re.fullmatch(rb"freeguy# *", self.partial):
            self.line(self.partial)

    def should_interrupt(self):
        return self.unexpected and self.phase == "early" and not self.caught

    def result(self):
        return dict(phase=self.phase, kernelEntries=self.kernels, resetMarkers=self.resets,
                    hashMarkers=self.hashes, unexpectedBoot=self.unexpected,
                    consoleCaught=self.caught, operationalBootVerified=False)


def check_arm(plan, arm, allowed):
    stock, target = activation.check_plan(plan, allowed)
    need(arm.get("mode") == "first-activation-console"
         and arm.get("phase") == "armed-not-booted" and arm.get("complete") is True
         and arm.get("pending") is None and "failure" not in arm
         and arm.get("planSha256") == activation.planner.verify.content_hash(plan)
         and arm.get("stockBootargs") == stock and arm.get("undoBootargs") == stock
         and arm.get("temporaryBootargs") == target,
         "Require the matching completed arm transaction")
    for key in ("inspectionComplete", "armAttempted", "ramEntryVerified",
                "saveAcknowledged", "ramAfterSaveVerified", "writeMayHaveOccurred", "saveMayHaveOccurred"):
        need(arm.get(key) is True, "Incomplete arm evidence")
    for key in ("rebootIssued", "subsequentBootVerified", "firmwareImagesWritten",
                "slotOrRetryWriteIssued", "needsRecoveryReview"):
        need(arm.get(key) is False, "Arm record requires review")
    return target


def claim_reset(arm_path, arm_hash):
    """Claim this original journal once, even if reset delivery becomes uncertain."""
    need(activation.planner.inventory.SHA.fullmatch(arm_hash), "Pin the arm journal")
    need(arm_path.is_file() and not arm_path.is_symlink()
         and hashlib.sha256(arm_path.read_bytes()).hexdigest() == arm_hash, "Arm journal changed")
    marker = arm_path.with_name(arm_path.name + ".first-boot-issued")
    raw = json.dumps(dict(armJournalSha256=arm_hash, resetMayBeIssued=True)).encode() + b"\n"
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(marker.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def start(console, plan, arm, allowed, arm_path, arm_hash, checkpoint):
    target = check_arm(plan, arm, allowed)
    claimed = arm_path.with_name(arm_path.name + ".first-boot-issued")
    if claimed.exists() or claimed.is_symlink():
        raise FileExistsError("This arm journal already has a reset claim; inspect it instead of retrying")
    record = dict(mode="first-entry-boot", complete=False, pending="fresh-console",
                  armJournalSha256=arm_hash, resetMayHaveBeenIssued=False, resetIssued=False,
                  environmentWritesIssued=False, firmwareWritesIssued=False,
                  operationalBootVerified=False)
    checkpoint(record.copy())
    console.handshake()
    replies = {command: console.command(command) for command in activation.IDENTITY_READS}
    recovery.validate_console(replies)
    for _ in range(2):
        need(recovery.extract_bootargs(console.command("printenv bootargs")) == target,
             "Armed entry changed; no reset")
    need(console.authorized_write is None, "Unexpected environment write authorization")
    record["pending"] = "claim-reset"
    checkpoint(record.copy())
    claim_reset(arm_path, arm_hash)
    record.update(pending="normal-reset", resetMayHaveBeenIssued=True)
    checkpoint(record.copy())
    # One ordinary bootloader reset. Never AVB, bootm, slot or retry commands.
    console.send(b"reset\r")
    record.update(pending="observe-boot", resetIssued=True)
    checkpoint(record.copy())
    return record


def observe(console, output, seconds, record, checkpoint):
    trace, total, spaces = BootTrace(), 0, 0
    began = time.monotonic()
    last_space = last_status = 0.0
    with (output / "boot-uart.bin").open("xb", buffering=0) as raw:
        while time.monotonic() - began < seconds:
            if select.select([console.fd], [], [], .04)[0]:
                data = os.read(console.fd, 65536)
                need(data, "UART connection closed")
                total += len(data)
                need(total <= MAX_LOG, "UART log bound exceeded")
                raw.write(data)
                trace.feed(data)
            now = time.monotonic()
            if trace.caught:
                break
            if trace.should_interrupt() and now - last_space >= .08:
                console.send(b" ")
                spaces += 1
                last_space = now
            if now - last_status >= 1:
                record.update(trace.result(), bytesObserved=total, spacesSent=spaces,
                              elapsedSeconds=round(now - began, 1))
                checkpoint(record.copy())
                last_status = now
        os.fsync(raw.fileno())
    record.update(trace.result(), bytesObserved=total, spacesSent=spaces, pending=None,
                  complete=True, observationFinished=True,
                  elapsedSeconds=round(time.monotonic() - began, 1),
                  outcome="console-stopped" if trace.caught else
                          "unexpected-boot-not-stopped" if trace.unexpected else
                          "kernel-observed" if trace.kernels == 1 else "no-kernel-observed")
    checkpoint(record.copy())
    return record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", required=True)
    for name in ("activation-plan", "armed-record", "recovery-profile", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--activation-plan-sha256", required=True)
    p.add_argument("--armed-record-sha256", required=True)
    p.add_argument("--seconds", type=int, default=180)
    a = p.parse_args()
    need(60 <= a.seconds <= 1200, "Observation must be 60 to 1200 seconds")
    os.umask(0o077)
    plan = activation.planner.pinned(a.activation_plan, a.activation_plan_sha256)
    arm = activation.planner.pinned(a.armed_record, a.armed_record_sha256)
    allowed = recovery.read_profile(a.recovery_profile)
    check_arm(plan, arm, allowed)
    recovery.private_output(a.output)
    private_record = a.armed_record.resolve()
    need(ROOT != private_record and ROOT not in private_record.parents,
         "The existing arm journal and its reset claim must stay outside the repository")
    claimed = a.armed_record.with_name(a.armed_record.name + ".first-boot-issued")
    need(not claimed.exists() and not claimed.is_symlink(),
         "This arm journal already has a reset claim; no serial connection opened")
    a.output.mkdir(mode=0o700)
    save = lambda r: activation.planner.verify.stager.save(a.output, r)
    console = recovery.Console(a.port, a.output)
    record = None
    try:
        record = start(console, plan, arm, allowed, a.armed_record, a.armed_record_sha256, save)
        result = observe(console, a.output, a.seconds, record, save)
        print(json.dumps({k: result[k] for k in
                          ("outcome", "kernelEntries", "consoleCaught", "resetIssued",
                           "operationalBootVerified")}))
    except BaseException as exc:
        # start() already journals intent before any reset. Do not replace that
        # evidence with a clean-looking failure if it did not return a record.
        if record is not None:
            record.update(complete=False, failure=type(exc).__name__)
            save(record)
        raise
    finally:
        console.close()


if __name__ == "__main__":
    main()
