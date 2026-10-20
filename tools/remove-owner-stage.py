#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Remove only a completed capture's two staged files after normal HyperOS boot.

Requires the original staging journal, independently retained capture and exact
source build. Leaves captured data intact. No recursive removal or boot writes.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def load(name):
    s = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


owner, collector = load("stage-owner-capture"), load("collect-owner-capture")
stage, verify, need = owner.stage, owner.verify, owner.need
sha = lambda b: hashlib.sha256(b).hexdigest()


def captured(folder, digest, plan, allowed, cid):
    record = owner.inventory.read_json(folder / "manifest.json")
    need(sha((folder / "manifest.json").read_bytes()) == digest,
         "Independently retained capture manifest hash differs")
    need(record.get("complete") is True and record.get("sameBootCapture") is True
         and record.get("stockBVerified") is True and record.get("deviceWritesIssued") is False
         and "failure" not in record, "Complete read-only capture required")
    need(record.get("deviceBindingSha256") == plan["deviceBinding"]
         and record.get("deviceBindingScheme") == plan["deviceBindingScheme"],
         "Capture belongs to a different owner")
    need(re.fullmatch(collector.UUID, record.get("bootId", "")), "Capture boot missing")
    files = record.get("files", [])
    need(len(files) == 4 and [row.get("name") for row in files] == list(collector.SIZES),
         "Incomplete capture inventory")
    images = {}
    for row in files:
        name = row["name"]
        path = folder / name
        need(path.is_file() and not path.is_symlink()
             and path.stat().st_size == row.get("bytes") == collector.SIZES[name],
             "Capture file missing or changed")
        images[name] = path.read_bytes()
        need(sha(images[name]) == row["sha256"], "Capture hash differs")
    collector.validate_images(images, allowed, plan["deviceBinding"], plan["deviceBindingScheme"], cid)
    return record


class Device(stage.Device):
    def normal_start(self):
        words = self.shell("cat /proc/cmdline").split()
        need(words.count("root=/dev/ram") == words.count("init=/init") == words.count("rw") == 1
             and not any(w.startswith(("rdinit=", "rootfstype=", "rootflags=")) for w in words)
             and not any(w.startswith("init=") and w != "init=/init" for w in words)
             and not any(w.startswith("root=") and w != "root=/dev/ram" for w in words),
             "Reboot normally into HyperOS after capture before removing its entry")

    def directory_names(self):
        return self.shell("ls -A " + self.path(owner.inventory.LOADER)).splitlines()

    def unlink(self, path):
        need(path in (owner.inventory.LOADER + "/envdaily", owner.MARKER), "Unknown staged file")
        self.shell("rm " + self.path(path))

    def remove_directory(self):
        self.shell("rmdir " + self.path(owner.inventory.LOADER))


def remove(device, plan, staged, capture, output, apply=False):
    rows = verify.check_journal(staged, plan)
    need(len(rows) == 2, "Expected only the owner loader and marker")
    need(capture.get("complete") is True
         and capture.get("deviceBindingSha256") == plan["deviceBinding"]
         and capture.get("deviceBindingScheme") == plan["deviceBindingScheme"],
         "Validated same-owner capture required")
    identity = device.guard()
    need(identity["bootId"] != capture["bootId"], "Collect, then perform a normal stock reboot")
    device.normal_start()
    expected = dict(staged["parents"], **staged["directories"])
    wanted = {Path(row["target"]).name for row in rows}
    record = dict(mode="remove-owner-stage", complete=False, inspectOnly=not apply,
                  identity=identity, stagingSha256=verify.content_hash(staged),
                  pending=None, removed=[], capturedDataRemoved=False,
                  environmentWrites=False, firmwareWrites=False, rebootIssued=False)
    save = lambda: stage.save(output, record)
    save()

    def check():
        need(device.guard() == identity, "Device or boot changed")
        device.normal_start()
        stage.verify_directories(device, expected)
        for path in ("/data/local/codex-system-default-v1", "/data/vendor/codex-switch-proxy-v1"):
            need(device.metadata(path) is None, "Daily installation appeared")
        need(set(device.directory_names()) == wanted, "Extra, missing or changed staging entries")
        for row, saved in zip(rows, staged["files"]):
            if Path(row["target"]).name in wanted:
                stage.verify_file(device, row, saved["metadata"])

    try:
        check()
        if not apply:
            record["inspectionComplete"] = True
            save()
            return record
        for row in rows:
            path = row["target"]
            check()
            record["pending"] = dict(operation="unlink", path=path)
            save()
            device.unlink(path)
            need(device.metadata(path) is None, "File removal not confirmed")
            device.fsync(owner.inventory.LOADER)
            wanted.remove(Path(path).name)
            record["removed"].append(path)
            record["pending"] = None
            save()
        check()
        record["pending"] = dict(operation="rmdir", path=owner.inventory.LOADER)
        save()
        device.remove_directory()
        need(device.metadata(owner.inventory.LOADER) is None, "Directory removal not confirmed")
        device.fsync("/data/local/tmp")
        need(device.guard() == identity, "Boot changed during cleanup")
        record.update(pending=None, complete=True)
        save()
        return record
    except BaseException as exc:
        record["failure"] = type(exc).__name__
        save()
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("inspect", "remove"))
    for name in ("adb", "stock-boot", "capture-build", "loader-build", "staging-record",
                 "captured-data", "recovery-profile", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    for name in ("device", "known-device-binding", "staging-record-sha256", "capture-manifest-sha256"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--known-device-binding-scheme", choices=owner.inventory.BINDING_SCHEMES, required=True)
    a = p.parse_args()
    os.umask(0o077)
    stage.backup.private_output(a.output)
    need(not a.output.exists() and not a.output.is_symlink() and a.output.parent.is_dir(),
         "Use a new private cleanup journal")
    plan = owner.plan(a.stock_boot, a.capture_build, a.loader_build,
                      a.known_device_binding, a.known_device_binding_scheme)
    staged = owner.inventory.read_json(a.staging_record)
    need(sha(a.staging_record.read_bytes()) == a.staging_record_sha256, "Staging journal changed")
    device = Device(a.adb, a.device, plan)
    device.guard()
    cid = device.shell("cat /sys/block/mmcblk0/device/cid").lower() \
        if a.known_device_binding_scheme == "freeguy-emmc-cid-serial-v1" else None
    capture = captured(a.captured_data, a.capture_manifest_sha256, plan,
                       collector.recovery.read_profile(a.recovery_profile), cid)
    a.output.mkdir(mode=0o700)
    result = remove(device, plan, staged, capture, a.output, a.action == "remove")
    print(json.dumps({k: result[k] for k in
                      ("complete", "inspectOnly", "capturedDataRemoved", "environmentWrites", "rebootIssued")}))


if __name__ == "__main__":
    main()
