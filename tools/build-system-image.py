#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Apply the file-backed startup changes to a private framework system image.

The original device's own migration helper is extracted locally. No DRM keys,
accounts, identity blobs or data partitions are read or copied by this builder.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from dissect.extfs import ExtFS

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


framework = load('framework', 'build-framework-images.py')
board = load('board', 'render-board-overlays.py')
vndk = load('vndk', 'extract-vndk30.py')
need = framework.need
READ_PATHS = ('/system/etc/init/hw/init.rc', '/system/etc/init/apexd.rc',
              '/system/etc/init/vold.rc', '/system/etc/init/bpfloader.rc', '/system/build.prop')
DISABLED = ('update_engine', 'update_verifier', 'snapuserd', 'recovery-refresh', 'recovery-persist', 'mtectrl')


def render(inputs, migration):
    need(set(inputs) == set(READ_PATHS), 'Unexpected system input set')
    changes = {}
    for name in DISABLED:
        changes['/system/etc/init/' + name + '.rc'] = {
            'data': b'# Disabled for isolated fileboot bring-up.\n', 'template': None}
    path = '/system/etc/init/hw/init.rc'
    native = '\n'.join(line for line in inputs[path].decode().splitlines() if not any(s in line for s in
        ('exec_start update_verifier', 'vdc checkpoint markBootAttempt',
         'import /vendor/etc/init/hw/init.bsp-mtk.rc'))) + '\n'
    for name in READ_PATHS[:4]:
        text = native if name == path else inputs[name].decode()
        data = ('\n'.join(line for line in text.splitlines()
                          if not line.strip().startswith('reboot_on_failure ')) + '\n').encode()
        changes[name] = {'data': data, 'template': None}
    changes['/init.mitv.rc'] = {'template': '/init.environ.rc', 'data': (
        '# Retain board Wi-Fi and audio configuration only.\n'
        'import /vendor/etc/init/hw/init.mt9653.usb.rc\n'
        'import /vendor/etc/init/hw/init.wifi.rc\n'
        'import /vendor/etc/init/hw/init.bsp.audio.rc\n'
        'on early-init\n    setprop ro.codex.fileboot 1\n    setprop ro.crypto.state unsupported\n').encode()}
    need(migration.startswith(b'#!/') and 0 < len(migration) <= 65536, 'Invalid OEM migration script input')
    changes['/system/bin/move_widevine_data.sh'] = {'data': migration, 'template': '/system/bin/sh'}
    changes['/perm'] = {'data': b'', 'template': '/mnt'}
    props = board.properties(inputs['/system/build.prop'].decode(), {
        'ro.secure': '0', 'ro.debuggable': '1', 'ro.adb.secure': '1', 'ro.codex.fileboot': '1',
        'ro.crypto.state': 'unsupported', 'ro.crypto.type': 'none'})
    changes['/system/build.prop'] = {'data': props.encode(), 'template': None}
    return changes


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('framework-system', 'stock-system', 'dump-erofs', 'mkfs-erofs', 'fsck-erofs', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--framework-system-sha256', required=True)
    p.add_argument('--vndk-apex-sha256', required=True)
    args = p.parse_args()
    os.umask(0o077)
    need(not args.output.exists(), 'Use a new output directory')
    image_inputs = framework.inputs
    state = image_inputs.check_image(args.framework_system, args.framework_system_sha256)
    stock_state = image_inputs.check_image(args.stock_system, vndk.STOCK_SHA)
    def read(path):
        return subprocess.check_output([str(args.dump_erofs), '--cat', '--path=' + path,
                                        str(args.framework_system)], stderr=subprocess.PIPE, timeout=60)
    need(framework.sha(read('/system/apex/com.android.vndk.v30.apex')) == args.vndk_apex_sha256,
         'Framework does not contain the expected local VNDK APEX')
    files = {path: read(path) for path in READ_PATHS}
    with args.stock_system.open('rb') as stream:
        migration = ExtFS(stream).get('/system/bin/move_widevine_data.sh').open().read(65537)
    changes = render(files, migration)
    args.output.mkdir(parents=True)
    report = framework.rebuild(args.framework_system.resolve(), args.output / 'system-fileboot.erofs',
        changes, args.dump_erofs.resolve(), args.mkfs_erofs.resolve(), args.fsck_erofs.resolve())
    need(image_inputs.fingerprint(args.framework_system) == state and
         image_inputs.fingerprint(args.stock_system) == stock_state, 'Original inputs changed')
    report.update(frameworkSystemSha256=args.framework_system_sha256, stockSystemSha256=vndk.STOCK_SHA,
                  vndkApexSha256=args.vndk_apex_sha256, oemMigrationHelperSha256=framework.sha(migration),
                  dataPartitionsRead=False, drmKeysRead=False, originalImagesChanged=False,
                  containsThirdPartyFiles=True, runtimeOverlaysStillRequired=True,
                  deviceAccess=False, runtimeTested=False, deployEligible=False)
    (args.output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('pathsVerified', 'regularFilesReadBack', 'outputSha256', 'deployEligible')}))


if __name__ == '__main__':
    main()
