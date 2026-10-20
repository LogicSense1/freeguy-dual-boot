#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Enable the switch app's startup binding on a fresh stock HyperOS installation.

Requires a reviewed, already installed stock-style daily APK and no shared
default directory. Preserves other accessibility services. Does not install an
APK, select Google, create shared state, change boot arguments or reboot.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage, helper = load("stage-first-install"), load("provision-adb-helper")
need = stage.need
PACKAGE = "local.codex.tvswitchprobe"
COMPONENT = PACKAGE + "/.DefaultStartupService"
FULL_COMPONENT = PACKAGE + "/" + PACKAGE + ".DefaultStartupService"
SHARED = "/data/local/codex-system-default-v1"


def service_target(value):
    need(isinstance(value, str) and len(value) < 32768, "Invalid accessibility list")
    if value in ("", "null"):
        return COMPONENT
    parts = value.split(":")
    need(all(helper.COMPONENT_PATTERN.fullmatch(p) for p in parts)
         and len(parts) == len(set(parts))
         and sum(p in (COMPONENT, FULL_COMPONENT) for p in parts) <= 1,
         "Malformed or duplicate accessibility component")
    return value if any(p in (COMPONENT, FULL_COMPONENT) for p in parts) else value + ":" + COMPONENT


class Device(stage.Device):
    def __init__(self, adb, selector, binding, scheme, apk_hash, version):
        plan = dict(deviceBinding=binding, deviceBindingScheme=scheme,
                    directories=[], mustBeAbsent=[SHARED], files=[], markers=[])
        super().__init__(adb, selector, plan)
        self.apk_hash, self.version = apk_hash, version

    def guard(self):
        result = super().guard()
        need(self.shell("am get-current-user") == "0", "Owner user required")
        need(self.metadata(SHARED) is None, "Existing defaults: use the installed system, not first provisioning")
        paths = self.shell("pm path " + PACKAGE).splitlines()
        need(len(paths) == 1 and paths[0].startswith("package:/data/app/")
             and paths[0].endswith("/base.apk"), "Expected one locally installed switch APK")
        digest = self.shell("sha256sum " + shlex.quote(paths[0][8:])).split()
        need(len(digest) == 2 and digest[0] == self.apk_hash, "Installed APK differs from reviewed build")
        dump = self.shell("dumpsys package " + PACKAGE)
        versions = re.findall(r"\bversionCode=([0-9]+)\b", dump)
        need(versions == [str(self.version)], "Installed switch version differs")
        # dumpsys lists intent resolvers; the unfiltered JobService is not in
        # that list. The exact installed APK hash covers its manifest too.
        for component in ("SwitchActivity", "DefaultStartupService"):
            need(any(v in dump for v in (PACKAGE + "/." + component,
                                        PACKAGE + "/" + PACKAGE + "." + component)),
                 "Required switch component missing")
        return result

    def snapshot(self):
        services = self.shell("settings get secure enabled_accessibility_services")
        service_target(services)
        enabled = self.shell("settings get secure accessibility_enabled")
        need(enabled in ("null", "0", "1"), "Unexpected accessibility switch")
        idle = self.shell("dumpsys deviceidle whitelist").splitlines()
        whitelisted = any(len(row.split(",")) == 3 and row.split(",")[1] == PACKAGE for row in idle)
        modes = {}
        for op in ("RUN_IN_BACKGROUND", "RUN_ANY_IN_BACKGROUND"):
            text = self.shell("cmd appops get " + PACKAGE + " " + op)
            found = re.findall(r"\b" + op + r": (allow|ignore|deny|default|foreground)\b", text)
            need(len(found) <= 1 and (found or text == "No operations."), "Unknown app-op response")
            modes[op] = found[0] if found else "default"
        return dict(services=services, accessibility=enabled, idleWhitelisted=whitelisted, **modes)

    def apply(self, key, value):
        if key == "services":
            need(service_target(value) == value, "Missing switch startup service")
            command = "settings put secure enabled_accessibility_services " + shlex.quote(value)
        elif key == "accessibility":
            need(value == "1", "Only enable configured services")
            command = "settings put secure accessibility_enabled 1"
        elif key == "idleWhitelisted":
            need(value is True, "Only add this package")
            command = "dumpsys deviceidle whitelist +" + PACKAGE
        else:
            need(key in ("RUN_IN_BACKGROUND", "RUN_ANY_IN_BACKGROUND") and value == "allow",
                 "Unexpected switch app-op")
            command = "cmd appops set " + PACKAGE + " " + key + " allow"
        self.shell(command)

    def open_app(self):
        result = self.shell("am start -W -n " + PACKAGE + "/.SwitchActivity")
        need("Status: ok" in result and "Error:" not in result, "Switch activity did not start")


