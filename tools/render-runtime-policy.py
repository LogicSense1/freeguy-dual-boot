#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bind the fresh-data component policy to locally built artifacts. No device I/O."""
import argparse
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('hdmi-apk', 'label-apk', 'audio-adapter', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error('Use a new output file')
    values = {}
    for name, file in (('HDMI', args.hdmi_apk), ('LABEL', args.label_apk), ('AUDIO', args.audio_adapter)):
        if file.is_symlink() or not file.is_file() or file.stat().st_size > 16 * 1024 * 1024:
            p.error('Invalid local artifact: ' + name)
        raw = file.read_bytes()
        if name == 'AUDIO':
            if len(raw) < 52 or raw[:6] != b'\x7fELF\x01\x01' or struct.unpack_from('<HH', raw, 16) != (3, 40):
                p.error('Expected ARM32 audio adapter')
        else:
            with zipfile.ZipFile(file) as archive:
                if 'AndroidManifest.xml' not in archive.namelist():
                    p.error('Expected Android APK')
        values['@' + name + '_SHA256@'] = hashlib.sha256(raw).hexdigest()
    text = (ROOT / 'runtime/runtime-policy.sh.in').read_text()
    for key, value in values.items():
        if text.count(key) != 1:
            raise ValueError('Unexpected policy template')
        text = text.replace(key, value)
    os.umask(0o077)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as output:
        output.write(text)
    subprocess.run(['sh', '-n', args.output], check=True)
    print('Policy rendered and shell syntax checked; not deployed')


if __name__ == '__main__':
    main()
