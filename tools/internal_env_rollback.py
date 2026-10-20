# SPDX-License-Identifier: Apache-2.0
"""Offline byte transformation only. No filesystem/device write or deployment.

Restore only the exact internal envread trial's bootargs in a CRC-valid
65536-byte environment. Keep every other environment entry byte-for-byte.
Do not use the factory .bak as an assumed stock environment.
"""
import struct
import zlib

SIZE = 65536
REPLACE = {
    b'root=/dev/mmcblk0p42': b'root=/dev/ram',
    b'init=/local/tmp/rd-root/envread': b'init=/init',
    b'ro': b'rw',
    b'loglevel=7': b'loglevel=0',
}
REMOVE = (b'rdinit=/codex-no-rdinit', b'rootfstype=f2fs', b'rootflags=norecovery')


def unpack(raw):
    if len(raw) != SIZE or struct.unpack('<I', raw[:4])[0] != zlib.crc32(raw[4:]):
        raise ValueError('Environment size or CRC mismatch')
    end = raw.find(b'\0\0', 4)
    if end < 4 or any(raw[end + 2:]):
        raise ValueError('Missing terminator or nonzero padding')
    entries = raw[4:end].split(b'\0')
    keys = [e.split(b'=', 1)[0] for e in entries]
    if any(b'=' not in e or not k for e, k in zip(entries, keys)) or len(set(keys)) != len(keys):
        raise ValueError('Malformed or duplicate environment entry')
    if keys.count(b'bootargs') != 1:
        raise ValueError('Missing bootargs')
    return entries


def pack(entries):
    body = b'\0'.join(entries) + b'\0\0'
    if len(body) > SIZE - 4:
        raise ValueError('Environment overflow')
    body = body.ljust(SIZE - 4, b'\0')
    return struct.pack('<I', zlib.crc32(body)) + body


def rollback(raw):
    entries = unpack(raw)
    index = next(i for i, e in enumerate(entries) if e.startswith(b'bootargs='))
    value = entries[index][9:]
    tokens = value.split(b' ')
    if not value or len(value) > 2047 or any(not t or any(c < 33 or c > 126 for c in t) for t in tokens):
        raise ValueError('Unexpected bootargs encoding')
    for source, dest in REPLACE.items():
        if tokens.count(source) != 1 or dest in tokens:
            raise ValueError('Not the exact trial')
    for source in REMOVE:
        if tokens.count(source) != 1:
            raise ValueError('Not the exact trial')
    for prefix in (b'root=', b'init=', b'loglevel=', b'rdinit=', b'rootfstype=', b'rootflags='):
        if sum(t.startswith(prefix) for t in tokens) != 1:
            raise ValueError('Ambiguous boot parameter')
    result = [REPLACE.get(t, t) for t in tokens if t not in REMOVE]
    out = entries.copy()
    out[index] = b'bootargs=' + b' '.join(result)
    encoded = pack(out)
    check = unpack(encoded)
    assert len(check) == len(entries)
    assert all(a == b for i, (a, b) in enumerate(zip(entries, check)) if i != index)
    return encoded
