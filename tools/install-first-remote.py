#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Share this TV's HyperOS remote bond during the bounded first-install boot.

Only Google's two Bluetooth files are replaced. Each original inode is retained
beside it. Failure leaves the journal and files for inspection; it does not
automatically restart Bluetooth or retry a mutation.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


access, bond = load("prepare-first-switch-access"), load("prepare-remote-bond")
stage, need, q = access.stage, access.need, access.q
DIR = "/data/misc/bluedroid"
STOCK = "/data/misc/bluedroid/bt_config.conf"
LABEL = "u:object_r:bluetooth_data_file:s0"
NAMES = ("conf", "bak")
sha = lambda b: hashlib.sha256(b).hexdigest()


def path(name, role="current"):
    need(name in NAMES and role in ("current", "next", "before", "rejected"), "Unknown pairing path")
    return DIR + "/bt_config." + name + ("" if role == "current" else ".freeguy-" + role + "-v1")


def file_ok(meta):
    need(meta is not None and stat.S_ISREG(meta["mode"]) and meta["links"] == 1
         and meta["uid"] == meta["gid"] == 1002 and meta["mode"] in (0o100600, 0o100660)
         and 0 < meta["bytes"] <= bond.MAX_BYTES and meta["label"] == LABEL,
         "Unexpected Google Bluetooth file metadata")


def same(a, b):
    return a == b


class Device(access.Device):
    def __init__(self, adb, serial, plan, pins):
        super().__init__(adb, serial, plan, pins)
        self.pair_paths = {DIR} | {path(n, r) for n in NAMES
                                  for r in ("current", "next", "before", "rejected")}

    def rawpath(self, value):
        # This class uses the Google path namespace. HyperOS is read through
        # stock_read() only; it has no writable path in this interface.
        need(value in self.pair_paths, "Outside Google pairing paths")
        return value

    def metadata(self, value):
        need(value in self.pair_paths, "Outside Google pairing paths")
        need(self.shell("test ! -L /data && test ! -L /data/misc && "
                        "test ! -L /data/misc/bluedroid && echo OK") == "OK",
             "Redirected Google Bluetooth parent")
        text = self.shell("LC_ALL=C stat -c '%d %i %f %u %g %h %s' " + q(value) +
                          " 2>&1; rc=$?; printf '\\nCTV_STAT_RC %s\\n' \"$rc\"")
        body, sep, code = text.rpartition("\nCTV_STAT_RC ")
        need(sep and code.isdigit(), "Missing pairing metadata result")
        if code != "0":
            need("\n" not in body and body.startswith("stat:") and value in body
                 and body.endswith(": No such file or directory"), "Cannot establish pairing path absence")
            return None
        result = stage.parse_metadata(body)
        result["label"] = self.shell("ls -Zd " + q(value) + " | cut -d ' ' -f 1")
        return result

    def read(self, value):
        meta = self.metadata(value)
        file_ok(meta)
        raw = subprocess.check_output(self.adb + ["exec-out", "cat " + q(value)],
                                      stderr=subprocess.PIPE, timeout=20)
        need(len(raw) == meta["bytes"] and self.metadata(value) == meta, "Pairing file changed while reading")
        return raw

    def stock_read(self):
        need(self.host is not None, "Runtime must be bound before stock reads")
        base = self.host + "/misc/bluedroid"
        target = base + "/bt_config.conf"
        need(self.shell("test ! -L " + q(self.host + "/misc") + " && test ! -L " + q(base) +
                        " && test ! -L " + q(target) + " && test -f " + q(target) + " && echo OK") == "OK",
             "Redirected or missing HyperOS pairing source")
        fields = self.shell("stat -c '%f %h %s' " + q(target)).split()
        need(len(fields) == 3 and all(re.fullmatch("[0-9a-f]+", x) for x in fields)
             and stat.S_ISREG(int(fields[0], 16)) and fields[1] == "1"
             and 0 < int(fields[2]) <= bond.MAX_BYTES, "Unexpected HyperOS pairing file")
        raw = subprocess.check_output(self.adb + ["exec-out", "cat " + q(target)],
                                      stderr=subprocess.PIPE, timeout=20)
        need(len(raw) == int(fields[2]), "Incomplete HyperOS pairing source")
        return raw

    def processes_clear(self):
        processes = self.shell("ps -A -o ARGS")
        need(not any(name in processes for name in
                     ("RemoteVoiceCaptureProbe", "RemoteReconnectProbe", "RemoteKeyProbe",
                      "codex/autovoice/start.sh", "codex/autovoice/lifecycle.sh",
                      "codex-autovoice/start.sh", "codex-autovoice/lifecycle.sh",
                      "codex-autovoice/supervisor.sh")),
             "Remote helpers are running; this installer is only for bounded setup")

    def bt_state(self):
        text = self.shell("dumpsys bluetooth_manager")
        found = re.findall(r"^\s*state: (ON|OFF|TURNING_ON|TURNING_OFF)\s*$", text, re.M)
        need(len(found) == 1, "Ambiguous Bluetooth state")
        return found[0]

    def bt_set(self, enabled):
        self.shell("svc bluetooth " + ("enable" if enabled else "disable"))

    def wait_state(self, target):
        for _ in range(25):
            if self.bt_state() == target:
                return
            time.sleep(1)
        raise RuntimeError("Bluetooth state transition timed out")

    def stop_process(self):
        text = self.shell("pidof com.android.bluetooth || true")
        if not text:
            return
        need(re.fullmatch("[1-9][0-9]*", text), "Ambiguous Bluetooth PID")
        name = self.shell("tr '\\000' '\\n' < /proc/" + text + "/cmdline").splitlines()
        need(name and name[0] == "com.android.bluetooth", "Bluetooth PID identity differs")
        self.shell("kill -TERM " + text)
        for _ in range(15):
            if not self.shell("pidof com.android.bluetooth || true"):
                return
            time.sleep(1)
        raise RuntimeError("Bluetooth process did not stop")

    def quiet(self):
        self.processes_clear()
        need(self.bt_state() == "OFF"
             and not self.shell("pidof com.android.bluetooth || true"), "Bluetooth is not quiescent")

    def set_metadata(self, value, meta):
        need(value in {path(n, "next") for n in NAMES}, "Metadata writes only to new pairing files")
        file_ok(meta)
        self.shell("chown 1002:1002 " + q(value) + " && chmod " +
                   format(meta["mode"] & 0o777, "o") + " " + q(value) +
                   " && chcon " + LABEL + " " + q(value))

    def rename(self, source, target):
        need(source in self.pair_paths and target in self.pair_paths and source != DIR and target != DIR,
             "Unknown pairing rename")
        need(self.metadata(target) is None, "Pairing rename target occupied")
        self.shell("mv -n " + q(source) + " " + q(target))

    def bond_listed(self, remote):
        text = self.shell("dumpsys bluetooth_manager")
        need("Bonded devices:" in text, "No bonded-device section")
        section = text.split("Bonded devices:", 1)[1].split("\n\n", 1)[0]
        return remote.lower() in section.lower()


