#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Inspect freeguy's stopped B console or remove this project's boot arguments.

Console modes require an already stopped bootloader. The verify-persisted mode
reads physical storage over ADB from the accepted Google runtime. Never resets,
flashes, changes slots, increments retry counters, installs a loader or replays
an entire environment. The profile and UART output remain private.
"""
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import select
import stat
import struct
import subprocess
import termios
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
ENV_SIZE = 65536
PARTITION_SIZE = 16777216
STOCK_BOOT_SHA256 = "e9f89733f586648d6af0784d5b4d233da9ba2d7f163bd275c427a454d3224da6"

SUFFIX = " rdinit=/codex-no-rdinit rootfstype=f2fs rootflags=norecovery"
REPLACE = {
    "root=/dev/mmcblk0p42": "root=/dev/ram",
    "init=/local/tmp/rd-root/envdaily": "init=/init",
    "ro": "rw",
    "loglevel=7": "loglevel=0",
}
READS = {
    "version", "md.b 0x1c040480 1", "md.b 0x1c040400 1",
    "printenv bootargs", "printenv makedata factory_mode devicestate",
}


def need(value, message):
    if not value:
        raise ValueError(message)


def read_profile(path):
    need(path.is_file() and not path.is_symlink() and path.stat().st_size < 65536,
         "Expected a private profile file")
    profile = json.loads(path.read_text())
    need(isinstance(profile, dict) and profile.get("device") == "freeguy" and
         profile.get("model") == "L75MA-SPL" and
         profile.get("bootloader") == "01.01.250225",
         "Only the investigated L75MA-SPL/freeguy firmware is supported")
    hashes = profile.get("normalizedBootargsSha256")
    need(isinstance(hashes, list) and 1 <= len(hashes) <= 4 and
         all(isinstance(h, str) and re.fullmatch("[0-9a-f]{64}", h) for h in hashes),
         "Owner's normalized stock bootargs hashes required")
    return hashes


def private_output(path):
    resolved = path.resolve()
    need(ROOT != resolved and ROOT not in resolved.parents,
         "Private recovery output must stay outside the repository")
    need(not path.exists() and not path.is_symlink() and path.parent.is_dir(),
         "Use a new private output directory")


def has_prompt(data):
    return re.search(rb"(?:^|[\r\n])freeguy# *\Z", data) is not None


def words(value):
    need(isinstance(value, str) and 0 < len(value) < 2048, "Invalid bootargs length")
    need(all(32 <= ord(c) < 127 for c in value), "Non-ASCII bootargs")
    tokens = value.split(" ")
    need(all(tokens), "Unexpected bootargs whitespace")
    keys = [t.split("=", 1)[0] for t in tokens if "=" in t]
    need(len(keys) == len(set(keys)), "Duplicate bootarg key")
    return tokens


def normalized(value):
    tokens = words(value)
    reasons = [t for t in tokens if t.startswith("androidboot.bootreason=")]
    levels = [t for t in tokens if t.startswith("lastblval=")]
    need(len(reasons) == 1 and reasons[0] in
         ("androidboot.bootreason=cold", "androidboot.bootreason=reboot"),
         "Unrecognized boot reason")
    need(len(levels) == 1 and re.fullmatch(r"lastblval=[0-9]{1,3}", levels[0])
         and int(levels[0].split("=")[1]) <= 255, "Invalid brightness field")
    return " ".join(t for t in tokens if t not in reasons + levels)


def fingerprint(value):
    return hashlib.sha256(normalized(value).encode("ascii")).hexdigest()


def restore_plan(value, allowed_stock_hashes):
    """Return an exact restoration or None for an already-stock configuration."""
    tokens = words(value)
    if fingerprint(value) in allowed_stock_hashes:
        need("root=/dev/ram" in tokens and "init=/init" in tokens and
             "rw" in tokens and "loglevel=0" in tokens and
             not any(t.startswith(("rdinit=", "rootfstype=", "rootflags="))
                     for t in tokens), "Profile does not describe stock startup")
        return None
    need(value.endswith(SUFFIX) and value.count(SUFFIX) == 1,
         "Not this project's exact temporary startup")
    base = value[:-len(SUFFIX)]
    original = words(base)
    for source, target in REPLACE.items():
        need(original.count(source) == 1 and target not in original,
             "Unexpected startup substitution")
    restored = " ".join(REPLACE.get(t, t) for t in original)
    need(fingerprint(restored) in allowed_stock_hashes,
         "Restored configuration does not match the owner's stock profile")
    need(restore_plan(restored, allowed_stock_hashes) is None,
         "Restoration failed stock validation")
    return restored


def encode_setenv(value):
    words(value)
    # mboot's actual parser was tested with this escaping and chunked input.
    # Avoid quoting the whole value: this firmware's parser preserves quotes.
    encoded = "setenv bootargs " + "".join(
        "\\" + c if c in "\\';$" else c for c in value)
    need(len(encoded) < 2048, "Console command exceeds mboot input size")
    return encoded


def extract_bootargs(reply):
    matches = re.findall(rb"(?:^|\n)bootargs=([^\r\n]*)", reply)
    need(len(matches) == 1, "Ambiguous or missing bootargs response")
    value = matches[0].decode("ascii")
    words(value)
    return value


def validate_console(replies):
    need(b"g43964869 (Feb 25 2025" in replies["version"],
         "Unsupported bootloader; no write")
    need(re.search(rb"1c040480:\s+21\s", replies["md.b 0x1c040480 1"]),
         "Original B selection is not active; no write")
    need(re.search(rb"1c040400:\s+00\s", replies["md.b 0x1c040400 1"]),
         "Retry state changed; no write")
    flags = replies["printenv makedata factory_mode devicestate"].replace(b"\r", b"")
    parsed = re.findall(rb"(?:^|\n)(makedata|factory_mode|devicestate)=([^\n]*)", flags)
    need(len(parsed) == 3 and dict(parsed) ==
         {b"makedata": b"true", b"factory_mode": b"0", b"devicestate": b"lock"},
         "Stock initialization or lock state changed; no write")


class Console:
    def __init__(self, port, output):
        need(re.fullmatch(r"/dev/(?:cu\.[A-Za-z0-9._-]+|ttyUSB[0-9]+|ttyACM[0-9]+)", port),
             "Specify a local serial character device")
        need(stat.S_ISCHR(os.stat(port, follow_symlinks=False).st_mode),
             "Serial path is not a character device")
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_NOFOLLOW)
        self.original = None
        try:
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            self.original = termios.tcgetattr(self.fd)
            settings = termios.tcgetattr(self.fd)
            settings[0] = settings[1] = settings[3] = 0
            settings[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            settings[4] = settings[5] = termios.B115200
            settings[6][termios.VMIN] = settings[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, settings)
        except BaseException:
            self.close()
            raise
        self.output, self.index, self.authorized_write = output, 0, None

    def handshake(self):
        # A newline is harmless in an idle console. No command is sent until
        # this connection has received a fresh, exact bootloader prompt.
        os.write(self.fd, b"\r")
        data = bytearray()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if select.select([self.fd], [], [], .1)[0]:
                chunk = os.read(self.fd, 65536)
                need(chunk, "Serial connection closed")
                data.extend(chunk)
                need(len(data) <= 2 * 1024**2, "Unexpected serial output")
                if has_prompt(data):
                    break
        (self.output / "handshake.bin").write_bytes(data)
        need(has_prompt(data) and
             b"Starting kernel" not in data,
             "Stop the B bootloader first; no command was sent")

    def send(self, data):
        offset, deadline = 0, time.monotonic() + 5
        while offset < len(data):
            need(time.monotonic() < deadline, "Serial write timed out")
            if select.select([], [self.fd], [], .1)[1]:
                try:
                    sent = os.write(self.fd, data[offset:])
                except BlockingIOError:
                    continue
                need(sent > 0, "Serial write stopped")
                offset += sent

    def close(self):
        if self.fd is not None:
            try:
                if self.original is not None:
                    termios.tcsetattr(self.fd, termios.TCSANOW, self.original)
            finally:
                os.close(self.fd)
                self.fd = None

    def command(self, command):
        need(command in READS or (self.authorized_write is not None and
             command in (self.authorized_write, "saveenv")),
             "Command outside the recovery plan")
        data = bytearray()
        payload = (command + "\r").encode("ascii")
        for offset in range(0, len(payload), 32):
            self.send(payload[offset:offset + 32])
            time.sleep(.03)
            while select.select([self.fd], [], [], 0)[0]:
                chunk = os.read(self.fd, 65536)
                need(chunk, "Serial connection closed")
                data.extend(chunk)
                need(len(data) <= 2 * 1024**2, "Unexpected serial output size")
        deadline = time.monotonic() + 30
        while not has_prompt(data) and time.monotonic() < deadline:
            if select.select([self.fd], [], [], .1)[0]:
                chunk = os.read(self.fd, 65536)
                need(chunk, "Serial connection closed")
                data.extend(chunk)
                need(len(data) <= 2 * 1024**2, "Unexpected serial output size")
        (self.output / f"{self.index:02}.bin").write_bytes(data)
        self.index += 1
        need(has_prompt(data) and b"Starting kernel" not in data and
             not re.search(rb"Unknown command|Command too long|Too many args", data),
             "No verified stopped bootloader response; no further commands")
        return bytes(data)


def recover(console, allowed, write=False):
    replies = {cmd: console.command(cmd) for cmd in (
        "version", "md.b 0x1c040480 1", "md.b 0x1c040400 1",
        "printenv makedata factory_mode devicestate")}
    validate_console(replies)
    before = extract_bootargs(console.command("printenv bootargs"))
    target = restore_plan(before, allowed)
    need(extract_bootargs(console.command("printenv bootargs")) == before,
         "Boot configuration changed during inspection")
    report = dict(alreadyStock=target is None, restorationAvailable=target is not None,
                  environmentSaved=False, rebootIssued=False, firmwareWritten=False,
                  persistentReadbackVerified=False, subsequentBootVerified=False,
                  beforeSha256=hashlib.sha256(before.encode()).hexdigest())
    if write and target is not None:
        console.authorized_write = encode_setenv(target)
        console.command(console.authorized_write)
        need(extract_bootargs(console.command("printenv bootargs")) == target,
             "RAM readback mismatch; not saved")
        reply = console.command("saveenv")
        need(re.search(rb"(?:^|[\r\n])done(?:[\r\n]|$)", reply, re.I) and
             not re.search(rb"\b(?:error|failed|failure)\b", reply, re.I),
             "Save completion not confirmed; do not reboot")
        need(extract_bootargs(console.command("printenv bootargs")) == target,
             "RAM configuration changed after save")
        report.update(environmentSaved=True,
                      restoredSha256=hashlib.sha256(target.encode()).hexdigest())
    return report


def stored_bootargs(raw):
    need(len(raw) == ENV_SIZE and
         struct.unpack_from("<I", raw)[0] == zlib.crc32(raw[4:]),
         "Stored environment size or CRC mismatch")
    end = raw.find(b"\0\0", 4)
    need(end > 4 and not any(raw[end + 2:]), "Invalid environment terminator or padding")
    entries = raw[4:end].split(b"\0")
    keys = [e.split(b"=", 1)[0] for e in entries]
    need(len(entries) <= 1024 and all(b"=" in e and k for e, k in zip(entries, keys))
         and len(set(keys)) == len(keys) and keys.count(b"bootargs") == 1,
         "Missing, duplicate or malformed environment entry")
    value = entries[keys.index(b"bootargs")].split(b"=", 1)[1].decode("ascii")
    words(value)
    return value


def check_partition(raw, allowed, reader=None):
    need(len(raw) == PARTITION_SIZE, "Incomplete physical environment read")
    if reader is None:
        from dissect.extfs import ExtFS
        reader = ExtFS
    fs = reader(io.BytesIO(raw))
    files = []
    for name in ("uboot.env", "uboot.env.bak"):
        node = fs.get("/" + name)
        need(stat.S_ISREG(node.inode.i_mode) and node.inode.i_links_count == 1 and
             node.size == ENV_SIZE, "Unexpected physical environment file")
        content = node.open().read()
        value = stored_bootargs(content)
        need(restore_plan(value, allowed) is None,
             "Physical environment still contains temporary boot arguments")
        files.append(dict(name=name, sha256=hashlib.sha256(content).hexdigest(),
                          crcValid=True, stockProfileMatched=True))
    return dict(partitionSha256=hashlib.sha256(raw).hexdigest(), files=files)


def verify_persisted(adb_path, device, allowed):
    """Read physical storage under the accepted Google runtime; never write it."""
    need(re.fullmatch(r"[A-Za-z0-9_.:-]+", device), "Invalid explicit ADB selector")
    adb = [str(adb_path), "-s", device]
    def shell(command, timeout=30):
        return subprocess.check_output(adb + ["shell", command], text=True,
                                       stderr=subprocess.PIPE, timeout=timeout).strip()
    def identity():
        props = [shell("getprop " + name) for name in
                 ("ro.product.device", "ro.build.version.release", "ro.boot.slot_suffix",
                  "ro.bootloader", "sys.boot_completed")]
        need(props == ["ladybird", "14", "_b", "01.01.250225", "1"],
             "Accepted Google runtime on original B is required")
        boot = shell("cat /proc/sys/kernel/random/boot_id")
        need(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot),
             "Invalid boot identity")
        for name, path, size in (("uenv", "/dev/block/mmcblk0p3", PARTITION_SIZE),
                                 ("boot_b", "/dev/block/mmcblk0p26", 78643200)):
            actual = shell("readlink -f /dev/block/by-name/" + name +
                           "; blockdev --getsize64 /dev/block/by-name/" + name +
                           "; blockdev --getro /dev/block/by-name/" + name)
            need(actual == path + "\n" + str(size) + "\n1",
                 "Physical read-only partition identity mismatch")
        digest = shell("sha256sum /dev/block/by-name/boot_b", 120).split()
        need(len(digest) == 2 and digest[0] == STOCK_BOOT_SHA256 and
             digest[1] == "/dev/block/by-name/boot_b", "Original B boot image changed")
        return boot
    before = identity()
    def read():
        raw = subprocess.check_output(adb + ["exec-out", "cat", "/dev/block/by-name/uenv"],
                                      stderr=subprocess.PIPE, timeout=60)
        need(len(raw) == PARTITION_SIZE, "Incomplete physical environment read")
        return raw
    raw = read()
    report = check_partition(raw, allowed)
    need(read() == raw, "Physical environment changed during verification")
    need(identity() == before, "Boot identity changed during verification")
    report.update(persistentReadbackVerified=True, subsequentBootVerified=False,
                  coldPowerTested=False, deviceWritesIssued=False,
                  originalBBootImageMatched=True, blockReadOnly=True, bootId=before)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["inspect", "restore", "verify-persisted"])
    parser.add_argument("--port")
    parser.add_argument("--adb", type=Path)
    parser.add_argument("--device")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    hashes = read_profile(args.profile)
    private_output(args.output)
    if args.mode == "verify-persisted":
        need(args.adb and args.device and not args.port, "Specify --adb and --device, not --port")
        os.umask(0o077)
        args.output.mkdir(mode=0o700)
        report = verify_persisted(args.adb, args.device, hashes)
        (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(dict(persistentReadbackVerified=True, filesVerified=len(report["files"]),
                              deviceWritesIssued=False, subsequentBootVerified=False,
                              coldPowerTested=False)))
        return
    need(args.port and not args.adb and not args.device, "Specify --port for console operations")
    os.umask(0o077)
    args.output.mkdir()
    console = Console(args.port, args.output)
    try:
        console.handshake()
        report = recover(console, hashes, write=args.mode == "restore")
        (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
    finally:
        console.close()


if __name__ == "__main__":
    main()
