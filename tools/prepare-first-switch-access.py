#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare stock switch access from the bounded first-install Google runtime.

Only ordinary file metadata, a new proxy directory, two symlinks and a scratch
file are changed. No environment contents, partitions, shared default state,
APK installation or reboot. Existing proxy/shared directories are refused.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("first_stager", ROOT / "tools/stage-first-install.py")
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)
inventory, need, q = stage.inventory, stage.need, stage.q
BASE, LOADER = inventory.BASE, inventory.LOADER
PROXY = "/data/vendor/codex-switch-proxy-v1"
SHARED = "/data/local/codex-system-default-v1"
DIRECTORIES = (BASE, LOADER, BASE + "/voice-assets-v1")
PARENTS = ("/data", "/data/local", "/data/local/tmp", "/data/vendor")
LINKS = {PROXY + "/main": "/mnt/vendor/uenv/uboot.env",
         PROXY + "/backup": "/mnt/vendor/uenv/uboot.env.bak"}
SCRATCH = PROXY + "/roundtrip.bin"
SCRATCH_BYTES = b"A" * 65536
LABEL = "u:object_r:vendor_cusdata_file:s0"


def layout(plan):
    files = {row["target"]: row for row in plan["files"]
             if row["kind"] != "backing-image" or row["writable"] is False}
    data = [row["target"] for row in plan["files"]
            if row["kind"] == "backing-image" and row["writable"] is True]
    need(len(files) == 14 and len(data) == 7 and LOADER + "/envdaily" in files,
         "Unexpected first-install file layout")
    return files, data


def runtime_pins(inputs, parent):
    path = parent / inputs["setupRoot"]["path"]
    raw = path.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == inputs["setupRoot"]["sha256"], "Setup root changed")
    rows = inventory.runtime.archive.entries(gzip.decompress(raw))
    contents = {row["name"]: row["data"] for row in rows}
    return {name: hashlib.sha256(contents["codex/" + name]).hexdigest()
            for name in ("helper", "entry.sh")}


def same(actual, expected):
    if actual is None:
        return False
    keys = set(expected)
    if stat.S_ISDIR(expected["mode"]):
        keys -= {"bytes", "links"}
    return all(actual[k] == expected[k] for k in keys)


