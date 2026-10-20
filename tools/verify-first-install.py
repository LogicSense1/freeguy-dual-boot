#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Recheck a completed first staging and optionally test the loader's PID guard.

No boot activation, environment writes, slot changes, file repair or reboot.
The optional probe runs the exact audited loader as UID 2000, never as PID 1.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stager", ROOT / "tools/stage-first-install.py")
stager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stager)
inventory, need = stager.inventory, stager.need


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def payload_rows(plan):
    rows = list(plan["files"])
    for marker in plan["markers"]:
        content = marker["content"].encode()
        rows.append(dict(marker, bytes=len(content), sha256=hashlib.sha256(content).hexdigest()))
    return rows


def check_journal(record, plan):
    need(record.get("mode") == "first-install-stage" and record.get("complete") is True
         and record.get("applied") is True and record.get("deviceWritesIssued") is True
         and record.get("pending") is None and "failure" not in record
         and all(record.get(k) is False for k in ("startupChanged", "loaderExecuted", "rebootIssued")),
         "Require a completed, unchanged staging record")
    need(record.get("plan") == plan, "Staging record does not match the recomputed input inventory")
    identity = record.get("identity", {})
    need(identity.get("deviceBindingSha256") == plan["deviceBinding"]
         and identity.get("deviceBindingScheme") == plan["deviceBindingScheme"],
         "Staging record is bound to another device")
    need(isinstance(record.get("parents"), dict) and set(record["parents"]) == set(stager.PARENTS),
         "Staging record lacks the original parent-directory identities")
    need(isinstance(record.get("directories"), dict)
         and set(record["directories"]) == set(plan["directories"]),
         "Incomplete directory record")
    rows = payload_rows(plan)
    files = record.get("files")
    need(isinstance(files, list) and len(files) == len(rows)
         and all(isinstance(row, dict) for row in files), "Incomplete staged-file record")
    for expected, actual in zip(rows, files):
        need(actual.get("target") == expected["target"] and actual.get("sha256") == expected["sha256"]
             and isinstance(actual.get("metadata"), dict), "Staged-file order or pin mismatch")
    return rows


def probe_audit(plan):
    loader = next(row for row in plan["files"] if row["kind"] == "setup-loader")
    audit = inventory.read_json(Path(loader["source"]).parent / "audit.json")
    need(audit.get("passed") is True and audit.get("sha256") == loader["sha256"],
         "Loader audit mismatch")
    cases = [case for case in audit["cases"] if case.get("case") == "not-pid1"]
    need(len(cases) == 1 and cases[0].get("end") == "unprivileged-exit"
         and cases[0].get("syscalls") == 2 and cases[0].get("environmentWrites") == 0,
         "Require exact getpid/exit-only audit evidence for a non-PID-1 execution")
    return loader