def install(device, output, apply=False):
    identity = device.guard()
    device.processes_clear()
    directory = device.metadata(DIR)
    need(directory is not None and directory["mode"] == 0o40770
         and directory["uid"] == directory["gid"] == 1002 and directory["label"] == LABEL,
         "Unexpected Bluetooth directory")
    for n in NAMES:
        file_ok(device.metadata(path(n)))
        for role in ("next", "before", "rejected"):
            need(device.metadata(path(n, role)) is None, "Prior pairing operation exists")
    stock = device.stock_read()
    # Read-only inspection may race with Bluetooth; refuse rather than relying
    # on these bytes for installation. Stable capture follows quiescence.
    for n in NAMES:
        bond.merge(stock, device.read(path(n)))
    initial_state = device.bt_state()
    need(initial_state in ("ON", "OFF"), "Wait for a settled Bluetooth state")
    output.mkdir(mode=0o700)
    record = dict(mode="first-remote-install", complete=False, inspectOnly=not apply,
                  binding=identity, directory=directory, before={}, prepared={}, pending=None,
                  operations=[], uploadProcess=None, stockWritten=False, environmentWrites=False,
                  firmwareWrites=False, rebootIssued=False, bluetoothEnabled=False,
                  bondListed=False, physicalRemoteVerified=False, initialBluetoothState=initial_state)
    save = lambda: stage.save(output, record)
    save()

    def stable():
        need(device.guard() == identity, "Device or boot changed")
        now = device.metadata(DIR)
        need(now is not None and all(now[k] == directory[k] for k in
                                    ("dev", "ino", "mode", "uid", "gid", "label")),
             "Bluetooth directory changed")
        need(device.stock_read() == stock, "HyperOS pairing changed")

    def action(label, function):
        stable()
        changing_files = label.startswith(("upload-", "metadata-", "flush-", "retain-", "commit-"))
        if changing_files:
            device.quiet()
        record["pending"] = label
        save()
        value = function()
        if changing_files:
            device.quiet()
        record["operations"].append(label)
        record["pending"] = None
        save()
        return value

    def local(name, data):
        fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        need(bond.read_private(output / name) == data, "Independent pairing snapshot readback failed")

    try:
        stable()
        if not apply:
            record["inspectionComplete"] = True
            save()
            return record
        action("disable-bluetooth", lambda: device.bt_set(False))
        device.wait_state("OFF")
        action("stop-bluetooth-process", device.stop_process)
        device.quiet()
        stable()
        local("hyperos-source.conf", stock)
        record["stockSha256"] = sha(stock)
        desired = {}
        for n in NAMES:
            meta = device.metadata(path(n))
            file_ok(meta)
            raw = device.read(path(n))
            need(same(meta, device.metadata(path(n))), "Google source changed")
            desired[n] = bond.merge(stock, raw)
            record["before"][n] = dict(metadata=meta, sha256=sha(raw))
            record["prepared"][n] = dict(bytes=len(desired[n]), sha256=sha(desired[n]))
            local("before." + n, raw)
            local("prepared." + n, desired[n])
        save()
        # Upload both complete replacements before moving either original.
        for n in NAMES:
            device.quiet()
            stable()
            need(device.metadata(path(n, "next")) is None, "Staged pairing destination appeared")

            def uploaded(event):
                record["uploadProcess"] = event
                save()

            action("upload-" + n, lambda n=n: device.upload(
                path(n, "next"), io.BytesIO(desired[n]), uploaded))
            target_meta = dict(record["before"][n]["metadata"], bytes=len(desired[n]))
            action("metadata-" + n, lambda n=n, meta=target_meta: device.set_metadata(path(n, "next"), meta))
            need(device.read(path(n, "next")) == desired[n], "Staged pairing bytes differ")
            action("flush-" + n, lambda n=n: device.fsync(path(n, "next")))
            record["prepared"][n]["metadata"] = device.metadata(path(n, "next"))
            save()
        for n in NAMES:
            device.quiet()
            stable()
            need(device.metadata(path(n)) == record["before"][n]["metadata"]
                 and sha(device.read(path(n))) == record["before"][n]["sha256"],
                 "Original pairing file changed")
            need(device.metadata(path(n, "before")) is None, "Predecessor path occupied")
            action("retain-" + n, lambda n=n: device.rename(path(n), path(n, "before")))
            need(device.metadata(path(n)) is None
                 and device.metadata(path(n, "before")) == record["before"][n]["metadata"],
                 "Original pairing inode was not retained")
            action("commit-" + n, lambda n=n: device.rename(path(n, "next"), path(n)))
            need(device.metadata(path(n)) == record["prepared"][n]["metadata"]
                 and device.read(path(n)) == desired[n], "Committed pairing differs")
            action("flush-directory-" + n, lambda: device.fsync(DIR))
        device.quiet()
        stable()
        for n in NAMES:
            need(sha(device.read(path(n, "before"))) == record["before"][n]["sha256"],
                 "Predecessor pairing changed")
            need(device.metadata(path(n)) == record["prepared"][n]["metadata"]
                 and device.read(path(n)) == desired[n], "Committed pairing changed before Bluetooth start")
        record["filesCommitted"] = True
        save()
        action("enable-bluetooth", lambda: device.bt_set(True))
        device.wait_state("ON")
        remote = next(s for s in bond.parse(stock).sections()
                      if bond.parse(stock).get(s, "Name", fallback="") == bond.REMOTE_NAME)
        need(device.bond_listed(remote), "Imported remote is not listed")
        stable()
        record.update(complete=True, bluetoothEnabled=True, bondListed=True)
        save()
        return record
    except BaseException as exc:
        record.update(failure=type(exc).__name__, complete=False)
        save()
        raise


