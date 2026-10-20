#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare this TV's HyperOS remote bond for its isolated Google Bluetooth files.

Offline only. Reads private owner-supplied files and writes a new private
directory. It does not stop Bluetooth, install files or touch the TV.
"""
import argparse
import configparser
import hashlib
import io
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 1024 * 1024
MAC = re.compile(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}')
LOCAL_KEYS = ('LE_LOCAL_KEY_IRK', 'LE_LOCAL_KEY_IR', 'LE_LOCAL_KEY_DHK', 'LE_LOCAL_KEY_ER')
REMOTE_NAME = '小米蓝牙语音遥控器'


def need(condition, message):
    if not condition:
        raise ValueError(message)


def parse(raw):
    need(isinstance(raw, bytes) and 0 < len(raw) <= MAX_BYTES and b'\0' not in raw,
         'Invalid Bluetooth configuration input')
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(raw.decode('utf-8'))
    except (UnicodeError, configparser.Error) as error:
        # Parser exceptions can contain pairing data. Do not echo their text.
        raise ValueError('Malformed Bluetooth configuration') from None
    need(not parser.defaults() and parser.has_section('Adapter'), 'Expected explicit adapter section')
    address = parser.get('Adapter', 'Address', fallback='')
    need(MAC.fullmatch(address), 'Invalid adapter address')
    return parser


def merge(stock_raw, google_raw):
    stock, google = parse(stock_raw), parse(google_raw)
    need(stock['Adapter']['Address'].lower() == google['Adapter']['Address'].lower(),
         'Adapter identities differ; no migration')
    remotes = [s for s in stock.sections() if stock.get(s, 'Name', fallback='') == REMOTE_NAME]
    need(len(remotes) == 1 and MAC.fullmatch(remotes[0]), 'Expected one supported original remote')
    remote = remotes[0]
    bond = stock[remote]
    need(re.fullmatch(r'[0-9a-fA-F]{56}', bond.get('LE_KEY_PENC', '')),
         'Missing or malformed remote encryption record')
    descriptor = bond.get('HidDescriptor', '')
    need(len(descriptor) % 2 == 0 and re.fullmatch(r'[0-9a-fA-F]+', descriptor),
         'Missing or malformed HID descriptor')
    peers = [s for s in google.sections() if MAC.fullmatch(s)]
    need(all(s.lower() == remote.lower() for s in peers),
         'Other Google bonds require a separate migration review')
    for key in LOCAL_KEYS:
        value = stock['Adapter'].get(key, '')
        need(re.fullmatch(r'[0-9a-fA-F]{32}', value), 'Missing or malformed local adapter key')
        google['Adapter'][key] = value
    for peer in peers:
        google.remove_section(peer)
    google[remote] = dict(bond)
    output = io.StringIO()
    google.write(output)
    result = output.getvalue().encode()
    need(len(result) <= MAX_BYTES, 'Prepared configuration exceeds bound')
    return result


def read_private(path):
    need(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_BYTES,
         'Expected a bounded regular configuration file')
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
         (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
         'Input changed during reading')
    return raw


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('stock-config', 'google-config', 'google-backup', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    args = p.parse_args()
    out = args.output.resolve()
    need(ROOT != out and ROOT not in out.parents, 'Private pairing outputs must stay outside the repository')
    need(not out.exists(), 'Use a new output directory')
    os.umask(0o077)
    paths = {'stock': args.stock_config, 'conf': args.google_config, 'bak': args.google_backup}
    originals = {name: read_private(path) for name, path in paths.items()}
    prepared = {name: merge(originals['stock'], originals[name]) for name in ('conf', 'bak')}
    # Refuse a concurrent rewrite before emitting a complete preparation.
    need(all(read_private(path) == originals[name] for name, path in paths.items()),
         'Input changed during preparation')
    out.mkdir(parents=True, mode=0o700)
    for name in ('conf', 'bak'):
        for prefix, data in (('before.', originals[name]), ('bt_config.', prepared[name])):
            path = out / (prefix + name)
            with path.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            need(path.read_bytes() == data, 'Local output readback failed')
    sha = lambda data: hashlib.sha256(data).hexdigest()
    report = {'sourceSha256': {n: sha(data) for n, data in originals.items()},
              'preparedSha256': {n: sha(data) for n, data in prepared.items()},
              'copiedRemoteCount': 1, 'containsPairingKeys': True,
              'stockWritten': False, 'deviceAccess': False, 'installed': False,
              'physicalRemoteVerified': False}
    (out / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'prepared': True, 'containsPairingKeys': True,
                      'deviceAccess': False, 'installed': False}))


if __name__ == '__main__':
    main()
