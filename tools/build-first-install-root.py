#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Derive a bounded first-install runtime from a pinned assembled daily runtime.

Only two scripts in each ramdisk root change. No device commands, shared-state
creation, image initialization or boot activation. Outputs contain OEM files
and the owner's ADB public key and must remain private.
"""
import argparse
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("components", ROOT / "tools/assemble-runtime-components.py")
components = importlib.util.module_from_spec(spec)
spec.loader.exec_module(components)
need = components.need
sha = lambda b: hashlib.sha256(b).hexdigest()

DAILY_WATCHDOG = '''/codex/trial-guard watchdog > /dev/null 2> "$LOG/transition-guard.txt" &
TRANSITION_GUARD_PID=$!
kill -0 "$TRANSITION_GUARD_PID"
/codex/helper watchdog-daily > /dev/null 2> "$LOG/watchdog.txt" &
WATCHDOG_PID=$!
echo "$WATCHDOG_PID" > /dev/codex-watchdog.pid
for n in $(seq 1 10); do test -f /dev/codex-daily-watchdog-ready && break; kill -0 "$WATCHDOG_PID"; sleep 1; done
test "$(cat /dev/codex-daily-watchdog-ready)" = ready
kill -0 "$WATCHDOG_PID"
kill "$TRANSITION_GUARD_PID"
wait "$TRANSITION_GUARD_PID" || true
'''
FIRST_WATCHDOG = '''# A first installation has no shared fault file yet.
/codex/helper watchdog > /dev/null 2> "$LOG/watchdog.txt" &
WATCHDOG_PID=$!
echo "$WATCHDOG_PID" > /dev/codex-watchdog.pid
kill -0 "$WATCHDOG_PID"
'''
DAILY_SHARED = '''SHARED="$H/local/codex-system-default-v1"
test ! -L "$SHARED" && test -d "$SHARED"
test "$(stat -c '%u %g %a' "$SHARED")" = '1000 1000 700'
for F in state fault; do test ! -L "$SHARED/$F" && test -f "$SHARED/$F"; done
test "$(cat "$SHARED/fault")" = OK
test ! -L "$G/data/local" && test ! -L "$G/data/local/codex-system-default-v1"
mkdir -p "$G/data/local/codex-system-default-v1"
mount --bind "$SHARED" "$G/data/local/codex-system-default-v1"
MOUNTS="$G/data/local/codex-system-default-v1 $MOUNTS"
echo shared-default-v1 > /dev/codex-daily-v1
chmod 644 /dev/codex-daily-v1
cp /codex/helper /dev/codex-helper-daily
chmod 755 /dev/codex-helper-daily
'''
FIRST_MARKER = '''echo first-install-v1 > /dev/codex-first-install-v1
chmod 644 /dev/codex-first-install-v1
'''
DAILY_START = '''    (
        /system/bin/sh /dev/codex-runtime-policy-start.sh > /data/local/tmp/codex-fileboot-logs/runtime-policy.txt 2>&1 || exit 1
        /system/bin/sh /dev/codex-autovoice/start.sh > /data/local/tmp/codex-fileboot-logs/remote-voice-startup.txt 2>&1
    ) &
'''
FIRST_START = '''    # Daily voice needs the shared-state profile; configure it after bootstrap.
    /system/bin/sh /dev/codex-runtime-policy-start.sh > /data/local/tmp/codex-fileboot-logs/runtime-policy.txt 2>&1 &
'''


def scripts():
    daily_entry = components.patch_entry((ROOT / "runtime/fileboot-entry.sh").read_bytes())
    daily_start = (ROOT / "runtime/runtime-policy-start.sh").read_bytes()
    entry = daily_entry.decode()
    changes = (
        (DAILY_WATCHDOG, FIRST_WATCHDOG),
        (DAILY_SHARED, FIRST_MARKER),
        (DAILY_START, FIRST_START),
        ('STAGE="$H/local/tmp/codex-gtv-fileboot-v1"\n',
         '# Refuse to substitute a setup runtime for an installed daily system.\n'
         'test ! -e "$H/local/codex-system-default-v1"\n'
         'test ! -L "$H/local/codex-system-default-v1"\n'
         'STAGE="$H/local/tmp/codex-gtv-fileboot-v1"\n'),
        ('/dev/codex-helper-daily monotonic-seconds > /dev/codex-health-heartbeat.tmp',
         'cut -d . -f 1 /proc/uptime > /dev/codex-health-heartbeat.tmp'),
    )
    for before, after in changes:
        entry = components.replace_once(entry, before, after)
    starter = components.replace_once(daily_start.decode(),
        '[ "$(cat /dev/codex-daily-v1)" = shared-default-v1 ]',
        '[ "$(cat /dev/codex-first-install-v1)" = first-install-v1 ]')
    need("watchdog-daily" not in entry and "shared-default-v1" not in entry
         and "/dev/codex-helper-daily" not in entry and "TRANSITION_GUARD_PID" not in entry,
         "Unexpected daily dependency remains")
    return {"codex/entry.sh": (daily_entry, entry.encode()),
            "codex/runtime-policy-start.sh": (daily_start, starter.encode())}


def transform(rows):
    byname = {row["name"]: row for row in rows}
    need(len(byname) == len(rows), "Duplicate ramdisk entry")
    components.archive.table(rows)
    changes = scripts()
    output = copy.deepcopy(rows)
    result = {row["name"]: row for row in output}
    report = []
    for prefix in ("", "first_stage_ramdisk/"):
        for name, (before, after) in changes.items():
            key = prefix + name
            need(key in byname and stat.S_ISREG(byname[key]["fields"][1])
                 and byname[key]["data"] == before, "Expected current daily source script: " + key)
            result[key]["data"] = after
            report.append({"path": key, "before": sha(before), "after": sha(after)})
    need({e["name"] for e in output} == set(byname), "Entry set changed")
    for item in output:
        old = byname[item["name"]]
        need(item["fields"] == old["fields"], "Metadata changed")
        if item["name"] not in {c["path"] for c in report}:
            need(item["data"] == old["data"], "Unrelated file changed")
    components.archive.table(output)
    return output, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-ramdisk", type=Path, required=True)
    parser.add_argument("--daily-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    output = args.output.resolve()
    need(ROOT != output and ROOT not in output.parents and
         not args.output.exists() and not args.output.is_symlink() and args.output.parent.is_dir(),
         "Use a new private output directory outside this checkout")
    need(args.daily_ramdisk.is_file() and not args.daily_ramdisk.is_symlink(),
         "Expected a regular daily ramdisk")
    source = components.read_input(
        {"path": str(args.daily_ramdisk.resolve()), "sha256": args.daily_sha256}, Path("/"))
    need(source.startswith(b"\x1f\x8b"), "Expected a gzip daily ramdisk")
    with gzip.GzipFile(fileobj=io.BytesIO(source)) as stream:
        packed = stream.read(components.archive.MAX_BYTES + 1)
    need(len(packed) <= components.archive.MAX_BYTES, "Expanded ramdisk exceeds bound")
    original = components.archive.entries(packed)
    rows, changes = transform(original)
    for _, script in scripts().values():
        subprocess.run(["sh", "-n"], input=script, check=True, capture_output=True)
    packed = components.pack(rows)
    decoded = components.archive.entries(packed)
    semantic = lambda items: [(e["name"], e["fields"][1:6], e["data"]) for e in items]
    need(semantic(decoded) == semantic(rows), "CPIO readback mismatch")
    compressed = gzip.compress(packed, mtime=0)
    output.mkdir(mode=0o700)
    (output / "ramdisk.cpio.gz").write_bytes(compressed)
    report = dict(mode="first-install", dailyInputSha256=sha(source), outputSha256=sha(compressed),
                  changedEntries=changes, totalEntries=len(rows), initialInstallOnly=True,
                  loaderEntryName="envdaily", startupDeadlineSeconds=600, healthLeaseSeconds=120,
                  initialSessionCapSeconds=3600, dailySessionCapChanged=False,
                  sharedStateRequired=False, sharedStateCreated=False,
                  dailyVoiceStarted=False, containsOemFilesAndOwnerKey=True,
                  deviceAccess=False, runtimeTested=False, deployEligible=False)
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in
                     ("mode", "outputSha256", "totalEntries", "deviceAccess", "deployEligible")}))


if __name__ == "__main__":
    main()