class Device(stage.Device):
    def __init__(self, adb, serial, plan, pins):
        super().__init__(adb, serial, plan)
        self.pins, self.host = pins, None
        self.allowed |= set(DIRECTORIES + PARENTS) | {PROXY, SHARED, SCRATCH} | set(LINKS)

    def guard(self):
        values = self.shell("id -u; getprop ro.codex.fileboot; getprop ro.build.version.release; "
                            "getprop ro.boot.slot_suffix; getprop ro.bootloader; "
                            "getprop sys.boot_completed; cat /proc/sys/kernel/random/boot_id; "
                            "cat /dev/codex-watchdog.pid").splitlines()
        need(len(values) == 8 and values[:6] == ["0", "1", "14", "_b", "01.01.250225", "1"]
             and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", values[6])
             and values[7].isdigit() and int(values[7]) > 1, "First-install Google on original B required")
        need(self.shell("cat /dev/codex-first-install-v1") == "first-install-v1"
             and self.shell("test ! -e /dev/codex-daily-v1 && test ! -L /dev/codex-daily-v1 && echo OK") == "OK",
             "Refuse an installed daily runtime")
        boot, pid = values[6:]
        need(self.shell("readlink /proc/" + pid + "/exe") == "/codex/helper",
             "Unexpected startup watchdog")
        need(self.shell("tr '\\000' '\\n' < /proc/" + pid + "/cmdline").splitlines()
             == ["/codex/helper", "watchdog"], "Bounded first-install watchdog required")
        for name in ("helper", "entry.sh"):
            path = "/proc/" + pid + ("/exe" if name == "helper" else "/root/codex/entry.sh")
            need(self.shell("sha256sum " + path).split()[0] == self.pins[name], "Runtime code changed")
        uptime = self.shell("cat /proc/uptime").split()
        need(len(uptime) == 2 and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", uptime[0])
             and float(uptime[0]) < 2400, "Too little time remains in the bounded setup session")
        host = "/proc/" + pid + "/root/fileboot-host"
        mounts = self.shell("cat /proc/" + pid + "/mounts").splitlines()
        need(any(row.split()[:3] == ["/dev/block/mmcblk0p42", "/fileboot-host", "f2fs"]
                 and "rw" in row.split()[3].split(",") for row in mounts), "Original data mount missing")
        serial = self.shell("getprop ro.serialno")
        need(8 <= len(serial) <= 128 and len(set(serial)) > 1
             and all(33 <= ord(c) < 127 for c in serial)
             and serial.lower() not in ("unknown", "0123456789abcdef"), "Device serial unavailable")
        scheme = self.plan["deviceBindingScheme"]
        if scheme == "freeguy-emmc-cid-serial-v1":
            cid = self.shell("cat /sys/block/mmcblk0/device/cid")
            need(re.fullmatch("[0-9a-fA-F]{32}", cid), "Device CID unavailable")
            material = "freeguy\0" + cid.lower() + "\0" + serial
        else:
            need(scheme == "freeguy-android-serial-v1", "Unknown device-binding scheme")
            material = "freeguy-android-serial-v1\0" + serial
        need(hashlib.sha256(material.encode()).hexdigest() == self.plan["deviceBinding"],
             "Owner-device binding differs")
        binding = dict(bootId=boot, watchdog=pid, deviceBinding=self.plan["deviceBinding"], scheme=scheme)
        need(self.bound is None or self.bound == binding, "Boot, watchdog or device changed")
        self.bound, self.host = binding, host
        return binding

    def rawpath(self, path):
        need(self.host is not None and path in self.allowed and path.startswith("/data"),
             "Path outside the fixed original-data layout")
        return self.host + path[len("/data"):]

    def path(self, path):
        return q(self.rawpath(path))

    def digest(self, path):
        need(path in self.files or path == SCRATCH, "Only prepared immutable files may be hashed")
        fields = self.shell("sha256sum " + self.path(path), timeout=600).split()
        need(len(fields) == 2 and inventory.SHA.fullmatch(fields[0])
             and fields[1] == self.rawpath(path), "Invalid original-data readback digest")
        return fields[0]

    def metadata(self, path):
        raw = self.rawpath(path)
        # Only the two new endpoint symlinks may be symlinks. Never traverse
        # one while setting metadata or reading a file.
        pieces = path[len("/data"):].strip("/").split("/") if path != "/data" else []
        prefix, checks = self.host, []
        for piece in pieces[:-1]:
            prefix += "/" + piece
            checks.append("test ! -L " + q(prefix))
        if checks:
            need(self.shell(" && ".join(checks) + " && echo OK") == "OK", "Redirected parent")
        text = self.shell("LC_ALL=C stat -c '%d %i %f %u %g %h %s' " + q(raw) +
                          " 2>&1; rc=$?; printf '\\nCTV_STAT_RC %s\\n' \"$rc\"")
        body, sep, code = text.rpartition("\nCTV_STAT_RC ")
        need(sep and code.isdigit(), "Missing metadata completion")
        if code != "0":
            need("\n" not in body and body.startswith("stat:") and raw in body
                 and body.endswith(": No such file or directory"), "Absence is not established")
            return None
        value = stage.parse_metadata(body)
        label = self.shell("ls -Zd " + q(raw) + " | cut -d ' ' -f 1")
        need(re.fullmatch(r"u:object_r:[a-z0-9_]+:s0", label), "Invalid file label")
        value["label"] = label
        return value

    def set_metadata(self, path, value):
        need(path in DIRECTORIES or path in self.files or path in (PROXY, SCRATCH),
             "Metadata write outside prepared files")
        self.shell("chown " + str(value["uid"]) + ":" + str(value["gid"]) + " " + self.path(path)
                   + " && chmod " + format(value["mode"] & 0o7777, "o") + " " + self.path(path)
                   + " && chcon " + q(value["label"]) + " " + self.path(path))

    def create_proxy(self):
        self.shell("mkdir -m 700 " + self.path(PROXY))

    def create_link(self, path):
        need(path in LINKS, "Unexpected proxy link")
        self.shell("ln -s " + q(LINKS[path]) + " " + self.path(path)
                   + " && chown -h 0:0 " + self.path(path)
                   + " && chcon -h " + LABEL + " " + self.path(path))

    def readlink(self, path):
        need(path in LINKS, "Unexpected link read")
        return self.shell("readlink " + self.path(path))


