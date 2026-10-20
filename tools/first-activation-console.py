#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Stopped-console transaction for the exact first-install boot entry.

The CLI exposes inspection only. The write transaction is an integration
component: its caller must first establish backups, demonstrated same-device
serial undo, filesystem health and a fresh staging/probe check. Those gates
are not implemented by this module and must not be inferred from an inspection.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("activation_plan", ROOT / "tools/plan-first-activation.py")
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)
recovery, need = planner.recovery, planner.need
IDENTITY_READS = ("version", "md.b 0x1c040480 1", "md.b 0x1c040400 1",
                  "printenv makedata factory_mode devicestate")


def check_plan(plan, allowed):
    need(plan.get("mode") == "first-activation-plan"
         and all(plan.get(k) is False for k in
                 ("deviceAccess", "deviceWritesIssued", "activationAuthorized", "liveConsoleVerified"))
         and plan.get("exactUndoVerified") is True, "Expected an offline activation plan")
    stock, target = plan.get("stockBootargs"), plan.get("temporaryBootargs")
    need(recovery.restore_plan(stock, allowed) is None
         and recovery.restore_plan(target, allowed) == stock
         and plan.get("undoBootargs") == stock, "Plan has no exact supported undo")
    extra = plan.get("appendedBytes")
    need(type(extra) is int and 0 < extra < 2048
         and plan.get("measuredTargetBytes") == len(target) + extra
         and plan.get("conservativeTargetBytes") == len(target) + max(626, extra)
         and plan.get("reservedMarginBytes") == 64
         and plan["conservativeTargetBytes"] + 64 <= 2047, "Invalid command-line length accounting")
    for key in ("setupLoaderSha256", "stagingContentSha256", "verificationContentSha256",
                "observedKernelSha256", "bootObservationSha256"):
        need(isinstance(plan.get(key), str) and planner.inventory.SHA.fullmatch(plan[key]),
             "Missing evidence hash")
    need(isinstance(plan.get("verificationBootId"), str) and
         re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", plan["verificationBootId"]),
         "Missing verification boot identity")
    recovery.encode_setenv(stock)
    recovery.encode_setenv(target)
    return stock, target


