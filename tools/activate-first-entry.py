#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check retained first-install evidence, then optionally arm the exact entry.

Uses original B and the existing serial transaction. Never reboots or flashes.
Evidence must be collected on the owner's TV in the documented order; local
hashes do not attest which physical television a serial adapter was attached to.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


console_tool = load("first-activation-console")
planner, recovery = console_tool.planner, console_tool.recovery
owner = load("stage-owner-capture")
need, inventory = planner.need, planner.inventory
PREFIX_BYTES = 9277440 * 512
UNDO_FILES = {"result.json", "handshake.bin"} | {f"{i:02}.bin" for i in range(10)}
sha = lambda b: hashlib.sha256(b).hexdigest()


class Inputs:
    """Hash opened local inputs and retain metadata for a final change check."""
    def __init__(self):
        self.stamps = {}

    @staticmethod
    def stamp(path):
        value = path.lstat()
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns

    def file(self, record, parent, limit=128 * 1024**2):
        checked = inventory.checked_file(record, parent, limit)
        path = Path(checked["source"])
        need(ROOT != path and ROOT not in path.parents, "Private evidence must stay outside this repository")
        self.stamps[path] = self.stamp(path)
        return path

    def json(self, record, parent):
        return inventory.read_json(self.file(record, parent, 2 * 1024**2))

    def unchanged(self):
        need(all(self.stamp(p) == stamp for p, stamp in self.stamps.items()),
             "A reviewed local input changed or disappeared")


def binding(value, expected, scheme):
    need(value.get("deviceBindingSha256") == expected
         and value.get("deviceBindingScheme") == scheme, "Evidence belongs to another owner device")


def check_backup(value, parent, files, expected, scheme):
    binding(value, expected, scheme)
    need(value.get("version") == 1 and type(value.get("prefixBytes")) is int
         and value["prefixBytes"] == PREFIX_BYTES, "Full original prefix through userdata boundary required")
    chunks = value.get("chunks")
    need(isinstance(chunks, list) and 1 <= len(chunks) <= 256, "Invalid prefix chunk inventory")
    end, seen = 0, set()
    for row in chunks:
        need(set(row) == {"offset", "file"} and type(row["offset"]) is int and row["offset"] == end,
             "Backup chunks overlap or leave a gap")
        file = row["file"]
        need(type(file.get("bytes")) is int and file["bytes"] > 0 and file["bytes"] % 512 == 0,
             "Backup chunks must be sector aligned")
        path = files.file(file, parent)
        need(path not in seen, "Repeated backup chunk")
        seen.add(path)
        end += file["bytes"]
        need(end <= PREFIX_BYTES, "Backup extends beyond the documented prefix")
    need(end == PREFIX_BYTES, "Backup does not cover the complete prefix")
    return dict(bytes=end, chunks=len(chunks), integrityChecked=True,
                includesUserdata=False, includesHardwareBootAreas=False, includesRpmb=False)


def check_undo(value, parent, files, expected, scheme, allowed, stock):
    binding(value, expected, scheme)
    need(value.get("version") == 1 and set(value.get("files", {})) == UNDO_FILES,
         "Require the complete public serial-restore transcript, including its result")
    raw = {name: files.file(record, parent, 2 * 1024**2).read_bytes()
           for name, record in value["files"].items()}
    for name, data in raw.items():
        if name != "result.json":
            need(recovery.has_prompt(data) and b"Starting kernel" not in data
                 and not re.search(rb"Unknown command|Command too long|Too many args", data),
                 "Incomplete or failed stopped-console transcript")
    recovery.validate_console(dict(zip(console_tool.IDENTITY_READS,
                                       (raw[f"{i:02}.bin"] for i in range(4)))))
    before = recovery.extract_bootargs(raw["04.bin"])
    need(recovery.extract_bootargs(raw["05.bin"]) == before, "Undo source changed during the test")
    target = recovery.restore_plan(before, allowed)
    need(target is not None and recovery.fingerprint(target) == recovery.fingerprint(stock),
         "The restore test did not exercise this stock profile's temporary entry")
    need(all(recovery.extract_bootargs(raw[name]) == target for name in ("07.bin", "09.bin")),
         "Undo RAM readback did not match the exact original")
    need(re.search(rb"(?:^|[\r\n])done(?:[\r\n]|$)", raw["08.bin"], re.I)
         and not re.search(rb"\b(?:error|failed|failure)\b", raw["06.bin"] + raw["08.bin"], re.I),
         "Restore save was not acknowledged")
    result = json.loads(raw["result.json"])
    need(result.get("environmentSaved") is True and result.get("restorationAvailable") is True
         and result.get("alreadyStock") is False and result.get("rebootIssued") is False
         and result.get("firmwareWritten") is False
         and result.get("beforeSha256") == sha(before.encode("ascii"))
         and result.get("restoredSha256") == sha(target.encode("ascii")),
         "Restore result disagrees with its transcript")
    return dict(transcriptChecked=True, restoreSaved=True, exactInverseChecked=True,
                physicalSerialAssociationAttested=False)