def provision(device, output, apply=False):
    identity = device.guard()
    before = device.snapshot()
    target = dict(before, services=service_target(before["services"]), accessibility="1",
                  idleWhitelisted=True, RUN_IN_BACKGROUND="allow", RUN_ANY_IN_BACKGROUND="allow")
    output.mkdir(mode=0o700)
    record = dict(mode="first-switch-startup", complete=False, inspectOnly=not apply,
                  identity=identity, apkSha256=device.apk_hash, before=before, target=target,
                  pending=None, operations=[], activityOpened=False, startupBindingVerified=False,
                  runtimeSwitchVerified=False, bootRecoveryVerified=False,
                  environmentWritesIssued=False, firmwareWritesIssued=False, rebootIssued=False)
    save = lambda: stage.save(output, record)
    expected = before.copy()

    def check():
        need(device.guard() == identity, "Device or boot changed")
        need(device.snapshot() == expected, "Settings changed outside this operation")

    save()
    try:
        check()
        if not apply:
            record["inspectionComplete"] = True
            save()
            return record
        # Opening removes Android's stopped-package state. Until finalization
        # creates shared defaults, the app cannot start an automatic transition.
        record["pending"] = {"activity": PACKAGE + "/.SwitchActivity"}
        save()
        device.open_app()
        check()
        record.update(activityOpened=True, pending=None)
        save()
        for key in ("RUN_IN_BACKGROUND", "RUN_ANY_IN_BACKGROUND",
                    "idleWhitelisted", "services", "accessibility"):
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
        record["complete"] = True
        save()
        return record
    except BaseException as exc:
        record["failure"] = type(exc).__name__
        save()
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("inspect", "enable"))
    for name in ("adb", "apk", "build-result", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    for name in ("device", "apk-sha256", "build-result-sha256", "known-device-binding"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--known-device-binding-scheme", choices=stage.inventory.BINDING_SCHEMES, required=True)
    a = p.parse_args()
    os.umask(0o077)
    need(stage.inventory.SHA.fullmatch(a.known_device_binding), "Independently recorded owner required")
    helper.local_apk(a.apk, a.apk_sha256)
    result = stage.inventory.read_json(a.build_result)
    need(stage.inventory.SHA.fullmatch(a.build_result_sha256)
         and hashlib.sha256(a.build_result.read_bytes()).hexdigest() == a.build_result_sha256
         and result.get("sha256") == a.apk_sha256 and result.get("dailyDefault") is True
         and result.get("launcherStyle") == "stock" and result.get("signatureVerified") is True
         and type(result.get("versionCode")) is int and result["versionCode"] > 0,
         "Require the reviewed stock-style daily switch build")
    stage.backup.private_output(a.output)
    need(not a.output.exists() and not a.output.is_symlink() and a.output.parent.is_dir(),
         "Use a new private output directory")
    report = provision(Device(a.adb, a.device, a.known_device_binding,
                              a.known_device_binding_scheme, a.apk_sha256, result["versionCode"]),
                       a.output, a.action == "enable")
    print(json.dumps({k: report[k] for k in ("complete", "inspectOnly", "activityOpened",
                                            "runtimeSwitchVerified", "rebootIssued")}))


if __name__ == "__main__":
    main()
