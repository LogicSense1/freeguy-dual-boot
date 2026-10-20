#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the published undo without saving temporary arguments or rebooting.

Requires retained prefix backups, a staged owner-capture loader, a completed
refusal probe and an interactive original-B console. A RAM change is followed
by the public restore transaction, which saves only the original arguments.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("activation", ROOT / "tools/activate-first-entry.py")
activation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activation)
recovery, need = activation.recovery, activation.need
save = activation.planner.verify.stager.backup.save


def exercise(plan, allowed, output, inputs, factory=None):
    """One attempt. A failure leaves both journals and sends no further command."""
    factory = factory or recovery.Console
    record = dict(mode="serial-undo-exercise", complete=False, phase="created",
                  writeMayHaveOccurred=False, temporaryArgumentsSaved=False,
                  stockSaveAcknowledged=False, rebootIssued=False,
                  subsequentBootVerified=False, persistentReadbackVerified=False)
    def checkpoint():
        save(output / "journal.json", copy.deepcopy(record))
    checkpoint()
    ram = output / "ram"
    ram.mkdir(mode=0o700)
    port = None
    try:
        port = factory(ram)
        transaction = activation.console_tool.Transaction(port, plan, allowed,
            lambda value: save(ram / "journal.json", value))
        transaction.inspect()
        inputs.unchanged()
        # Persist intent before authorizing the only temporary RAM write.
        record.update(phase="set-temporary-in-ram", writeMayHaveOccurred=True)
        checkpoint()
        transaction.record["writeMayHaveOccurred"] = True
        port.authorized_write = recovery.encode_setenv(transaction.target)
        response = transaction.command(port.authorized_write, "set-temporary-in-ram")
        need(not activation.re.search(rb"\b(?:error|failed|failure)\b", response, activation.re.I),
             "RAM command reported an error")
        port.authorized_write = None
        transaction.guard("check-B-after-ram-change")
        need(transaction.bootargs("check-temporary-in-ram") == transaction.target,
             "Temporary RAM readback mismatch; use verified recovery, do not save or reboot")
        record["phase"] = "temporary-in-ram"
        checkpoint()
        port.close()
        port = None

        # New console and counter preserve the exact public restore transcript
        # layout expected by the activation coordinator.
        restored = output / "restore"
        restored.mkdir(mode=0o700)
        record["phase"] = "restore-original"
        checkpoint()
        port = factory(restored)
        port.handshake()
        result = recovery.recover(port, allowed, write=True)
        need(result.get("environmentSaved") is True, "Restore did not exercise its save path")
        save(restored / "result.json", result)
        port.close()
        port = None
        file_records = {}
        for name in sorted(activation.UNDO_FILES):
            path = restored / name
            raw = path.read_bytes()
            file_records[name] = dict(path="restore/" + name, bytes=len(raw),
                                      sha256=hashlib.sha256(raw).hexdigest())
        manifest = dict(version=1, deviceBindingSha256=plan["deviceBindingSha256"],
                        deviceBindingScheme=plan["deviceBindingScheme"], files=file_records)
        activation.check_undo(manifest, output, activation.Inputs(),
            plan["deviceBindingSha256"], plan["deviceBindingScheme"], allowed, plan["stockBootargs"])
        save(output / "serial-undo.json", manifest)
        record.update(complete=True, phase="stock-restored-not-booted",
                      stockSaveAcknowledged=True, ramRestored=True)
        checkpoint()
        return record
    except BaseException as exc:
        record.update(complete=False, failure=type(exc).__name__)
        try:
            checkpoint()
        except BaseException:
            pass
        raise
    finally:
        if port is not None:
            port.authorized_write = None
            port.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("inspect", "exercise"))
    for name in ("preflight", "recovery-profile", "stock-boot", "capture-build", "loader-build", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--preflight-sha256", required=True)
    p.add_argument("--known-device-binding", required=True)
    p.add_argument("--known-device-binding-scheme", choices=activation.inventory.BINDING_SCHEMES,
                   required=True)
    p.add_argument("--port")
    a = p.parse_args()
    need((a.action == "exercise") == bool(a.port), "Only exercise takes a stopped serial port")
    os.umask(0o077)
    recovery.private_output(a.output)
    preflight = activation.planner.pinned(a.preflight, a.preflight_sha256)
    files = activation.Inputs()
    files.file(dict(path=str(a.preflight.resolve()), bytes=a.preflight.stat().st_size,
                    sha256=a.preflight_sha256), Path("/"), 2 * 1024**2)
    allowed = recovery.read_profile(a.recovery_profile)
    plan = activation.owner.plan(a.stock_boot, a.capture_build, a.loader_build,
                                 a.known_device_binding, a.known_device_binding_scheme)
    a.output.mkdir(mode=0o700)
    entry, report = activation.prepare(plan, preflight, a.preflight.parent, allowed,
                                       a.output, files, undo_required=False)
    report["preflightSha256"] = a.preflight_sha256
    save(a.output / "preflight.json", report)
    if a.action == "inspect":
        print(json.dumps(dict(preflightComplete=True, deviceAccess=False, rebootIssued=False)))
        return
    result = exercise(entry, allowed, a.output, files, lambda folder: recovery.Console(a.port, folder))
    print(json.dumps({k: result[k] for k in
                      ("complete", "phase", "temporaryArgumentsSaved", "stockSaveAcknowledged",
                       "rebootIssued", "subsequentBootVerified", "persistentReadbackVerified")}))


if __name__ == "__main__":
    main()
