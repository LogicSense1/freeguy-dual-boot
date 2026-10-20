#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Export the original B board's eMMC prefix to USB, then verify a host copy.

No internal storage writes, boot commands or environment changes. Requires an
already stopped supported B console and a new empty directory on FAT USB 0:1.
The exported files contain private device material; keep every output private.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import zlib

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("activation", ROOT / "tools/activate-first-entry.py")
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
recovery, need = a.recovery, a.need
save = a.planner.verify.stager.backup.save
PREFIX_SECTORS = 9277440
CHUNK_SECTORS = 131072
ADDRESS = "0x2c000000"
GEOMETRY_SHA = "5f7f13a924b880420bc60d35fc72340822c5817b485ab9668f3581fe5e50d022"


def geometry(raw):
    need(b"Partition Map for MMC device 0" in raw and b"Partition Type: EFI" in raw,
         "Expected original eMMC user-area partition map")
    rows = [(int(i), int(start, 16), int(end, 16), name.decode("ascii"))
            for i, start, end, name in re.findall(
                rb'^\s*(\d+)\s+(0x[0-9a-f]+)\s+(0x[0-9a-f]+)\s+"([^"]+)"', raw, re.M)]
    digest = hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()
    need(len(rows) == 42 and digest == GEOMETRY_SHA, "Partition layout differs from the tested board")
    return digest


def chunks():
    return [dict(name=f"disk-prefix-{i:03}.img", offset=start * 512,
                 blocks=min(CHUNK_SECTORS, PREFIX_SECTORS - start),
                 bytes=min(CHUNK_SECTORS, PREFIX_SECTORS - start) * 512)
            for i, start in enumerate(range(0, PREFIX_SECTORS, CHUNK_SECTORS))]


def commands(directory):
    need(re.fullmatch(r"/freeguy-backup-[a-z0-9]{8,32}", directory),
         "Use a new /freeguy-backup-<8..32 lowercase letters/digits> directory")
    allowed = set(recovery.READS) | {"mmc part", "usb start", "fatls usb 0:1 " + directory}
    for row in chunks():
        allowed.update((
            f"mmc read {ADDRESS} 0x{row['offset']//512:x} 0x{row['blocks']:x}",
            f"crc32 {ADDRESS} 0x{row['bytes']:x}",
            f"fatwrite usb 0:1 {ADDRESS} {directory}/{row['name']} 0x{row['bytes']:x}"))
    return allowed


class PrefixConsole(recovery.Console):
    def __init__(self, port, output, directory):
        self.allowed = commands(directory)
        super().__init__(port, output)

    def command(self, command):
        need(command in self.allowed, "Command outside the fixed read-to-USB plan")
        # Use the shared bounded transport, without exposing saveenv or a
        # caller-defined memory range through this subclass.
        self.authorized_write = command if command not in recovery.READS else None
        try:
            return super().command(command, timeout=180)
        finally:
            self.authorized_write = None


def capture(console, directory, allowed_profile, binding, scheme, output):
    allowed_commands = commands(directory)
    rows = chunks()
    record = dict(version=1, deviceBindingSha256=binding, deviceBindingScheme=scheme,
                  prefixBytes=PREFIX_SECTORS * 512, geometrySha256=GEOMETRY_SHA,
                  complete=False, pending=None, internalWritesIssued=False,
                  rebootIssued=False, hostReadbackVerified=False, chunks=[])
    def checkpoint():
        save(output / "capture.json", record)
    def command(text):
        need(text in allowed_commands, "Unexpected command")
        record["pending"] = text
        checkpoint()
        raw = console.command(text)
        need(not re.search(rb"\b(?:error|failed|failure|invalid)\b|not found", raw, re.I),
             "OEM command failed; stop without retry")
        record["pending"] = None
        checkpoint()
        return raw
    def guard():
        replies = {text: command(text) for text in a.console_tool.IDENTITY_READS}
        recovery.validate_console(replies)
        need(recovery.restore_plan(recovery.extract_bootargs(command("printenv bootargs")),
                                   allowed_profile) is None, "Require normal stock arguments")
    checkpoint()
    console.handshake()
    guard()
    geometry(command("mmc part"))
    command("usb start")
    listing = command("fatls usb 0:1 " + directory)
    need(re.search(rb"(?:^|[\r\n])\s*0 file\(s\),\s*2 dir\(s\)", listing),
         "USB directory is not empty or its listing is incomplete")
    for row in rows:
        guard()
        count, offset = row["blocks"], row["offset"] // 512
        read_command = f"mmc read {ADDRESS} 0x{offset:x} 0x{count:x}"
        raw = command(read_command)
        # This OEM build returns only the echoed command and prompt on a
        # successful read; it does not print upstream U-Boot's block count.
        need(raw.replace(b"\r", b"").strip() == (read_command + "\nfreeguy#").encode(),
             "Unexpected eMMC read response; USB write refused")
        raw = command(f"crc32 {ADDRESS} 0x{row['bytes']:x}")
        crc = re.findall(rb"==>\s*([0-9a-fA-F]{8})", raw)
        need(len(crc) == 1, "Missing or ambiguous RAM CRC")
        raw = command(f"fatwrite usb 0:1 {ADDRESS} {directory}/{row['name']} 0x{row['bytes']:x}")
        need(re.search(rb"\b" + str(row["bytes"]).encode() + rb" bytes written\b", raw),
             "USB write not acknowledged")
        record["chunks"].append(dict(row, ramCrc32=crc[0].decode().lower(), usbWriteAcknowledged=True))
        checkpoint()
        print(json.dumps(dict(chunks=len(record["chunks"]), total=len(rows))), flush=True)
    guard()
    geometry(command("mmc part"))
    record["complete"] = True
    checkpoint()
    return record


