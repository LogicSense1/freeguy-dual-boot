#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check and inventory local first-install files. No ADB, UART or device writes.

The plan is private and is not an executable installation script. Same-device
provenance and recoverability require independent evidence, not just hashes.
"""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime_root", ROOT / "tools/assemble-runtime-root.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
spec = importlib.util.spec_from_file_location("first_install", ROOT / "tools/build-first-install-root.py")
first = importlib.util.module_from_spec(spec)
spec.loader.exec_module(first)
need = runtime.need
BASE = "/data/local/tmp/codex-gtv-fileboot-v1"
LOADER = "/data/local/tmp/rd-root"
SHA = re.compile(r"[0-9a-f]{64}")
IMAGE_NAMES = {name for name, _, _, _ in runtime.LAYOUT}
PRIVATE_NAMES = {name for name, _, _, mode in runtime.LAYOUT if mode == "rw"}
LIMIT = 8 * 1024**3
BINDING_SCHEMES = ("freeguy-emmc-cid-serial-v1", "freeguy-android-serial-v1")
AUDIT_CASES = {
    "normal", "partial-writes", "not-pid1", "extra-arg", "wrong-fs", "writable-root",
    "tmpfs-failed", "create-failed", "write-failed", "symlink-failed", "proc-failed",
    "cmdline-failed", "pivot-failed", "oldroot-detach-failed", "exec-failed",
    "sysfs-failed", "wrong-partition", "env-mount-failed", "bad-env-crc",
    "bad-backup-crc", "backup-other-trial", "env-open-failed", "env-not-regular",
    "env-wrong-size", "partial-env-reads", "readonly-env", "backup-write-failed",
    "main-write-failed", "backup-fsync-failed", "main-fsync-failed",
    "backup-readback-failed", "main-readback-failed",
} | {"powercut-" + str(i) for i in range(1, 33)}


def read_json(path):
    need(path.is_file() and not path.is_symlink() and path.stat().st_size <= 2 * 1024**2,
         "Expected a bounded regular JSON input")
    return json.loads(path.read_bytes())


def checked_file(record, parent, limit=LIMIT):
    need(isinstance(record, dict) and set(record) == {"path", "bytes", "sha256"},
         "File record requires path, bytes and sha256")
    need(isinstance(record["path"], str) and "\0" not in record["path"]
         and "\n" not in record["path"], "Invalid local file path")
    need(type(record["bytes"]) is int and 0 < record["bytes"] <= limit
         and isinstance(record["sha256"], str) and SHA.fullmatch(record["sha256"]),
         "Invalid size or SHA-256")
    path = parent / record["path"]
    need(path.is_file() and not path.is_symlink(), "Expected a regular local input")
    # Reject final-component symlinks and bind the hash to one opened inode.
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
        before = os.fstat(source.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
             and before.st_size == record["bytes"], "Unexpected input size or hard link")
        h = hashlib.sha256()
        for data in iter(lambda: source.read(4 * 1024**2), b""):
            h.update(data)
        after = os.fstat(source.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    need(identity(before) == identity(after) == identity(path.stat()),
         "Local input changed while hashing")
    need(h.hexdigest() == record["sha256"], "Local file hash mismatch")
    return dict(source=str(path.resolve()), bytes=before.st_size, sha256=h.hexdigest())


def check_root(data, image_hashes, voice):
    need(data.startswith(b"\x1f\x8b"), "Expected gzip setup root")
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        packed = stream.read(runtime.archive.MAX_BYTES + 1)
    rows = runtime.archive.entries(packed)
    runtime.archive.table(rows)
    entries = {row["name"]: row for row in rows}
    mounts, index = runtime.mount_files(image_hashes)
    scripts = first.scripts()
    required = {"codex/mount-images.sh": mounts, "codex/immutable.sha256": index,
                **{name: value[1] for name, value in scripts.items()}}
    for prefix in ("", "first_stage_ramdisk/"):
        for name, expected in required.items():
            row = entries.get(prefix + name)
            need(row is not None and stat.S_ISREG(row["fields"][1]) and row["data"] == expected,
                 "Setup root does not match source and image plan")
        key = entries.get(prefix + "codex/host-adb-key")
        need(key is not None and stat.S_ISREG(key["fields"][1]), "Missing owner ADB key")
        runtime.validate_adb_key(key["data"])
        index = entries.get(prefix + "codex/autovoice/payload.sha256")
        need(index is not None and stat.S_ISREG(index["fields"][1]), "Missing voice index")
        lines = [line.split() for line in index["data"].decode().splitlines()]
        need(all(len(line) == 2 and SHA.fullmatch(line[0]) for line in lines)
             and len({line[1] for line in lines}) == len(lines), "Invalid voice index")
        hashes = {name: digest for digest, name in lines}
        for name, record in voice.items():
            need(hashes.get(name) == record["sha256"], "External voice dependency mismatch")
    return rows


def plan(inputs, parent, known_binding, known_scheme):
    need(set(inputs) == {"version", "deviceBinding", "deviceBindingScheme",
                         "images", "voice", "setupRoot", "setupLoader"},
         "Unexpected first-install input fields")
    need(type(inputs["version"]) is int and inputs["version"] == 1,
         "Unsupported input version")
    need(isinstance(known_binding, str) and SHA.fullmatch(known_binding)
         and inputs["deviceBinding"] == known_binding, "Same-device binding mismatch")
    need(known_scheme in BINDING_SCHEMES and inputs["deviceBindingScheme"] == known_scheme,
         "Device binding scheme mismatch")
    need(isinstance(inputs["images"], dict) and set(inputs["images"]) == IMAGE_NAMES,
         "Exactly eighteen backing images are required")
    need(isinstance(inputs["voice"], dict) and set(inputs["voice"]) ==
         {"oem.apk", "libmibluetooth_jni.so"}, "Exactly two external voice dependencies required")
    files = []
    image_hashes = {}
    for name, fs, mount, access in runtime.LAYOUT:
        record = checked_file(inputs["images"][name], parent)
        need(record["bytes"] % 4096 == 0, "Backing image size must be block-aligned")
        if access == "ro":
            image_hashes[name] = record["sha256"]
        files.append(dict(record, target=BASE + "/" + name, kind="backing-image",
                          writable=access == "rw", filesystem=fs, mount=mount,
                          setupMode="0600", overwriteAllowed=False))
    voice = {}
    for name in sorted(inputs["voice"]):
        record = checked_file(inputs["voice"][name], parent, 128 * 1024**2)
        voice[name] = record
        files.append(dict(record, target=BASE + "/voice-assets-v1/" + name,
                          kind="voice-dependency", setupMode="0600", overwriteAllowed=False))
    root = checked_file(inputs["setupRoot"], parent, runtime.archive.MAX_BYTES)
    data = Path(root["source"]).read_bytes()
    need(hashlib.sha256(data).hexdigest() == root["sha256"], "Setup root changed")
    rows = check_root(data, image_hashes, voice)
    loader = checked_file(inputs["setupLoader"], parent, 128 * 1024**2)
    manifest_path = Path(loader["source"]).parent / "manifest.json"
    audit_path = manifest_path.parent / "audit.json"
    manifest, audit = read_json(manifest_path), read_json(audit_path)
    _, _, index = runtime.archive.table(rows)
    need(manifest.get("sha256") == loader["sha256"]
         and manifest.get("inputRamdiskSha256") == root["sha256"]
         and manifest.get("entries") == index, "Loader does not describe this setup root")
    cases = audit.get("cases")
    need(isinstance(cases, list) and len(cases) == 64
         and all(isinstance(case, dict) for case in cases)
         and {case.get("case") for case in cases} == AUDIT_CASES, "Incomplete audit case set")
    need(audit.get("passed") is True and audit.get("sha256") == loader["sha256"]
         and audit.get("originalEntriesVerified") == len(rows)
         and audit.get("liveTested") is False,
         "Matching complete offline loader audit required")
    for name in ("internal-env-inspect.h", "internal-env-transform.c", "internal-env-once.h"):
        expected = hashlib.sha256((ROOT / "runtime" / name).read_bytes()).hexdigest()
        need(manifest.get("componentSources", {}).get(name) == expected
             and audit.get("componentSources", {}).get(name) == expected,
             "Loader component source drift")
    source_sha = hashlib.sha256((ROOT / "runtime/loader.c").read_bytes()).hexdigest()
    need(manifest.get("sourceSha256") == source_sha == audit.get("sourceSha256"),
         "Loader source drift")
    files.append(dict(loader, target=LOADER + "/envdaily", kind="setup-loader",
                      setupMode="0700", overwriteAllowed=False))
    need(len(files) == 21 and len({r["target"] for r in files}) == 21, "Unexpected destination set")
    total = sum(row["bytes"] for row in files)
    need(total < 32 * 1024**3, "Unexpected first-install total")
    return dict(version=1, mode="first-install-inventory", deviceBinding=known_binding,
                deviceBindingScheme=known_scheme,
                directories=[LOADER, LOADER + "/scratch", BASE, BASE + "/voice-assets-v1"],
                directoryMode="0700",
                markers=[dict(target=BASE + "/marker", content="codex-freeguy-fileboot-v1\n",
                              setupMode="0600", overwriteAllowed=False)],
                mustBeAbsent=[LOADER, BASE, "/data/local/codex-system-default-v1",
                              "/data/vendor/codex-switch-proxy-v1"],
                files=files, totalBytes=total, freeSpaceReserveBytes=8 * 1024**3,
                deviceAccess=False, installAuthorized=False, runtimeVerified=False,
                filesystemsVerified=False, sameDeviceProvenanceVerified=False,
                createsSharedState=False, changesStartup=False, overwritesExistingFiles=False,
                setupRootSha256=root["sha256"], offlineLoaderCases=64,
                mutableFiles=sorted(PRIVATE_NAMES),
                warning="Private inventory only; source provenance, filesystem checks, current device identity, "
                        "storage and absence checks, serial undo and deployment remain separate requirements")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--known-device-binding", required=True,
                        help="Independently recorded owner-device binding, not copied from an unknown manifest")
    parser.add_argument("--known-device-binding-scheme", required=True, choices=BINDING_SCHEMES)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    resolved = args.output.resolve()
    need(ROOT != resolved and ROOT not in resolved.parents and
         not args.output.exists() and not args.output.is_symlink() and args.output.parent.is_dir(),
         "Use a new private output directory outside the checkout")
    record = plan(read_json(args.inputs), args.inputs.parent, args.known_device_binding,
                  args.known_device_binding_scheme)
    args.output.mkdir(mode=0o700)
    with (args.output / "plan.json").open("x") as out:
        json.dump(record, out, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    print(json.dumps({key: record[key] for key in
                      ("mode", "totalBytes", "deviceAccess", "installAuthorized")}))


if __name__ == "__main__":
    main()