def filesystem_checks(plan, docker, container, output, files, run=subprocess.run):
    rows = [r for r in plan["files"] if r.get("filesystem") == "ext4"]
    if not rows:
        need(plan.get("mode") == "owner-capture-stage-plan", "Missing filesystem inventory")
        return []
    need(docker is not None and isinstance(container, str)
         and re.fullmatch("sha256:[0-9a-f]{64}", container), "Pin a reviewed local e2fsprogs image by ID")
    checks = []
    for number, row in enumerate(rows):
        source = Path(row["source"])
        need("," not in str(source) and "\n" not in str(source), "Unsupported container mount path")
        files.unchanged()
        log = output / f"filesystem-{number:02}.log"
        command = [str(docker), "run", "--rm", "--pull=never", "--network=none", "--read-only",
                   "--cap-drop=ALL", "--security-opt=no-new-privileges",
                   "--mount", "type=bind,source=" + str(source) + ",target=/input.img,readonly",
                   "--entrypoint", "e2fsck", container, "-fn", "/input.img"]
        with log.open("xb") as stream:
            result = run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=600, check=False)
            stream.flush()
            os.fsync(stream.fileno())
        need(result.returncode == 0, "Read-only filesystem check failed; inspect the private log")
        files.unchanged()
        checks.append(dict(target=row["target"], imageSha256=row["sha256"],
                           exitCode=0, logSha256=sha(log.read_bytes())))
    return checks


