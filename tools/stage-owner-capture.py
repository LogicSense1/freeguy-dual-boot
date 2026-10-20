#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Stage or probe the owner collector on an otherwise unmodified stock setup.

Refuses an existing loader, Google installation or capture directory. Reuses
the first-install uploader and probe protocol. Never activates or reboots.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


owner, verify = load("build-owner-root"), load("verify-first-install")
stage, inventory = verify.stager, verify.inventory
need, sha = stage.need, lambda data: hashlib.sha256(data).hexdigest()
CAPTURE = "/data/local/tmp/freeguy-owner-capture-v1"
MARKER = inventory.LOADER + "/owner-capture-stage-v1"


def plan(stock_boot, capture_build, loader_build, binding, scheme):
    need(inventory.SHA.fullmatch(binding) and scheme in inventory.BINDING_SCHEMES,
         "Independently recorded owner binding required")
    for path in (stock_boot, capture_build / "owner-capture", loader_build / "loader"):
        need(path.is_file() and not path.is_symlink(), "Expected a regular private build input")
    need(stock_boot.stat().st_size == 75 * 1024**2, "Wrong original boot partition size")
    rows, source = owner.prepare(stock_boot.read_bytes(), capture_build)
    manifest = inventory.read_json(loader_build / "manifest.json")
    audit = inventory.read_json(loader_build / "audit.json")
    binary = loader_build / "loader"
    checked = inventory.checked_file(
        dict(path=str(binary.resolve()), bytes=binary.stat().st_size, sha256=manifest["sha256"]),
        Path("/"), 128 * 1024**2)
    expected_entries = owner.bootstrap.archive.table(rows)[2]
    need(manifest.get("rootKind") == "owner-capture" and manifest.get("ownerCapture") == source
         and manifest.get("entries") == expected_entries,
         "Loader does not contain the exact original root and audited collector")
    need(audit.get("passed") is True and audit.get("sha256") == checked["sha256"]
         and audit.get("originalEntriesVerified") == len(rows), "Loader audit mismatch")
    cases = audit.get("cases", [])
    need(len(cases) == 64 and {c.get("case") for c in cases} == inventory.AUDIT_CASES,
         "Incomplete outer-loader audit")
    source_hash = sha((ROOT / "runtime/loader.c").read_bytes())
    need(manifest.get("sourceSha256") == audit.get("sourceSha256") == source_hash,
         "Outer loader source changed")
    for name in ("internal-env-inspect.h", "internal-env-transform.c", "internal-env-once.h"):
        expected = sha((ROOT / "runtime" / name).read_bytes())
        need(manifest.get("componentSources", {}).get(name) ==
             audit.get("componentSources", {}).get(name) == expected, "Undo source changed")
    marker = "FREEGUY_OWNER_STAGE_V1\n" + checked["sha256"] + "\n"
    result = dict(
        mode="owner-capture-stage-plan", deviceBinding=binding, deviceBindingScheme=scheme,
        ownerCapture=source, directories=[inventory.LOADER],
        mustBeAbsent=[inventory.LOADER, inventory.BASE, CAPTURE,
                      "/data/local/codex-system-default-v1", "/data/vendor/codex-switch-proxy-v1"],
        files=[dict(checked, target=inventory.LOADER + "/envdaily",
                    kind="setup-loader", setupMode="0700")],
        markers=[dict(target=MARKER, content=marker, setupMode="0600")],
        totalBytes=checked["bytes"], freeSpaceReserveBytes=2 * 1024**3 + 84 * 1024**2)
    verify.probe_audit(result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("inspect", "stage", "probe"))
    for name in ("adb", "stock-boot", "capture-build", "loader-build", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--device", required=True)
    p.add_argument("--known-device-binding", required=True)
    p.add_argument("--known-device-binding-scheme", choices=inventory.BINDING_SCHEMES, required=True)
    p.add_argument("--staging-record", type=Path)
    p.add_argument("--staging-record-sha256")
    a = p.parse_args()
    os.umask(0o077)
    stage.backup.private_output(a.output)
    need(not a.output.exists() and not a.output.is_symlink() and a.output.parent.is_dir(),
         "Use a new private journal directory")
    record = plan(a.stock_boot, a.capture_build, a.loader_build,
                  a.known_device_binding, a.known_device_binding_scheme)
    device = stage.Device(a.adb, a.device, record)
    if a.action == "probe":
        need(a.staging_record and a.staging_record_sha256
             and inventory.SHA.fullmatch(a.staging_record_sha256), "Pin the completed staging journal")
        raw = a.staging_record.read_bytes()
        need(sha(raw) == a.staging_record_sha256, "Staging journal changed")
        previous = json.loads(raw)
        verify.check_journal(previous, record)
        device.guard()
        need(device.metadata(CAPTURE) is None, "Prior capture exists; do not reuse this entry")
        a.output.mkdir(mode=0o700)
        result = verify.verify(device, record, previous, a.output, execute=True)
    else:
        need(a.staging_record is None and a.staging_record_sha256 is None,
             "Staging evidence is only used by probe")
        a.output.mkdir(mode=0o700)
        result = stage.stage(device, record, a.output, apply=a.action == "stage")
    print(json.dumps(dict(complete=result["complete"], action=a.action,
                          startupChanged=False, rebootIssued=False,
                          probeExit=result.get("probeExit"))))


if __name__ == "__main__":
    main()