def prepare(device, plan, output, apply=False):
    files, data = layout(plan)
    device.files = files
    binding = device.guard()
    help_text = device.shell("chcon --help 2>&1")
    need("-h" in help_text and "symlink" in help_text.lower(), "No no-follow label operation")
    before = {path: device.metadata(path) for path in PARENTS + DIRECTORIES + tuple(files) + tuple(data)}
    need(all(value is not None for value in before.values()), "Missing original-data path")
    need(len({value["dev"] for value in before.values()}) == 1, "Unexpected filesystem")
    for path in PARENTS + DIRECTORIES:
        need(stat.S_ISDIR(before[path]["mode"]), "Directory redirected or wrong type")
    need(device.metadata(PROXY) is None and device.metadata(SHARED) is None,
         "Existing proxy or shared default state; no overwrite")
    targets = {}
    for path in DIRECTORIES + tuple(files):
        prior = before[path]
        directory = path in DIRECTORIES
        need(prior["uid"] == prior["gid"] == 2000 and
             prior["mode"] == (0o40700 if directory else 0o100700 if path == LOADER + "/envdaily" else 0o100600),
             "Initial staging access metadata changed")
        if not directory:
            need(prior["links"] == 1 and prior["bytes"] == files[path]["bytes"]
                 and device.digest(path) == files[path]["sha256"], "Immutable file changed")
        targets[path] = dict(prior, gid=1000, mode=0o40750 if directory else
                             0o100750 if path == LOADER + "/envdaily" else 0o100640, label=LABEL)
    for path in data:
        value = before[path]
        need(value["mode"] == 0o100600 and value["uid"] == value["gid"] == 2000
             and value["links"] == 1, "Unexpected personal-data metadata")
    record = dict(mode="first-switch-access", complete=False, inspectOnly=not apply, binding=binding,
                  inventorySha256=hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest(),
                  before=before, targets=targets, pending=None, operations=[], uploadProcess=None,
                  environmentWrites=False, firmwareWrites=False, mutableImageWrites=False,
                  sharedStateCreated=False, automaticDefaultEnabled=False, rebootIssued=False)
    save = lambda: stage.save(output, record)
    def verify_originals():
        need(device.guard() == binding, "Runtime changed")
        for path in PARENTS + tuple(data):
            need(same(device.metadata(path), before[path]), "Original parent or data metadata changed")
        need(device.metadata(SHARED) is None, "Shared state appeared during setup")

    verify_originals()
    for path in DIRECTORIES + tuple(files):
        need(same(device.metadata(path), before[path]), "Staged metadata changed during inspection")
    need(device.metadata(PROXY) is None, "Proxy appeared during inspection")
    save()
    if not apply:
        record["inspectionComplete"] = True
        save()
        return record

    def operation(kind, path, action):
        verify_originals()
        record["pending"] = dict(kind=kind, path=path)
        save()
        action()
        record["operations"].append(record["pending"])
        record["pending"] = None
        save()

    try:
        operation("create-proxy", PROXY, device.create_proxy)
        proxy = device.metadata(PROXY)
        need(proxy is not None and proxy["mode"] == 0o40700 and proxy["uid"] == 0,
             "Unexpected new proxy")
        proxy_target = dict(proxy, mode=0o40750, uid=0, gid=1000, label=LABEL)
        operation("proxy-access", PROXY, lambda: device.set_metadata(PROXY, proxy_target))
        need(same(device.metadata(PROXY), proxy_target), "Proxy metadata readback differs")
        link_records = {}
        for path in LINKS:
            need(device.metadata(path) is None, "Proxy endpoint occupied")
            operation("create-link", path, lambda p=path: device.create_link(p))
            link = device.metadata(path)
            need(link is not None and link["mode"] == 0o120777 and link["uid"] == link["gid"] == 0
                 and link["dev"] == proxy["dev"] and link["links"] == 1
                 and link["label"] == LABEL and device.readlink(path) == LINKS[path],
                 "Proxy link readback differs")
            link_records[path] = link
        def upload():
            def event(value):
                record["uploadProcess"] = value
                save()
            with tempfile.TemporaryFile() as source:
                source.write(SCRATCH_BYTES); source.seek(0)
                device.upload(SCRATCH, source, event)
            need(record["uploadProcess"].get("finished") is True, "Scratch upload is unconfirmed")
        need(device.metadata(SCRATCH) is None, "Scratch path occupied")
        operation("scratch-upload", SCRATCH, upload)
        scratch = device.metadata(SCRATCH)
        need(scratch is not None and scratch["mode"] == 0o100600 and scratch["uid"] == 0
             and scratch["links"] == 1 and scratch["bytes"] == len(SCRATCH_BYTES), "Invalid scratch file")
        scratch_target = dict(scratch, uid=0, gid=1000, mode=0o100640, label=LABEL)
        operation("scratch-access", SCRATCH, lambda: device.set_metadata(SCRATCH, scratch_target))
        for path, target in targets.items():
            need(same(device.metadata(path), before[path]), "Code metadata changed before access update")
            operation("code-access", path, lambda p=path, v=target: device.set_metadata(p, v))
            need(same(device.metadata(path), target), "Code metadata readback differs")
        operation("flush", PROXY, lambda: device.shell("sync", 120))
        verify_originals()
        for path, row in files.items():
            need(device.digest(path) == row["sha256"], "Immutable content changed")
        for path, target in dict(targets, **{PROXY: proxy_target, SCRATCH: scratch_target}).items():
            need(same(device.metadata(path), target), "Final metadata differs")
        need(device.digest(SCRATCH) == hashlib.sha256(SCRATCH_BYTES).hexdigest(), "Scratch data differs")
        for path, target in LINKS.items():
            link = device.metadata(path)
            need(same(link, link_records[path]) and device.readlink(path) == target,
                 "Final proxy link differs")
        record.update(complete=True, mutableMetadataUnchanged=True, stockServiceAccessVerified=False)
        save()
        return record
    except BaseException as exc:
        record.update(complete=False, failure=type(exc).__name__)
        try: save()
        except BaseException: pass
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "prepare"))
    parser.add_argument("--adb", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--known-device-binding", required=True)
    parser.add_argument("--known-device-binding-scheme", choices=inventory.BINDING_SCHEMES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stage.backup.private_output(args.output)
    need(not args.output.exists() and not args.output.is_symlink() and args.output.parent.is_dir(),
         "Use a new private record directory")
    inputs = inventory.read_json(args.inputs)
    plan = inventory.plan(inputs, args.inputs.parent, args.known_device_binding,
                          args.known_device_binding_scheme)
    device = Device(args.adb, args.device, plan, runtime_pins(inputs, args.inputs.parent))
    os.umask(0o077)
    args.output.mkdir(mode=0o700)
    result = prepare(device, plan, args.output, args.action == "prepare")
    print(json.dumps({key: result[key] for key in
                      ("complete", "inspectOnly", "sharedStateCreated", "environmentWrites", "rebootIssued")}))


if __name__ == "__main__":
    main()