def prepare(plan, preflight, parent, allowed, output, files, docker=None, container=None, runner=subprocess.run):
    need(set(preflight) == {"version", "deviceBindingSha256", "deviceBindingScheme",
                           "staging", "verification", "bootObservation", "backup", "serialUndo"}
         and preflight["version"] == 1, "Unexpected preflight schema")
    expected, scheme = plan["deviceBinding"], plan["deviceBindingScheme"]
    binding(preflight, expected, scheme)
    staged = files.json(preflight["staging"], parent)
    checked = files.json(preflight["verification"], parent)
    observation = files.json(preflight["bootObservation"], parent)
    evidence = planner.evidence(plan, staged, checked)
    binding(observation, expected, scheme)
    need(observation.get("bootId") == evidence["verificationBootId"],
         "Require the same normal stock boot for observation and final probe")
    need(recovery.restore_plan(observation["observedBootargs"], allowed) is None,
         "Finish the earlier temporary boot before preparing a fresh entry")
    evidence.update(planner.entry(observation["stockBootargs"], allowed,
                                  observation["observedBootargs"], observation["kernelCmdline"]))
    evidence.update(mode="first-activation-plan",
                    purpose="owner-capture" if plan["mode"] == "owner-capture-stage-plan" else "google-setup",
                    deviceBindingSha256=expected, deviceBindingScheme=scheme,
                    deviceAccess=False, deviceWritesIssued=False, activationAuthorized=False,
                    liveConsoleVerified=False, filesystemHealthVerified=False,
                    bootObservationSha256=preflight["bootObservation"]["sha256"])
    console_tool.check_plan(evidence, allowed)
    backup_path = files.file(preflight["backup"], parent, 2 * 1024**2)
    undo_path = files.file(preflight["serialUndo"], parent, 2 * 1024**2)
    backup = check_backup(inventory.read_json(backup_path), backup_path.parent, files, expected, scheme)
    undo = check_undo(inventory.read_json(undo_path), undo_path.parent, files,
                      expected, scheme, allowed, evidence["stockBootargs"])
    # The inventory already hashed these build inputs. Check them again and
    # retain their identities across filesystem checks and console setup.
    for row in plan["files"]:
        files.file(dict(path=row["source"], bytes=row["bytes"], sha256=row["sha256"]),
                   Path("/"), inventory.LIMIT)
    checks = filesystem_checks(plan, docker, container, output, files, runner)
    files.unchanged()
    return evidence, dict(mode="first-entry-preflight", complete=True,
                          deviceBindingSha256=expected, deviceBindingScheme=scheme,
                          backup=backup, serialUndo=undo, filesystemChecks=checks,
                          stagingAndProbeJoined=True, deviceWritesIssued=False,
                          physicalDeviceAssociationAttested=False, operationalBootVerified=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("inspect", "arm"))
    p.add_argument("--preflight", type=Path, required=True)
    p.add_argument("--preflight-sha256", required=True)
    p.add_argument("--recovery-profile", type=Path, required=True)
    p.add_argument("--known-device-binding", required=True)
    p.add_argument("--known-device-binding-scheme", choices=inventory.BINDING_SCHEMES, required=True)
    p.add_argument("--inputs", type=Path)
    for name in ("stock-boot", "capture-build", "loader-build", "docker", "output"):
        p.add_argument("--" + name, type=Path, required=name == "output")
    p.add_argument("--filesystem-container")
    p.add_argument("--port")
    a = p.parse_args()
    need((a.action == "arm") == bool(a.port), "Only arm takes a verified stopped serial port")
    os.umask(0o077)
    recovery.private_output(a.output)
    preflight = planner.pinned(a.preflight, a.preflight_sha256)
    files = Inputs()
    files.file(dict(path=str(a.preflight.resolve()), bytes=a.preflight.stat().st_size,
                    sha256=a.preflight_sha256), Path("/"), 2 * 1024**2)
    allowed = recovery.read_profile(a.recovery_profile)
    if a.inputs:
        need(not any((a.stock_boot, a.capture_build, a.loader_build)), "Choose Google or owner-capture inputs")
        plan = inventory.plan(inventory.read_json(a.inputs), a.inputs.parent,
                              a.known_device_binding, a.known_device_binding_scheme)
    else:
        need(all((a.stock_boot, a.capture_build, a.loader_build)), "Supply the three owner-capture inputs")
        plan = owner.plan(a.stock_boot, a.capture_build, a.loader_build,
                          a.known_device_binding, a.known_device_binding_scheme)
    a.output.mkdir(mode=0o700)
    evidence, report = prepare(plan, preflight, a.preflight.parent, allowed, a.output, files,
                               a.docker, a.filesystem_container)
    report["preflightSha256"] = a.preflight_sha256
    plan_out = a.output / "plan"
    plan_out.mkdir(mode=0o700)
    planner.verify.stager.save(plan_out, evidence)
    planner.verify.stager.backup.save(a.output / "preflight.json", report)
    if a.action == "inspect":
        print(json.dumps(dict(preflightComplete=True, armAttempted=False, rebootIssued=False)))
        return
    files.unchanged()
    arm_out = a.output / "arm"
    arm_out.mkdir(mode=0o700)
    port = recovery.Console(a.port, arm_out)
    try:
        transaction = console_tool.Transaction(port, evidence, allowed,
            lambda record: planner.verify.stager.save(arm_out, record))
        transaction.inspect()
        files.unchanged()
        result = transaction.arm_after_preflight()
        print(json.dumps({k: result[k] for k in
                          ("phase", "armAttempted", "saveAcknowledged", "rebootIssued",
                           "persistentReadbackVerified", "subsequentBootVerified")}))
    finally:
        port.close()


if __name__ == "__main__":
    main()