class Transaction:
    """One inspection and at most one arm attempt; no retry, reset or boot call.

    `checkpoint` must durably store each supplied private record before returning.
    The CLI supplies the existing fsynced journal writer. A coordinator may call
    `arm_after_preflight` only after completing the prerequisites in this module's
    docstring. This class checks the serial transaction, not those prerequisites.
    """
    def __init__(self, console, plan, allowed, checkpoint):
        self.stock, self.target = check_plan(plan, allowed)
        need(console.authorized_write is None, "Console already has a write authorization")
        self.console, self.checkpoint = console, checkpoint
        self.inspected = self.attempted = self.failed = False
        self.record = dict(mode="first-activation-console", planSha256=planner.verify.content_hash(plan),
                           stockBootargs=self.stock, undoBootargs=self.stock,
                           temporaryBootargs=self.target, phase="created", complete=False,
                           pending=None, sequence=0, inspectionComplete=False,
                           armAttempted=False, writeMayHaveOccurred=False, saveMayHaveOccurred=False,
                           ramEntryVerified=False, saveAcknowledged=False, ramAfterSaveVerified=False,
                           persistentReadbackVerified=False, subsequentBootVerified=False,
                           rebootIssued=False, firmwareImagesWritten=False,
                           slotOrRetryWriteIssued=False, needsRecoveryReview=False)
        self.store()

    def store(self):
        self.checkpoint(copy.deepcopy(self.record))

    def command(self, command, phase):
        self.record.update(phase=phase, pending=command, sequence=self.record["sequence"] + 1)
        self.store()
        response = self.console.command(command)
        self.record["pending"] = None
        self.store()
        return response

    def guard(self, phase):
        responses = {cmd: self.command(cmd, phase) for cmd in IDENTITY_READS}
        recovery.validate_console(responses)

    def bootargs(self, phase):
        return recovery.extract_bootargs(self.command("printenv bootargs", phase))

    def failure(self, exc):
        self.failed = True
        self.record.update(complete=False, failure=type(exc).__name__,
                           needsRecoveryReview=self.record["writeMayHaveOccurred"])
        try:
            self.store()
        except BaseException:
            # A failed checkpoint cannot authorize another serial operation.
            # The last durable intent may be the only reliable state available.
            pass

    def inspect(self):
        need(not self.inspected and not self.failed and not self.attempted,
             "Transaction inspection is not repeatable")
        try:
            self.record.update(phase="handshake", pending="fresh-console-prompt")
            self.store()
            self.console.handshake()
            self.record["pending"] = None
            self.store()
            self.guard("inspect-original-B")
            need(self.bootargs("inspect-stock") == self.stock, "Stock arguments changed; regenerate plan")
            need(self.bootargs("recheck-stock") == self.stock, "Stock arguments changed during inspection")
            self.record.update(phase="inspected", inspectionComplete=True, complete=True)
            self.store()
            self.inspected = True
            return copy.deepcopy(self.record)
        except BaseException as exc:
            self.failure(exc)
            raise

    def arm_after_preflight(self):
        need(self.inspected and not self.failed and not self.attempted,
             "Require a successful inspection and an unused transaction")
        self.attempted = True
        try:
            self.record.update(armAttempted=True, complete=False)
            self.store()
            self.guard("pre-write-original-B")
            need(self.bootargs("pre-write-stock") == self.stock, "Stock arguments changed; no write")
            self.console.authorized_write = recovery.encode_setenv(self.target)
            self.record["writeMayHaveOccurred"] = True
            response = self.command(self.console.authorized_write, "set-entry")
            need(not re.search(rb"\b(?:error|failed|failure)\b", response, re.I),
                 "Entry command reported an error")
            need(self.bootargs("verify-ram-entry") == self.target, "RAM entry mismatch; not saved")
            self.record["ramEntryVerified"] = True
            self.store()
            self.guard("pre-save-original-B")
            need(self.bootargs("pre-save-entry") == self.target, "RAM entry changed; not saved")
            self.record["saveMayHaveOccurred"] = True
            response = self.command("saveenv", "save-entry")
            need(re.search(rb"(?:^|[\r\n])done(?:[\r\n]|$)", response, re.I)
                 and not re.search(rb"\b(?:error|failed|failure)\b", response, re.I),
                 "Save completion not confirmed")
            self.record["saveAcknowledged"] = True
            self.store()
            need(self.bootargs("verify-ram-after-save") == self.target, "RAM changed after save")
            self.record["ramAfterSaveVerified"] = True
            self.guard("post-save-original-B")
            self.record.update(phase="armed-not-booted", complete=True)
            self.store()
            return copy.deepcopy(self.record)
        except BaseException as exc:
            self.failure(exc)
            raise
        finally:
            self.console.authorized_write = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect",))
    parser.add_argument("--port", required=True)
    parser.add_argument("--activation-plan", type=Path, required=True)
    parser.add_argument("--activation-plan-sha256", required=True)
    parser.add_argument("--recovery-profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = planner.pinned(args.activation_plan, args.activation_plan_sha256)
    allowed = recovery.read_profile(args.recovery_profile)
    check_plan(plan, allowed)
    recovery.private_output(args.output)
    os.umask(0o077)
    args.output.mkdir(mode=0o700)
    console = recovery.Console(args.port, args.output)
    try:
        transaction = Transaction(console, plan, allowed,
                                  lambda record: planner.verify.stager.save(args.output, record))
        result = transaction.inspect()
        print(json.dumps({key: result[key] for key in
                          ("phase", "inspectionComplete", "armAttempted", "writeMayHaveOccurred",
                           "persistentReadbackVerified", "rebootIssued")}))
    finally:
        console.close()


if __name__ == "__main__":
    main()