def probe_command(loader, boot):
    need(loader["target"] == inventory.LOADER + "/envdaily"
         and inventory.SHA.fullmatch(loader["sha256"])
         and re.fullmatch("[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot),
         "Invalid fixed probe identity")
    path = stager.q(loader["target"])
    # Check UID, boot and hash again in the shell immediately before execution.
    # The loader's audited first two syscalls are getpid and exit(90).
    return ("set -u; printf 'CTV_PROBE_PID %s\\n' \"$$\"; "
            "test \"$(id -u)\" = 2000 || exit 91; "
            "test \"$(cat /proc/sys/kernel/random/boot_id)\" = " + stager.q(boot) + " || exit 92; "
            "test ! -L " + path + " && test -f " + path + " || exit 93; "
            "test \"$(sha256sum " + path + ")\" = " +
            stager.q(loader["sha256"] + "  " + loader["target"]) + " || exit 94; "
            + path + "; rc=$?; printf 'CTV_PROBE_EXIT %s\\n' \"$rc\"; exit 0")


def run_probe(device, loader, boot, event):
    process = subprocess.Popen(device.adb + ["shell", "-T", probe_command(loader, boot)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        event(dict(localAdbPid=process.pid, remotePid=None, finished=False))
        handshake = b""
        deadline = time.monotonic() + 10
        while b"\n" not in handshake:
            remaining = deadline - time.monotonic()
            need(remaining > 0 and len(handshake) < 128, "Probe handshake incomplete")
            ready, _, _ = select.select([process.stdout], [], [], remaining)
            need(ready, "Probe handshake timed out; remote state is unknown")
            data = os.read(process.stdout.fileno(), 128 - len(handshake))
            need(data, "Probe handshake ended early")
            handshake += data
        first, remainder = handshake.split(b"\n", 1)
        match = re.fullmatch(rb"CTV_PROBE_PID ([1-9][0-9]*)\r?", first)
        need(match is not None, "Unexpected probe handshake")
        pid = int(match[1])
        need(pid > 1, "Refuse an init-process shell")
        event(dict(localAdbPid=process.pid, remotePid=pid, finished=False))
        out, err = process.communicate(timeout=10)
        event(dict(localAdbPid=process.pid, remotePid=pid, finished=True))
        need(process.returncode == 0 and not err and
             (remainder + out).replace(b"\r\n", b"\n") == b"CTV_PROBE_EXIT 90\n",
             "Loader did not return only the expected non-PID-1 refusal")
        return 90
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
            # Remote completion remains unknown after killing only the host client.


def verify(device, plan, staged, output, execute=False, runner=run_probe):
    rows = check_journal(staged, plan)
    loader = probe_audit(plan)
    record = dict(mode="first-install-verification", complete=False, probeRequested=execute,
                  probeStarted=False, probeExit=None, process=None, filesVerified=0,
                  setupLoaderSha256=loader["sha256"], pending=None,
                  identity=None, stagingBootId=staged["identity"]["bootId"],
                  startupChanged=False, rebootIssued=False,
                  bootActivationAuthorized=False, runtimeVerified=False, filesystemsVerified=False)
    record.update(stagingContentSha256=content_hash(staged), inventorySha256=content_hash(plan))
    stager.save(output, record)
    try:
        identity = device.guard()
        need(identity["deviceBindingSha256"] == plan["deviceBinding"]
             and identity["deviceBindingScheme"] == plan["deviceBindingScheme"],
             "Staging device changed")
        record["identity"] = identity
        stager.save(output, record)
        expected = dict(staged["parents"], **staged["directories"])
        stager.verify_directories(device, expected)
        for path in ("/data/local/codex-system-default-v1", "/data/vendor/codex-switch-proxy-v1"):
            need(device.metadata(path) is None, "Initial-install state changed")
        for row, saved in zip(rows, staged["files"]):
            device.guard()
            stager.verify_directories(device, expected)
            stager.verify_file(device, row, saved["metadata"])
            record["filesVerified"] += 1
            stager.save(output, record)
        need(device.free_bytes() >= plan["freeSpaceReserveBytes"], "Free-space reserve lost")
        if execute:
            record.update(probeStarted=True, pending="non-pid1-probe")
            stager.save(output, record)
            def event(value):
                record["process"] = value
                stager.save(output, record)
            record["probeExit"] = runner(device, loader, identity["bootId"], event)
            need(record["probeExit"] == 90, "Unexpected refusal code")
            need(isinstance(record["process"], dict)
                 and record["process"].get("finished") is True
                 and type(record["process"].get("remotePid")) is int
                 and record["process"]["remotePid"] > 1, "Probe completion is not established")
            saved = next(row for row in staged["files"] if row["target"] == loader["target"])
            stager.verify_file(device, loader, saved["metadata"])
        need(device.guard() == identity, "Boot changed during verification")
        stager.verify_directories(device, expected)
        record.update(complete=True, pending=None)
        stager.save(output, record)
        return record
    except BaseException as exc:
        record["failure"] = type(exc).__name__
        stager.save(output, record)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "probe"))
    parser.add_argument("--adb", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--known-device-binding", required=True)
    parser.add_argument("--known-device-binding-scheme", choices=inventory.BINDING_SCHEMES, required=True)
    parser.add_argument("--staging-record", type=Path, required=True)
    parser.add_argument("--staging-record-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    stager.backup.private_output(args.output)
    need(not args.output.exists() and not args.output.is_symlink() and args.output.parent.is_dir(),
         "Use a new private verification directory")
    need(inventory.SHA.fullmatch(args.staging_record_sha256), "Pin the staging record")
    staged = inventory.read_json(args.staging_record)
    raw = args.staging_record.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == args.staging_record_sha256
         and json.loads(raw) == staged, "Staging record hash mismatch")
    plan = inventory.plan(inventory.read_json(args.inputs), args.inputs.parent,
                          args.known_device_binding, args.known_device_binding_scheme)
    check_journal(staged, plan)
    probe_audit(plan)
    device = stager.Device(args.adb, args.device, plan)
    args.output.mkdir(mode=0o700)
    result = verify(device, plan, staged, args.output, execute=args.action == "probe")
    print(json.dumps({key: result[key] for key in
                      ("complete", "filesVerified", "probeStarted", "probeExit",
                       "startupChanged", "bootActivationAuthorized")}))


if __name__ == "__main__":
    main()
