#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run or observe one already-provisioned updater; never reboot or upload images."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/data/local/codex-image-update-v1"
TAG = "CodexImageUpdate"
HEX = re.compile(r"[0-9a-f]{64}")
TOKEN = re.compile(r"[0-9a-f]{32}")
BOOT = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
LINE = re.compile(r"^[VDIWEF]/CodexImageUpdate\s*\(\s*(\d+)\):\s?(.*)$")


def need(ok, message):
    if not ok:
        raise ValueError(message)


def private_path(path, new=False):
    resolved = path.resolve()
    need(resolved != ROOT and ROOT not in resolved.parents,
         "Run records must stay outside the checkout")
    need(not path.is_symlink(), "Symlink output refused")
    if new:
        need(not path.exists() and path.parent.is_dir(), "Use a new private directory")


def action_args(action, stop):
    need(action in ("inspect", "apply", "rollback"), "Unsupported action")
    need(stop is None or (type(stop) is int and action != "inspect" and 1 <= stop <= 8),
         "Invalid interruption point")
    return action + ("" if stop is None else " " + str(stop))


def checked_build(build, old_profile, new_profile, probe):
    spec = importlib.util.spec_from_file_location("updater_builder", ROOT / "tools/build-image-updater.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = module.plan(module.read_profile(old_profile), module.read_profile(new_profile), probe)
    plan_path, dex = build / "plan.json", build / "dex/classes.dex"
    need(plan_path.is_file() and not plan_path.is_symlink() and plan_path.stat().st_size < 65536,
         "Missing or invalid local plan")
    actual = json.loads(plan_path.read_text())
    need(all(actual.get(k) == v for k, v in expected.items()), "Plan differs from profiles")
    need(dex.is_file() and not dex.is_symlink() and 0 < dex.stat().st_size <= 4 * 1024 * 1024,
         "Missing or invalid local DEX")
    digest = hashlib.sha256(dex.read_bytes()).hexdigest()
    need(actual.get("dexSha256") == digest, "Local updater hash mismatch")
    return expected["id"], digest


def validate_record(r):
    need(r.get("schema") == 1 and TOKEN.fullmatch(r.get("nonce", "")), "Invalid run record")
    need(re.fullmatch(r"[0-9a-f]{16}", r.get("plan", "")), "Invalid plan identity")
    need(HEX.fullmatch(r.get("dexSha256", "")), "Invalid DEX hash")
    need(BOOT.fullmatch(r.get("boot", "")), "Invalid boot identity")
    need(isinstance(r.get("serial"), str) and re.fullmatch(r"[A-Za-z0-9_.:\[\]-]+", r["serial"])
         and not r["serial"].startswith("-"), "An explicit ADB serial is required")
    need(r.get("remoteDex") == REMOTE + "/" + r["dexSha256"] + ".dex",
         "Only a content-addressed provisioned DEX is accepted")
    action_args(r.get("action"), r.get("stopAfter"))


def script(r):
    validate_record(r)
    n, dex = r["nonce"], r["remoteDex"]
    args = action_args(r["action"], r.get("stopAfter"))
    # Every substituted value is constrained above; no device or profile text
    # is interpolated as shell syntax. No command grants permissions.
    return f"""#!/system/bin/sh
log -t {TAG} "RUN_START {n} $$"
fail() {{ log -t {TAG} "RUN_FAILED {n} $$ $1"; exit 9; }}
test "$(id -u)" = 1000 || fail uid
test "$(cat /proc/sys/kernel/random/boot_id)" = '{r["boot"]}' || fail boot
test "$(sha256sum {dex} | cut -d ' ' -f 1)" = '{r["dexSha256"]}' || fail dex
CLASSPATH={dex} app_process /system/bin local.codex.tvswitchprobe.ImageUpdateRunner {args} &
child=$!
log -t {TAG} "RUN_CHILD {n} $$ $child"
wait "$child"
status=$?
log -t {TAG} "RUN_DONE {n} $$ $status"
exit "$status"
"""


def parse_result(log, r):
    """Match a single shell's delimiters and its Java child's log records."""
    validate_record(r)
    entries = [(int(m[1]), m[2]) for line in log.splitlines() if (m := LINE.fullmatch(line))]
    start_pattern = re.compile(r"RUN_START " + r["nonce"] + r" ([1-9][0-9]*)")
    starts = [i for i, (_, msg) in enumerate(entries) if start_pattern.fullmatch(msg)]
    if not starts:
        return {"terminal": False, "reason": "start-not-observed"}
    if len(starts) != 1:
        return {"terminal": False, "reason": "ambiguous-start"}
    entries = entries[starts[0]:]
    # Android's `log` is a separate process on this firmware. Its logcat PID is
    # not the invoking shell's PID; the shell explicitly prints $$ in delimiters.
    shell = start_pattern.fullmatch(entries[0][1])[1]
    child, done, failed = [], [], []
    for i, (pid, msg) in enumerate(entries):
        for key, target in (("RUN_CHILD", child), ("RUN_DONE", done), ("RUN_FAILED", failed)):
            prefix = key + " " + r["nonce"] + " " + shell + " "
            if msg.startswith(prefix):
                target.append((i, msg[len(prefix):]))
    if failed:
        return {"terminal": True, "passed": False, "reason": "shell-preflight-failed"}
    if len(child) != 1 or not child[0][1].isdigit() or int(child[0][1]) <= 0:
        return {"terminal": False, "reason": "child-not-established"}
    child_pid = int(child[0][1])
    if len(done) != 1 or not done[0][1].isdigit():
        return {"terminal": False, "reason": "completion-not-observed", "childPid": child_pid}
    if done[0][0] <= child[0][0]:
        return {"terminal": False, "reason": "invalid-delimiter-order"}
    # Child can log before its parent emits RUN_CHILD; RUN_START is the lower bound.
    messages = [msg for pid, msg in entries[:done[0][0]] if pid == child_pid]
    stops = [m for m in messages if m.startswith("STOPPED ")]
    states = r"\[(?:ORIGINAL|SAVED|INSTALLED)(?:, (?:ORIGINAL|SAVED|INSTALLED)){3}\]"
    before = [m for m in messages if re.fullmatch("BEFORE " + states + " plan=" + r["plan"], m)]
    verified = [m for m in messages if re.fullmatch("VERIFIED " + states + " plan=" + r["plan"], m)]
    complete = [m for m in messages if re.fullmatch(
        r"COMPLETE action=" + r["action"] + r" moves=[0-8] bootWrites=false dataWrites=false", m)]
    expected_stop = r.get("stopAfter") is not None
    if expected_stop:
        wanted = "java.io.InterruptedIOException: Requested stop after durable rename " + str(r["stopAfter"])
        passed = (len(before) == 1 and len(stops) == 1
                  and stops[0] == "STOPPED plan=" + r["plan"]
                  and wanted in messages and not complete and not verified)
    else:
        passed = len(before) == len(verified) == len(complete) == 1 and not stops
        if passed and r["action"] != "inspect":
            state = "INSTALLED" if r["action"] == "apply" else "ORIGINAL"
            passed = verified[0] == "VERIFIED [" + ", ".join([state] * 4) + "] plan=" + r["plan"]
    return {"terminal": True, "passed": passed and done[0][1] == "0",
            "childPid": child_pid, "expectedStop": expected_stop,
            "reason": "checked-result" if passed else "incomplete-or-failed-child"}


def shell(serial, command, timeout=20):
    return subprocess.check_output(["adb", "-s", serial, "shell", command],
                                   text=True, stderr=subprocess.PIPE, timeout=timeout).strip()


def identity(serial):
    value = shell(serial, "getprop ro.product.device; getprop ro.build.version.release; "
                  "getprop ro.boot.slot_suffix; getprop ro.bootloader; "
                  "getprop sys.boot_completed; cat /proc/sys/kernel/random/boot_id")
    lines = value.splitlines()
    need(len(lines) == 6 and lines[:5] == ["freeguy", "11", "_b", "01.01.250225", "1"]
         and BOOT.fullmatch(lines[5]), "Completed original HyperOS B required")
    return lines[5]


def save(directory, record):
    temporary = directory / "record.next"
    with temporary.open("w") as out:
        json.dump(record, out, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(directory / "record.json")


def observe(directory, r, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            current = identity(r["serial"])
            if current != r["boot"]:
                r["observation"] = {"terminal": True, "passed": False, "reason": "device-rebooted"}
                save(directory, r)
                return False
            log = shell(r["serial"], "logcat -d -v brief -s CodexImageUpdate", 15)
            result = parse_result(log, r)
            # Only this component's logs, after this run's delimiter, go to private evidence.
            marker = "RUN_START " + r["nonce"]
            if marker in log:
                offset = log.rfind("\n", 0, log.index(marker)) + 1
                (directory / "runtime.txt").write_text(log[offset:] + "\n")
            r["observation"] = result
            if result["terminal"]:
                result["sameStockBoot"] = identity(r["serial"]) == r["boot"]
                result["passed"] = result["passed"] and result["sameStockBoot"]
                save(directory, r)
                print(json.dumps(result))
                return result["passed"]
            save(directory, r)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError):
            r["observation"] = {"terminal": False, "reason": "connection-or-identity-unconfirmed"}
            save(directory, r)
        time.sleep(3)
    r["observationExpired"] = True
    save(directory, r)
    print("Observation expired. Use observe on this run; do not dispatch another update.")
    return False


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="mode", required=True)
    start = sub.add_parser("start")
    start.add_argument("--serial", required=True)
    start.add_argument("--build", type=Path, required=True)
    start.add_argument("--old-profile", type=Path, required=True)
    start.add_argument("--new-profile", type=Path, required=True)
    start.add_argument("--probe", action="store_true")
    start.add_argument("--action", choices=("inspect", "apply", "rollback"), required=True)
    start.add_argument("--stop-after", type=int)
    start.add_argument("--output", type=Path, required=True)
    watch = sub.add_parser("observe")
    watch.add_argument("--run", type=Path, required=True)
    for parser in (start, watch):
        parser.add_argument("--seconds", type=int, default=900)
    a = p.parse_args()
    need(1 <= a.seconds <= 3600, "Observation window must be 1 to 3600 seconds")
    os.umask(0o077)
    if a.mode == "observe":
        private_path(a.run)
        r = json.loads((a.run / "record.json").read_text())
        validate_record(r)
        return observe(a.run, r, a.seconds)
    action_args(a.action, a.stop_after)
    private_path(a.output, new=True)
    plan, digest = checked_build(a.build, a.old_profile, a.new_profile, a.probe)
    need(re.fullmatch(r"[A-Za-z0-9_.:\[\]-]+", a.serial) and not a.serial.startswith("-"),
         "An explicit ADB serial is required")
    r = dict(schema=1, nonce=uuid.uuid4().hex, serial=a.serial, boot=identity(a.serial),
             plan=plan, dexSha256=digest, remoteDex=REMOTE + "/" + digest + ".dex",
             action=a.action, stopAfter=a.stop_after, dispatch="not-attempted")
    body = script(r)
    a.output.mkdir(mode=0o700)
    save(a.output, r)
    path = REMOTE + "/run-" + r["nonce"] + ".sh"
    # A service-call timeout is ambiguous. Persist intent before either call,
    # never retry a write/launch, and leave observe available afterward.
    for phase, call in (("creating-script", ["2", "s16", path, "s16", body]),
                        ("launching", ["3", "s16", "/system/bin/sh " + path])):
        r["dispatch"] = phase
        save(a.output, r)
        response = shell(a.serial, shlex.join(["service", "call", "TvService", *call]))
        need(re.search(r"\b00000000\s+00000001\b", response),
             "Service acceptance unconfirmed; inspect the saved run before continuing")
    r["dispatch"] = "launch-accepted"
    save(a.output, r)
    return observe(a.output, r, a.seconds)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 2)
