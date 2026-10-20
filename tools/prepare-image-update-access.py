#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare updater access in an already validated Google runtime.

No startup changes, image replacement, data initialization, or automatic reboot.
Interrupted preparation leaves a private journal; it is never retried blindly.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = "/data/local/tmp/codex-gtv-fileboot-v1"
LOADER = "/data/local/tmp/rd-root"
CANDIDATE = "/data/local/tmp/codex-gtv-independent-candidate-01"
UPDATE = "/data/local/codex-image-update-v1"
DIRECTORIES = [BASE, LOADER, CANDIDATE]
DATA = [BASE + "/" + n + ".img" for n in
        ("uenv", "uuenv", "persist", "tvcertificate", "userdata", "metadata", "cache")]
LABEL = "u:object_r:system_data_file:s0"
SHA = re.compile(r"[0-9a-f]{64}")
BOOT = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def need(ok, message):
    if not ok:
        raise ValueError(message)


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def save(directory, record):
    tmp = directory / "journal.next"
    with tmp.open("w") as out:
        json.dump(record, out, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    tmp.replace(directory / "journal.json")
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def metadata(value):
    fields = value.split()
    need(len(fields) == 8 and all(n.isdigit() for n in fields[:2] + fields[3:7])
         and re.fullmatch("[0-9a-f]+", fields[2])
         and re.fullmatch(r"u:object_r:[a-z0-9_]+:s0", fields[7]),
         "Malformed file metadata")
    dev, ino, mode, uid, gid, links, size = (
        int(fields[i], 16 if i == 2 else 10) for i in range(7))
    need(mode & 0o170000 in (0o100000, 0o40000), "Only files and directories are allowed")
    return dict(dev=dev, ino=ino, mode=mode, uid=uid, gid=gid,
                links=links, size=size, label=fields[7])


def desired(before, directory=False):
    need(all(type(before[k]) is int and before[k] >= 0
             for k in ("dev", "ino", "mode", "uid", "gid", "links", "size"))
         and re.fullmatch(r"u:object_r:[a-z0-9_]+:s0", before["label"]),
         "Invalid saved metadata")
    need(before["mode"] & 0o170000 == (0o40000 if directory else 0o100000),
         "Unexpected file type")
    if directory:
        need(before["uid"] in (0, 1000, 2000) and before["gid"] in (0, 1000, 2000)
             and before["mode"] & 0o7000 == 0,
             "Unexpected directory owner or special mode")
        return dict(before, mode=0o40750, uid=1000, gid=1000, label=LABEL)
    need(before["links"] == 1 and before["uid"] in (0, 1000, 2000)
         and before["gid"] == 1000 and before["mode"] & 0o7777 in (0o640, 0o750)
         and 4096 <= before["size"] <= 2 * 1024**3, "Unexpected image metadata")
    return dict(before, label=LABEL)


def same_metadata(actual, expected):
    if actual is None:
        return False
    # Adding a file changes its parent's size, but not its identity/access.
    keys = set(expected)
    if expected["mode"] & 0o170000 == 0o40000:
        keys -= {"size", "links"}
    return all(actual[k] == expected[k] for k in keys)


def artifact_metadata(actual, content, mode):
    return actual is not None and actual["mode"] == (0o100000 | mode) and (
        actual["uid"] == actual["gid"] == 1000 and actual["links"] == 1
        and actual["size"] == len(content) and actual["label"] == LABEL)


def fixed_files(plan):
    need(plan["directories"] == DIRECTORIES and len(plan["rows"]) == 4
         and plan["probe"] is False and re.fullmatch("[0-9a-f]{16}", plan["id"]),
         "Wrong updater layout")
    files = {}
    for i, row in enumerate(plan["rows"]):
        name = ("system.img", "vendor.img", "system_ext.img", "envdaily")[i]
        active = (BASE if i < 3 else LOADER) + "/" + name
        candidate = CANDIDATE + "/" + (name if i < 3 else "loader")
        need(row["active"] == active and row["candidate"] == candidate
             and row["backup"] == active + ".previous-" + plan["id"],
             "Unexpected image path")
        for key, digest in ((active, row["oldSha256"]), (candidate, row["newSha256"])):
            need(SHA.fullmatch(digest), "Invalid image hash")
            files[key] = digest
    return files


def probe_id(plan):
    rows = [(row["active"], row["oldSha256"], row["newSha256"]) for row in plan["rows"]]
    return hashlib.sha256(json.dumps([rows, True], separators=(",", ":")).encode()).hexdigest()[:16]


class Device:
    def __init__(self, serial, helper_hash):
        need(re.fullmatch(r"[A-Za-z0-9_.:-]+", serial) and not serial.startswith("-"),
             "Explicit ADB serial required")
        need(SHA.fullmatch(helper_hash), "Pin the accepted runtime helper SHA-256")
        self.adb = ["adb", "-s", serial]
        self.helper_hash = helper_hash
        self.bound = None
        self.host = None

    def shell(self, command, timeout=30):
        return subprocess.check_output(self.adb + ["shell", command], text=True,
                                       stderr=subprocess.PIPE, timeout=timeout).strip()

    def guard(self):
        values = self.shell("id -u; getprop ro.codex.fileboot; getprop ro.build.version.release; "
                            "getprop ro.boot.slot_suffix; getprop ro.bootloader; "
                            "getprop sys.boot_completed; cat /proc/sys/kernel/random/boot_id; "
                            "cat /dev/codex-watchdog.pid").splitlines()
        need(len(values) == 8 and values[:6] == ["0", "1", "14", "_b", "01.01.250225", "1"]
             and BOOT.fullmatch(values[6]) and values[7].isdigit(), "Accepted Google B runtime required")
        boot, pid = values[6:]
        need(self.shell("readlink /proc/" + pid + "/exe") == "/codex/helper", "Unexpected watchdog")
        need(self.shell("sha256sum /proc/" + pid + "/exe").split()[0] == self.helper_hash,
             "Runtime helper changed")
        host = "/proc/" + pid + "/root/fileboot-host"
        need(self.shell("stat -f -c %T " + host) == "f2fs", "Original data filesystem missing")
        mounts = self.shell("cat /proc/" + pid + "/mounts").splitlines()
        need(any(line.split()[:3] == ["/dev/block/mmcblk0p42", "/fileboot-host", "f2fs"]
                 for line in mounts), "Unexpected original data mount")
        need(self.shell("cat " + host + "/local/tmp/codex-gtv-fileboot-v1/marker")
             == "codex-freeguy-fileboot-v1", "Original image directory marker missing")
        state = self.shell("cat /data/local/codex-system-default-v1/state").splitlines()
        need(state[:3] == ["CTV_DEFAULT_V1", "GOOGLE", "HEALTHY"]
             and self.shell("cat /data/local/codex-system-default-v1/fault") == "OK",
             "Google is not healthy")
        # Hash the board identity locally; never print or copy its raw value.
        cid = self.shell("cat /sys/block/mmcblk0/device/cid")
        need(re.fullmatch("[0-9a-fA-F]{32}", cid), "Board binding unavailable")
        binding = dict(boot=boot, watchdog=pid,
                       board=hashlib.sha256(cid.lower().encode()).hexdigest())
        need(self.bound is None or binding == self.bound, "Boot, watchdog or board changed")
        self.bound, self.host = binding, host

    def path(self, path):
        need(path in DIRECTORIES + DATA + [UPDATE] or
             path.startswith(BASE + "/") or path.startswith(LOADER + "/")
             or path.startswith(CANDIDATE + "/") or path.startswith(UPDATE + "/"),
             "Path outside fixed layout")
        need(re.fullmatch(r"/data/local/[a-zA-Z0-9_./-]+", path)
             and ".." not in path.split("/"), "Invalid path")
        return self.host + path[len("/data"):]

    def stat(self, path):
        target = self.path(path)
        # The /proc root traversal is intentional. Every component below the
        # bound host mount is checked against symlink redirection.
        parts = path[len("/data/"):].split("/")
        prefix = self.host
        checks = []
        for part in parts:
            prefix += "/" + part
            checks.append("test ! -L " + prefix)
        query = " && ".join(checks) + " && "
        query += "if test -e " + target + "; then stat -c '%d %i %f %u %g %h %s' " + target
        query += "; ls -Zd " + target + " | cut -d ' ' -f 1; else echo ABSENT; fi"
        value = self.shell(query)
        return None if value == "ABSENT" else metadata(value)

    def digest(self, path):
        value = self.shell("sha256sum " + self.path(path), 300).split()[0]
        need(SHA.fullmatch(value), "Hash read failed")
        return value

    def set_metadata(self, path, values):
        p = self.path(path)
        command = "chown {uid}:{gid} {p} && chmod {mode:o} {p} && chcon {label} {p}".format(
            uid=values["uid"], gid=values["gid"], p=p,
            mode=values["mode"] & 0o7777, label=shlex.quote(values["label"]))
        self.shell(command)

    def create_directory(self):
        p = self.path(UPDATE)
        self.shell("mkdir -m 700 " + p + " && chown 1000:1000 " + p
                   + " && chcon " + LABEL + " " + p)

    def create_file(self, path, content, mode):
        p = self.path(path)
        # noclobber refuses an occupied destination. A partial file is retained
        # on interruption and must be inspected, never silently overwritten.
        subprocess.run(self.adb + ["shell", "umask 077; set -C; cat > " + p],
                       input=content, check=True, capture_output=True, timeout=60)
        self.shell("chown 1000:1000 " + p + " && chmod " + format(mode, "o") + " " + p
                   + " && chcon " + LABEL + " " + p + " && fsync " + p)

    def flush(self):
        self.shell("sync", 120)


def provision(device, plan, artifacts, directory, inspect_only=False):
    files = fixed_files(plan)
    permitted_artifacts = {UPDATE + "/" + hashlib.sha256(c).hexdigest() + ".dex"
                           for c, mode in artifacts.values() if mode == 0o600}
    fixture_values = {}
    for i, name in enumerate(("system.img", "vendor.img", "system_ext.img", "loader")):
        for parent, byte in (((BASE if i < 3 else LOADER), 65 + i), (CANDIDATE, 97 + i)):
            p = parent + "/.update-probe-" + name
            permitted_artifacts.add(p)
            fixture_values[p] = (bytes([byte]) * 4096, 0o750 if i == 3 else 0o640)
    need(artifacts and set(artifacts) <= permitted_artifacts, "Unexpected artifact destination")
    for p in set(artifacts) & set(fixture_values):
        need(artifacts[p] == fixture_values[p], "Unexpected probe contents")
    device.guard()
    before = {}
    for path in DIRECTORIES + list(files) + DATA:
        before[path] = device.stat(path)
        need(before[path] is not None, "Required file is absent: " + path)
    targets = {p: desired(before[p], p in DIRECTORIES) for p in DIRECTORIES + list(files)}
    for p, digest in files.items():
        need(device.digest(p) == digest, "Existing image hash differs: " + p)
    for row in plan["rows"]:
        need(device.stat(row["backup"]) is None, "Backup path already occupied")
    update = device.stat(UPDATE)
    need(update is None or (update["mode"] == 0o40700 and update["uid"] == update["gid"] == 1000
                           and update["label"] == LABEL), "Updater directory is not provisioned")
    for path, (content, mode) in artifacts.items():
        need(0 < len(content) <= 4 * 1024**2 and mode in (0o600, 0o640, 0o750),
             "Unexpected artifact size or mode")
        if "/.update-probe-" in path:
            need(device.stat(path + ".previous-" + probe_id(plan)) is None, "Probe backup occupied")
        current = device.stat(path)
        need(current is None or (artifact_metadata(current, content, mode)
             and device.digest(path) == hashlib.sha256(content).hexdigest()),
             "Artifact destination occupied or incomplete: " + path)
    record = dict(schema=1, binding=device.bound, plan=plan, before=before,
                  targets=targets, files=files, complete=False, inspectOnly=inspect_only,
                  preflightPassed=True, operations=[], partitionWrites=False, dataImageWrites=False)
    save(directory, record)
    if inspect_only:
        return record

    def perform(kind, path, operation):
        device.guard()
        row = dict(kind=kind, path=path, state="unconfirmed")
        record["operations"].append(row)
        save(directory, record)
        operation()
        row["state"] = "returned"
        save(directory, record)

    for path, target in targets.items():
        need(same_metadata(device.stat(path), before[path]), "Metadata changed since preflight")
        if target != before[path]:
            perform("metadata", path, lambda p=path, v=target: device.set_metadata(p, v))
            need(same_metadata(device.stat(path), target), "Metadata readback differs")
    if update is None:
        perform("create-directory", UPDATE, device.create_directory)
    updated = device.stat(UPDATE)
    need(updated is not None and updated["mode"] == 0o40700 and
         updated["uid"] == updated["gid"] == 1000 and updated["label"] == LABEL,
         "Updater directory readback differs")
    for path, (content, mode) in artifacts.items():
        if device.stat(path) is None:
            perform("create-file", path, lambda p=path, c=content, m=mode: device.create_file(p, c, m))
        need(artifact_metadata(device.stat(path), content, mode)
             and device.digest(path) == hashlib.sha256(content).hexdigest(), "Artifact readback differs")
    perform("flush", UPDATE, device.flush)
    for path, digest in files.items():
        need(device.digest(path) == digest, "Existing image contents changed")
    for path in DATA:
        need(device.stat(path) == before[path], "Personal-data file metadata changed")
    for path in targets:
        need(same_metadata(device.stat(path), targets[path]), "Final metadata changed")
    device.guard()
    record["complete"] = True
    record["after"] = {p: device.stat(p) for p in targets}
    save(directory, record)
    return record


def restore_metadata(device, record, directory):
    need(record.get("complete") is True and record.get("inspectOnly") is False,
         "Automatic restoration requires completed preparation; inspect interrupted work manually")
    need("restorePending" not in record, "Previous restoration is unconfirmed; inspect before retrying")
    files = fixed_files(record["plan"])
    need(files == record["files"] and set(record["targets"]) == set(DIRECTORIES + list(files)),
         "Restoration scope changed")
    device.bound = record["binding"]
    device.guard()
    for path in record["targets"]:
        need(desired(record["before"][path], path in DIRECTORIES) == record["targets"][path],
             "Invalid saved metadata")
        current = device.stat(path)
        need(any(same_metadata(current, m) for m in (record["before"][path], record["targets"][path])),
             "Metadata or inode changed; restoration refused")
    for path, digest in files.items():
        need(device.digest(path) == digest, "Image changed; cannot restore old access settings")
    for path in reversed(list(record["targets"])):
        device.guard()
        before, target = record["before"][path], record["targets"][path]
        need(desired(before, path in DIRECTORIES) == target, "Invalid saved metadata")
        if not same_metadata(device.stat(path), before):
            record["restorePending"] = path
            save(directory, record)
            device.set_metadata(path, before)
            need(same_metadata(device.stat(path), before), "Restored metadata differs")
    record["restorePending"] = "flush"
    save(directory, record)
    device.flush()
    device.guard()
    for path in record["targets"]:
        need(same_metadata(device.stat(path), record["before"][path]), "Final restoration differs")
    for path in DATA:
        need(device.stat(path) == record["before"][path], "Personal-data metadata changed")
    record.pop("restorePending", None)
    record["metadataRestored"] = True
    save(directory, record)
    # New disposable fixtures and content-addressed DEX remain. No deletes.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--helper-sha256", required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("inspect", "prepare"):
        p = sub.add_parser(name)
        p.add_argument("--build", type=Path, required=True)
        p.add_argument("--old-profile", type=Path, required=True)
        p.add_argument("--new-profile", type=Path, required=True)
        p.add_argument("--probe", action="store_true")
        p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("restore-metadata")
    p.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    runner = load("run-image-updater")
    device = Device(args.serial, args.helper_sha256)
    if args.action == "restore-metadata":
        runner.private_path(args.run)
        record = json.loads((args.run / "journal.json").read_text())
        restore_metadata(device, record, args.run)
        print("Previous directory and image metadata restored; new artifacts retained")
        return
    runner.private_path(args.output, new=True)
    builder = load("build-image-updater")
    old, new = builder.read_profile(args.old_profile), builder.read_profile(args.new_profile)
    plan = builder.plan(old, new, False)
    _, dex_hash = runner.checked_build(args.build, args.old_profile, args.new_profile, args.probe)
    artifacts = {UPDATE + "/" + dex_hash + ".dex": ((args.build / "dex/classes.dex").read_bytes(), 0o600)}
    if args.probe:
        probe = builder.plan(old, new, True)
        for i, row in enumerate(probe["rows"]):
            for key, byte in (("active", 65 + i), ("candidate", 97 + i)):
                artifacts[row[key]] = (bytes([byte]) * 4096, 0o750 if i == 3 else 0o640)
    args.output.mkdir(mode=0o700)
    record = provision(device, plan, artifacts, args.output, args.action == "inspect")
    print(json.dumps({"complete": record["complete"], "inspectOnly": record["inspectOnly"],
                      "preflightPassed": record["preflightPassed"],
                      "operations": len(record["operations"]), "deviceRebooted": False}))


if __name__ == "__main__":
    main()
