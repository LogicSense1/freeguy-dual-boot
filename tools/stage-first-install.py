#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Stage a new installation's ordinary files in stock HyperOS.

Never replaces an existing directory, writes a partition, changes startup,
executes the loader or reboots. Interrupted runs are not automatically resumed.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import shlex
import stat
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inventory, backup = load("plan-first-install"), load("backup-data")
need = inventory.need
PARENTS = ("/data", "/data/local", "/data/local/tmp", "/data/vendor")
q = shlex.quote


def save(folder, record):
    backup.save(folder / "journal.json", record)


def parse_metadata(text):
    values = text.split()
    need(len(values) == 7 and all(v.isdigit() for v in values[:2] + values[3:])
         and re.fullmatch("[0-9a-f]+", values[2]), "Invalid target metadata")
    return dict(zip(("dev", "ino", "mode", "uid", "gid", "links", "bytes"),
                    [int(v, 16 if i == 2 else 10) for i, v in enumerate(values)]))


def directory_identity(value):
    need(value is not None and stat.S_ISDIR(value["mode"]), "Expected a real directory")
    return {key: value[key] for key in ("dev", "ino", "mode", "uid", "gid")}


class Device:
    def __init__(self, adb, serial, plan):
        need(re.fullmatch("[A-Za-z0-9_.:-]+", serial) and not serial.startswith("-"),
             "Explicit ADB device required")
        self.adb = [str(adb), "-s", serial]
        self.plan = plan
        self.bound = None
        self.allowed = set(PARENTS) | set(plan["directories"]) | set(plan["mustBeAbsent"])
        self.allowed |= {r["target"] for r in plan["files"] + plan["markers"]}

    def shell(self, command, timeout=30):
        return subprocess.check_output(self.adb + ["shell", "-T", command], text=True,
                                       stderr=subprocess.PIPE, timeout=timeout).strip()

    def path(self, path):
        need(path in self.allowed, "Path is outside the fixed staging layout")
        return q(path)

    def guard(self):
        scheme = self.plan["deviceBindingScheme"]
        source = "emmc-cid" if scheme == "freeguy-emmc-cid-serial-v1" else "android-serial"
        props = self.shell("; ".join("getprop " + name for name in backup.PROPS) +
                           "; printf 'CTV_PROPERTIES_END\\n'").splitlines()
        need(len(props) == 8 and props[-1] == "CTV_PROPERTIES_END", "Incomplete stock properties")
        attached = self.shell('for p in /sys/block/loop*/loop/backing_file; do '
                              '[ -e "$p" ] || continue; cat "$p" || exit 1; done')
        need(not attached, "Backing images are attached")
        identity = backup.stock_identity(props[:-1], self.shell("cat /proc/1/mounts"),
            self.shell("cat /proc/sys/kernel/random/boot_id"),
            self.shell("cat /sys/block/mmcblk0/device/cid") if source == "emmc-cid" else None,
            self.shell("getprop ro.serialno"), source)
        need(self.shell("id -u") == "2000", "Use the stock ADB shell; no privilege escalation")
        need(identity["deviceBindingSha256"] == self.plan["deviceBinding"]
             and identity["deviceBindingScheme"] == scheme, "Current TV does not match the owner binding")
        if self.bound is None:
            self.bound = identity
        need(identity == self.bound, "TV rebooted or identity changed")
        return identity

    def metadata(self, path):
        p = self.path(path)
        # test -e treats a permission error like absence. Require an explicit
        # ENOENT diagnostic instead; never use a denied lookup to approve setup.
        value = self.shell("LC_ALL=C stat -c '%d %i %f %u %g %h %s' " + p +
                           " 2>&1; rc=$?; printf '\\nCTV_STAT_RC %s\\n' \"$rc\"")
        text, sep, code = value.rpartition("\nCTV_STAT_RC ")
        need(sep and code.isdigit(), "Missing stat completion code")
        text = text.strip()
        if code == "0":
            return parse_metadata(text)
        need("\n" not in text and text.startswith("stat:") and path in text
             and text.endswith(": No such file or directory"),
             "Target lookup failed; absence is not established")
        return None

    def searchable(self, path):
        need(self.shell("test -x " + self.path(path) + " && printf 'OK\\n'") == "OK",
             "Cannot establish directory search access")

    def free_bytes(self):
        value = self.shell("stat -f -c '%a %S' /data").split()
        need(len(value) == 2 and all(v.isdigit() for v in value), "Cannot read userdata free space")
        return int(value[0]) * int(value[1])

    def capabilities(self):
        paths = self.shell("command -v fsync; command -v stat; command -v sha256sum").splitlines()
        need(len(paths) == 3 and all(path.startswith("/") for path in paths),
             "Required stock file utilities unavailable")

    def mkdir(self, path):
        self.shell("umask 077; mkdir -m 700 " + self.path(path))

    def digest(self, path):
        fields = self.shell("sha256sum " + self.path(path), timeout=600).split()
        need(len(fields) == 2 and inventory.SHA.fullmatch(fields[0]) and fields[1] == path,
             "Invalid file readback digest")
        return fields[0]

    def fsync(self, path):
        self.shell("fsync " + self.path(path), timeout=60)

    def chmod_loader(self, path):
        need(path == inventory.LOADER + "/envdaily", "Only the new loader becomes executable")
        self.shell("chmod 700 " + self.path(path))

    def upload(self, path, stream, process_event):
        p = self.path(path)
        # No PTY or adb push: noclobber creates the final file exclusively.
        # A partial file remains in place if the transfer fails.
        command = ("set -eu; umask 077; printf 'CTV_STAGE_PID %s\\n' \"$$\"; "
                   "test ! -e " + p + "; test ! -L " + p + "; set -C; cat > " + p)
        process = subprocess.Popen(self.adb + ["shell", "-T", command],
                                   stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            process_event({"localAdbPid": process.pid, "remotePid": None, "finished": False})
            handshake = b""
            deadline = time.monotonic() + 15
            while b"\n" not in handshake:
                remaining = deadline - time.monotonic()
                need(remaining > 0 and len(handshake) < 128, "Upload handshake incomplete")
                ready, _, _ = select.select([process.stdout], [], [], remaining)
                need(ready, "Upload handshake timed out; remote state is unknown")
                data = os.read(process.stdout.fileno(), 128 - len(handshake))
                need(data, "Upload handshake ended early")
                handshake += data
            match = re.fullmatch(rb"CTV_STAGE_PID ([1-9][0-9]*)\r?\n", handshake)
            need(match is not None, "Unexpected upload handshake")
            process_event({"localAdbPid": process.pid, "remotePid": int(match[1]), "finished": False})
            out, err = process.communicate(timeout=1200)
            need(process.returncode == 0 and not out and not err, "Upload did not finish cleanly")
            process_event({"localAdbPid": process.pid, "remotePid": int(match[1]), "finished": True})
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
                # Killing the host client is not proof the remote shell ended.


def preflight(device, plan):
    bound = device.guard()
    device.capabilities()
    parents = {}
    for path in PARENTS:
        device.searchable(path)
        parents[path] = directory_identity(device.metadata(path))
    need(len({parents[path]["dev"] for path in PARENTS}) == 1, "Unexpected parent filesystem")
    for path in plan["mustBeAbsent"]:
        need(device.metadata(path) is None, "Existing installation or staging path; no overwrite")
    need(device.free_bytes() >= plan["totalBytes"] + plan["freeSpaceReserveBytes"],
         "Insufficient free space including reserve")
    return bound, parents


def verify_directories(device, expected):
    for path, value in expected.items():
        need(directory_identity(device.metadata(path)) == value, "Parent identity or permissions changed")


def verify_file(device, row, metadata=None):
    actual = device.metadata(row["target"])
    need(actual is not None and actual["mode"] == stat.S_IFREG | int(row["setupMode"], 8)
         and actual["uid"] == actual["gid"] == 2000 and actual["links"] == 1
         and actual["bytes"] == row["bytes"], "Staged file metadata mismatch")
    if metadata is not None:
        need(actual == metadata, "Staged file identity changed")
    need(device.digest(row["target"]) == row["sha256"], "Staged file hash mismatch")
    return actual


def stage(device, plan, folder, apply=False):
    bound, parents = preflight(device, plan)
    record = dict(mode="first-install-stage", identity=bound, plan=plan, complete=False,
                  applied=False, pending=None, operations=[], files=[], directories={},
                  parents=parents,
                  deviceWritesIssued=False, startupChanged=False, loaderExecuted=False, rebootIssued=False)
    save(folder, record)
    if not apply:
        record["inspectionComplete"] = True
        save(folder, record)
        return record
    expected = dict(parents)
    remaining = plan["totalBytes"]

    def operation(kind, target, action):
        device.guard()
        verify_directories(device, expected)
        record["pending"] = {"kind": kind, "target": target}
        record["deviceWritesIssued"] = True
        save(folder, record)
        action()
        device.guard()
        record["operations"].append(record["pending"])
        record["pending"] = None
        save(folder, record)

    try:
        for path in plan["directories"]:
            def create(path=path):
                need(device.metadata(path) is None, "New directory appeared")
                device.mkdir(path)
                metadata = directory_identity(device.metadata(path))
                need(metadata["mode"] == 0o40700 and metadata["uid"] == metadata["gid"] == 2000
                     and metadata["dev"] == parents["/data"]["dev"], "Wrong new directory metadata")
                device.fsync(str(Path(path).parent))
                expected[path] = metadata
                record["directories"][path] = metadata
            operation("mkdir", path, create)

        rows = list(plan["files"])
        for marker in plan["markers"]:
            content = marker["content"].encode()
            # Marker creation is last, after all payloads have passed readback.
            rows.append(dict(marker, bytes=len(content), sha256=hashlib.sha256(content).hexdigest()))
        for row in rows:
            path = row["target"]

            def upload(row=row, path=path):
                nonlocal remaining
                need(device.metadata(path) is None, "Target already exists")
                need(device.free_bytes() >= remaining + plan["freeSpaceReserveBytes"],
                     "Free space fell below the reserve")
                if "source" in row:
                    current = inventory.checked_file(
                        dict(path=row["source"], bytes=row["bytes"], sha256=row["sha256"]), Path("/"))
                    stream = os.fdopen(os.open(current["source"], os.O_RDONLY | os.O_NOFOLLOW), "rb")
                else:
                    # A local temporary marker file avoids shell interpolation.
                    import tempfile
                    stream = tempfile.TemporaryFile()
                    stream.write(row["content"].encode())
                    stream.seek(0)
                try:
                    def event(value):
                        record["pending"]["uploadProcess"] = value
                        save(folder, record)
                    device.upload(path, stream, event)
                finally:
                    stream.close()
                device.guard()
                verify_directories(device, expected)
                if row["setupMode"] == "0700":
                    # Check the uploaded bytes before making them executable.
                    verify_file(device, dict(row, setupMode="0600"))
                    device.chmod_loader(path)
                device.fsync(path)
                device.fsync(str(Path(path).parent))
                actual = verify_file(device, row)
                record["files"].append(dict(target=path, sha256=row["sha256"], metadata=actual))
                remaining -= row["bytes"] if "source" in row else 0
            operation("upload", path, upload)

        for row, result in zip(rows, record["files"]):
            device.guard()
            verify_directories(device, expected)
            verify_file(device, row, result["metadata"])
        need(device.metadata("/data/local/codex-system-default-v1") is None
             and device.metadata("/data/vendor/codex-switch-proxy-v1") is None,
             "Shared state or proxy appeared during initial staging")
        need(device.free_bytes() >= plan["freeSpaceReserveBytes"], "Final free-space reserve lost")
        device.guard()
        record.update(complete=True, applied=True, pending=None)
        save(folder, record)
        return record
    except BaseException as exc:
        record["failure"] = type(exc).__name__
        record["remoteProcessMayStillExist"] = record["pending"] is not None
        save(folder, record)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "stage"))
    parser.add_argument("--adb", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--known-device-binding", required=True)
    parser.add_argument("--known-device-binding-scheme", choices=inventory.BINDING_SCHEMES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    backup.private_output(args.output)
    need(not args.output.exists() and not args.output.is_symlink() and args.output.parent.is_dir(),
         "Use a new private journal directory")
    plan = inventory.plan(inventory.read_json(args.inputs), args.inputs.parent,
                          args.known_device_binding, args.known_device_binding_scheme)
    device = Device(args.adb, args.device, plan)
    args.output.mkdir(mode=0o700)
    result = stage(device, plan, args.output, apply=args.action == "stage")
    print(json.dumps({k: result[k] for k in
                      ("complete", "applied", "deviceWritesIssued", "startupChanged",
                       "loaderExecuted", "rebootIssued")}))


if __name__ == "__main__":
    main()