def restore(device, previous, source_folder, output):
    """Undo an incomplete file transaction. Refuse changed current pairings."""
    need(previous.get("mode") == "first-remote-install" and previous.get("complete") is False
         and previous.get("inspectOnly") is False
         and previous.get("initialBluetoothState") in ("ON", "OFF"),
         "Only an incomplete installation can use transaction recovery")
    identity = device.guard()
    need(identity == previous.get("binding"), "Recovery requires the original setup boot and TV")
    device.processes_clear()
    before, prepared = previous.get("before", {}), previous.get("prepared", {})
    has_files = set(before) == set(prepared) == set(NAMES)
    touched = any(str(op).startswith(("upload-", "metadata-", "retain-", "commit-"))
                  for op in previous.get("operations", []) + [previous.get("pending")])
    need(has_files or not touched, "Missing source snapshots for an attempted file operation")
    stock = device.stock_read()
    if has_files:
        need(sha(stock) == previous.get("stockSha256"), "HyperOS pairing changed")
        for n in NAMES:
            file_ok(before[n]["metadata"])
            raw = bond.read_private(source_folder / ("before." + n))
            new = bond.read_private(source_folder / ("prepared." + n))
            need(sha(raw) == before[n]["sha256"] and len(raw) == before[n]["metadata"]["bytes"]
                 and sha(new) == prepared[n]["sha256"] and new == bond.merge(stock, raw),
                 "Independent pairing snapshots changed")
    output.mkdir(mode=0o700)
    record = dict(mode="first-remote-restore", complete=False, binding=identity,
                  sourceJournalSha256=sha(json.dumps(previous, sort_keys=True).encode()),
                  pending=None, operations=[], stockWritten=False, rebootIssued=False,
                  physicalRemoteVerified=False)
    save = lambda: stage.save(output, record)
    save()

    def stable():
        need(device.guard() == identity and device.stock_read() == stock, "Device or source changed")
        directory = device.metadata(DIR)
        need(directory is not None and all(directory[k] == previous["directory"][k] for k in
                                          ("dev", "ino", "mode", "uid", "gid", "label")),
             "Bluetooth directory changed")

    def act(label, function):
        stable()
        changing_files = label.startswith(("retain-", "restore-", "flush-"))
        if changing_files:
            device.quiet()
        record["pending"] = label
        save()
        function()
        if changing_files:
            device.quiet()
        record["operations"].append(label)
        record["pending"] = None
        save()

    def matches(value, row):
        return (device.metadata(value) == row.get("metadata")
                and row.get("metadata") is not None and sha(device.read(value)) == row["sha256"])

    try:
        stable()
        act("disable-bluetooth", lambda: device.bt_set(False))
        device.wait_state("OFF")
        act("stop-bluetooth-process", device.stop_process)
        device.quiet()
        # Classify the entire set before changing either primary file.
        plans = {}
        for n in NAMES:
            saved = device.metadata(path(n, "before"))
            if not has_files:
                need(all(device.metadata(path(n, role)) is None for role in
                         ("next", "before", "rejected")), "Unrecorded pairing files exist")
                continue
            current = device.metadata(path(n))
            if saved is None:
                need(matches(path(n), before[n]), "Original pairing is no longer intact")
                plans[n] = False
            else:
                need(matches(path(n, "before"), before[n]), "Retained original changed")
                need(current is None or matches(path(n), prepared[n]),
                     "Current pairing changed; do not overwrite a later bond")
                need(device.metadata(path(n, "rejected")) is None, "Recovery destination occupied")
                plans[n] = True
        for n, replace in plans.items():
            if not replace:
                continue
            device.quiet()
            need(matches(path(n, "before"), before[n]), "Original changed during recovery")
            if device.metadata(path(n)) is not None:
                need(matches(path(n), prepared[n]), "Current pairing changed during recovery")
                act("retain-rejected-" + n, lambda n=n: device.rename(path(n), path(n, "rejected")))
            need(device.metadata(path(n)) is None, "Current destination occupied")
            act("restore-" + n, lambda n=n: device.rename(path(n, "before"), path(n)))
            need(matches(path(n), before[n]), "Original inode or content was not restored")
            act("flush-directory-" + n, lambda: device.fsync(DIR))
        if has_files:
            need(all(matches(path(n), before[n]) for n in NAMES), "Pairing restoration incomplete")
        stable()
        if previous["initialBluetoothState"] == "ON":
            act("enable-bluetooth", lambda: device.bt_set(True))
            device.wait_state("ON")
        record.update(complete=True, originalsRestored=has_files,
                      bluetoothState=device.bt_state(), auxiliaryFilesRetained=True)
        save()
        return record
    except BaseException as exc:
        record["failure"] = type(exc).__name__
        save()
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("inspect", "install", "restore"))
    for name in ("adb", "inputs", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--device", required=True)
    p.add_argument("--known-device-binding", required=True)
    p.add_argument("--known-device-binding-scheme", choices=stage.inventory.BINDING_SCHEMES, required=True)
    p.add_argument("--install-record", type=Path)
    p.add_argument("--install-record-sha256")
    a = p.parse_args()
    os.umask(0o077)
    stage.backup.private_output(a.output)
    need(not a.output.exists() and not a.output.is_symlink() and a.output.parent.is_dir(),
         "Use a new private journal directory")
    inputs = stage.inventory.read_json(a.inputs)
    plan = stage.inventory.plan(inputs, a.inputs.parent,
                                a.known_device_binding, a.known_device_binding_scheme)
    device = Device(a.adb, a.device, plan, access.runtime_pins(inputs, a.inputs.parent))
    if a.action == "restore":
        need(a.install_record and a.install_record_sha256
             and stage.inventory.SHA.fullmatch(a.install_record_sha256),
             "Pin the incomplete install journal")
        previous = stage.inventory.read_json(a.install_record)
        need(sha(a.install_record.read_bytes()) == a.install_record_sha256, "Install journal changed")
        result = restore(device, previous, a.install_record.parent, a.output)
    else:
        need(a.install_record is None and a.install_record_sha256 is None,
             "Recovery inputs belong only to restore")
        result = install(device, a.output, a.action == "install")
    print(json.dumps({k: result[k] for k in ("mode", "complete", "stockWritten",
                                            "physicalRemoteVerified", "rebootIssued")}))


if __name__ == "__main__":
    main()
