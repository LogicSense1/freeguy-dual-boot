#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Configure an already installed, hash-pinned helper on stock HyperOS.

No APK installation, OEM policy-file edit, startup-parameter write or reboot.
Keeps other accessibility services and records every requested setting change.
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
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("backup", ROOT / "tools/backup-data.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
need = backup.need
PACKAGE = "local.codex.tvadb"
COMPONENT = PACKAGE + "/.ConnectionService"
FULL_COMPONENT = PACKAGE + "/" + PACKAGE + ".ConnectionService"
PERMISSION = "android.permission.WRITE_SECURE_SETTINGS"
SHA = re.compile(r"[0-9a-f]{64}")
COMPONENT_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*/[A-Za-z_.][A-Za-z0-9_.$]*")


def append_service(value):
    """Retain the exact existing list and its order; recognize both spellings."""
    need(isinstance(value, str) and len(value) < 32768, "Invalid accessibility list")
    if value in ("null", ""):
        return COMPONENT
    parts = value.split(":")
    need(all(COMPONENT_PATTERN.fullmatch(p) for p in parts), "Malformed accessibility component")
    own = [p for p in parts if p in (COMPONENT, FULL_COMPONENT)]
    need(len(own) <= 1 and len(parts) == len(set(parts)), "Duplicate accessibility component")
    return value if own else value + ":" + COMPONENT


def local_apk(path, digest):
    need(SHA.fullmatch(digest) and path.is_file() and not path.is_symlink(),
         "Use the reviewed local APK and its independently retained SHA-256")
    size = path.stat().st_size
    need(0 < size <= 16 * 1024**2, "Unexpected helper APK size")
    need(hashlib.sha256(path.read_bytes()).hexdigest() == digest, "Local APK changed")


class Device:
    def __init__(self, adb, selector, binding_source, apk_hash):
        need(re.fullmatch(r"[A-Za-z0-9_.:-]+", selector), "Explicit ADB selector required")
        self.adb = [str(adb), "-s", selector]
        self.binding_source, self.apk_hash = binding_source, apk_hash

    def shell(self, command):
        try:
            return subprocess.check_output(self.adb + ["shell", command],
                                           stderr=subprocess.PIPE, timeout=30).decode().strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise RuntimeError("ADB operation failed; inspect the private pending journal") from None

    def identity(self):
        props = [self.shell("getprop " + p) for p in backup.PROPS]
        need(self.shell("id -u") == "2000", "Require ordinary stock HyperOS ADB")
        need(self.shell("am get-current-user") == "0", "Only the stock owner user is supported")
        cid = self.shell("cat /sys/block/mmcblk0/device/cid") if self.binding_source == "emmc-cid" else None
        value = backup.stock_identity(
            props, self.shell("cat /proc/1/mounts"),
            self.shell("cat /proc/sys/kernel/random/boot_id"), cid,
            self.shell("getprop ro.serialno"), self.binding_source)
        paths = self.shell("pm path " + PACKAGE).splitlines()
        need(len(paths) == 1 and paths[0].startswith("package:/data/app/")
             and paths[0].endswith("/base.apk"), "One locally installed helper APK required")
        path = paths[0][8:]
        digest = self.shell("sha256sum " + shlex.quote(path)).split()
        need(len(digest) == 2 and digest[0] == self.apk_hash,
             "Installed APK differs from the reviewed local helper")
        package = self.shell("dumpsys package " + PACKAGE)
        component_seen = lambda name: any(
            spelling in package for spelling in
            (PACKAGE + "/." + name, PACKAGE + "/" + PACKAGE + "." + name))
        need(re.search(r"\bversionCode=4\b", package)
             and component_seen("MainActivity") and component_seen("ConnectionService"),
             "Expected the published version 4 helper")
        return value

    def snapshot(self):
        services = self.shell("settings get secure enabled_accessibility_services")
        append_service(services)
        enabled = self.shell("settings get secure accessibility_enabled")
        need(enabled in ("null", "0", "1"), "Unexpected accessibility switch")
        package = self.shell("dumpsys package " + PACKAGE)
        matches = re.findall(re.escape(PERMISSION) + r": granted=(true|false)\b", package)
        need(len(matches) <= 1, "Ambiguous helper permission")
        # An ungranted development permission can be absent from the grants section.
        granted = matches == ["true"]
        idle = self.shell("dumpsys deviceidle whitelist").splitlines()
        whitelist = any(len(v.split(",")) == 3 and v.split(",")[1] == PACKAGE for v in idle)
        modes = {}
        for op in ("RUN_IN_BACKGROUND", "RUN_ANY_IN_BACKGROUND"):
            text = self.shell("cmd appops get " + PACKAGE + " " + op)
            found = re.findall(r"\b" + op + r": (allow|ignore|deny|default|foreground)\b", text)
            need(len(found) <= 1, "Ambiguous background app-op")
            need(found or text == "No operations.", "Unrecognized background app-op response")
            modes[op] = found[0] if found else "default"
        return dict(services=services, accessibility=enabled, permission=granted,
                    idleWhitelisted=whitelist, **modes)

    def apply(self, key, value):
        if key == "permission":
            need(value is True, "Only the helper's required permission may be granted")
            command = "pm grant " + PACKAGE + " " + PERMISSION
        elif key == "idleWhitelisted":
            need(value is True, "Only add this package's idle exemption")
            command = "dumpsys deviceidle whitelist +" + PACKAGE
        elif key in ("RUN_IN_BACKGROUND", "RUN_ANY_IN_BACKGROUND"):
            need(value == "allow", "Unexpected app-op target")
            command = "cmd appops set " + PACKAGE + " " + key + " allow"
        elif key == "services":
            need(append_service(value) == value, "Helper component missing")
            command = "settings put secure enabled_accessibility_services " + shlex.quote(value)
        elif key == "accessibility":
            need(value == "1", "Only enable the configured services")
            command = "settings put secure accessibility_enabled 1"
        else:
            raise ValueError("Unknown helper setting")
        self.shell(command)

    def open_helper(self):
        result = self.shell("am start -W -n " + PACKAGE + "/.MainActivity")
        need("Status: ok" in result and "Error:" not in result, "Helper activity did not start")


def provision(device, binding, output, apply=False):
    first = device.identity()
    need(first["deviceBindingSha256"] == binding, "Owner device differs")
    before = device.snapshot()
    target = dict(before, services=append_service(before["services"]), accessibility="1",
                  permission=True, idleWhitelisted=True,
                  RUN_IN_BACKGROUND="allow", RUN_ANY_IN_BACKGROUND="allow")
    output.mkdir(mode=0o700)
    record = dict(mode="adb-helper-provision", complete=False, inspectOnly=not apply,
                  identity=first, apkSha256=device.apk_hash,
                  before=before, target=target, pending=None, operations=[],
                  firmwareWrites=False, environmentWrites=False, rebootIssued=False,
                  activityOpened=False, bootRecoveryVerified=False)
    save = lambda: backup.save(output / "journal.json", record)
    save()
    expected = before.copy()

    def check():
        need(device.identity() == first, "Device or boot changed")
        need(device.snapshot() == expected, "Settings changed outside this operation")

    try:
        check()
        if not apply:
            record["inspectionComplete"] = True
            save()
            return record
        # The service disables itself while the app's opt-in is false. Open the
        # activity after its prerequisites, before binding it for the first time.
        for key in ("permission", "RUN_IN_BACKGROUND", "RUN_ANY_IN_BACKGROUND",
                    "idleWhitelisted", "services", "accessibility"):
            if key == "services":
                check()
                record["pending"] = {"activity": PACKAGE + "/.MainActivity"}
                save()
                device.open_helper()
                check()
                record.update(pending=None, activityOpened=True)
                save()
            if expected[key] == target[key]:
                continue
            check()
            record["pending"] = dict(setting=key, before=expected[key], after=target[key])
            save()
            device.apply(key, target[key])
            expected[key] = target[key]
            check()
            record["operations"].append(record["pending"])
            record["pending"] = None
            save()
        check()
        record.update(pending=None, complete=True)
        save()
        return record
    except BaseException as exc:
        record["failure"] = type(exc).__name__
        save()
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("inspect", "enable"))
    for name in ("adb", "apk", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    for name in ("device", "apk-sha256", "known-device-binding"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--binding-source", choices=("emmc-cid", "android-serial"), default="emmc-cid")
    a = p.parse_args()
    os.umask(0o077)
    need(SHA.fullmatch(a.known_device_binding), "Independently recorded owner binding required")
    local_apk(a.apk, a.apk_sha256)
    backup.private_output(a.output)
    need(not a.output.exists() and not a.output.is_symlink() and a.output.parent.is_dir(),
         "Use a new private journal directory")
    value = provision(Device(a.adb, a.device, a.binding_source, a.apk_sha256),
                      a.known_device_binding, a.output, a.action == "enable")
    print(json.dumps({k: value[k] for k in ("complete", "inspectOnly", "activityOpened",
                                          "rebootIssued", "bootRecoveryVerified")}))


if __name__ == "__main__":
    main()