def verify(record, source, output, binding, scheme):
    recovery.private_output(output)
    a.binding(record, binding, scheme)
    expected = chunks()
    need(record.get("version") == 1 and record.get("complete") is True
         and record.get("pending") is None and record.get("internalWritesIssued") is False
         and record.get("rebootIssued") is False and record.get("hostReadbackVerified") is False
         and record.get("prefixBytes") == PREFIX_SECTORS * 512
         and record.get("geometrySha256") == GEOMETRY_SHA
         and len(record.get("chunks", [])) == len(expected), "Incomplete prefix capture")
    need(source.is_dir() and not source.is_symlink(), "Expected the USB export directory")
    need({p.name for p in source.iterdir()} == {r["name"] for r in expected},
         "USB export contains missing or additional files")
    for row, want in zip(record["chunks"], expected):
        need(all(row.get(k) == v for k, v in want.items())
             and row.get("usbWriteAcknowledged") is True
             and isinstance(row.get("ramCrc32"), str)
             and re.fullmatch("[0-9a-f]{8}", row["ramCrc32"]), "Changed capture geometry or CRC")
    need(shutil.disk_usage(output.parent).free > PREFIX_SECTORS * 512 + 1024**3,
         "Insufficient space for an independent host copy")
    output.mkdir(mode=0o700)
    result = dict(version=1, deviceBindingSha256=binding, deviceBindingScheme=scheme,
                  prefixBytes=PREFIX_SECTORS * 512, chunks=[])
    for row in record["chunks"]:
        path = source / row["name"]
        need(path.is_file() and not path.is_symlink(), "Expected a regular USB file")
        before = path.lstat()
        need(before.st_size == row["bytes"] and before.st_nlink == 1, "USB file size or link mismatch")
        digest, crc, count = hashlib.sha256(), 0, 0
        target = output / row["name"]
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as inp, target.open("xb") as out:
            opened = os.fstat(inp.fileno())
            need(stat.S_ISREG(opened.st_mode) and
                 (opened.st_dev, opened.st_ino, opened.st_size, opened.st_nlink) ==
                 (before.st_dev, before.st_ino, before.st_size, 1),
                 "USB source changed before opening")
            while True:
                data = inp.read(4 * 1024**2)
                if not data:
                    break
                count += len(data)
                need(count <= row["bytes"], "USB file grew during read")
                digest.update(data)
                crc = zlib.crc32(data, crc)
                out.write(data)
            out.flush()
            os.fsync(out.fileno())
        after = path.lstat()
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        need(stamp(before) == stamp(after) and count == row["bytes"]
             and f"{crc:08x}" == row["ramCrc32"], "USB readback differs from captured RAM")
        file = dict(path=target.name, bytes=count, sha256=digest.hexdigest())
        a.inventory.checked_file(file, output, 64 * 1024**2)
        result["chunks"].append(dict(offset=row["offset"], file=file))
    a.check_backup(result, output, a.Inputs(), binding, scheme)
    save(output / "backup.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("capture", "verify"))
    p.add_argument("--known-device-binding", required=True)
    p.add_argument("--known-device-binding-scheme", choices=a.inventory.BINDING_SCHEMES, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--port")
    p.add_argument("--usb-directory")
    p.add_argument("--recovery-profile", type=Path)
    p.add_argument("--capture-record", type=Path)
    p.add_argument("--capture-record-sha256")
    p.add_argument("--source", type=Path)
    args = p.parse_args()
    need(a.inventory.SHA.fullmatch(args.known_device_binding), "Invalid device binding")
    recovery.private_output(args.output)
    os.umask(0o077)
    if args.action == "verify":
        need(args.capture_record and args.capture_record_sha256 and args.source
             and not any((args.port, args.usb_directory, args.recovery_profile)), "Verify takes local inputs only")
        record = a.planner.pinned(args.capture_record, args.capture_record_sha256)
        result = verify(record, args.source, args.output,
                        args.known_device_binding, args.known_device_binding_scheme)
        print(json.dumps(dict(hostReadbackVerified=True, chunks=len(result["chunks"]),
                              bytes=result["prefixBytes"], deviceAccess=False)))
        return
    need(args.port and args.usb_directory and args.recovery_profile
         and not any((args.capture_record, args.capture_record_sha256, args.source)), "Capture takes serial and USB arguments")
    commands(args.usb_directory)
    allowed = recovery.read_profile(args.recovery_profile)
    args.output.mkdir(mode=0o700)
    console = PrefixConsole(args.port, args.output, args.usb_directory)
    try:
        capture(console, args.usb_directory, allowed, args.known_device_binding,
                args.known_device_binding_scheme, args.output)
    finally:
        console.close()


if __name__ == "__main__":
    main()
