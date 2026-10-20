#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare a private file-backed vendor image from the supported stock image.

Writes only a new local output directory. Does not mount filesystems, access a
TV, select slots or produce a flashable package. OEM-derived outputs stay local.
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
import subprocess
import sys

from dissect.extfs import ExtFS

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / file)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


board = module('board', 'render-board-overlays.py')
extract = module('extract', 'extract-board-inputs.py')
need = board.need
STOCK_SHA = '87f22567d736d8f4955fa29f7bcd493be03f5b78c7c215003dcff94b916400db'
PATHS = ('/etc/fstab.mt9653', '/etc/fstab_late', '/etc/init/hw/init.common.rc',
    '/etc/init/hw/init.mt9653.rc', '/etc/init/init.fusion.rc', '/etc/init/factory_svc.rc',
    '/etc/init/vendor_flash_recovery.rc', '/etc/init/android.hardware.boot@1.1-service.rc',
    '/bin/set_uuenv.sh', '/bin/update_wakeup_rc.sh', '/bin/init.product.sh', '/bin/dtv_svc.sh',
    '/etc/init/hw/init.mt9653.usb.rc', '/etc/init/init.insmod.rc',
    '/etc/selinux/vendor_sepolicy.cil', '/build.prop')


def render(inputs, cil):
    need(set(inputs) == set(PATHS), 'Unexpected vendor inputs')
    text = {name: data.decode('utf-8') for name, data in inputs.items()}
    need(cil and len(cil) < 16 * 1024**2 and b'\0' not in cil, 'Invalid compiled-input CIL')
    changes = {}
    for path in PATHS[:2]:
        changes[path] = '# Fileboot bring-up: all filesystems are pre-mounted image files.\n'
    blocked = {'makeuserdata', 'start_vab_update', 'stop_vab_update', 'set_mcu_ota',
               'bt-wakeuprc', 'f2fs-svc', 'ubootlog'}
    for path in PATHS[2:5]:
        value = board.strip_services(text[path], blocked)
        lines, skip = [], False
        for line in value.splitlines(True):
            if line.startswith('on '):
                skip = line.strip() == 'on mkfs_userdata'
            if skip or re.search(r'^\s*(mount_all |exec_start wait_for_keymaster|symlink /dev/block/|load_data_props\b|saverecoverylog\b|savepanicinfo\b|chmod 755 /vendor/factory/)', line):
                continue
            lines.append(line)
        changes[path] = ''.join(lines)
    for path in PATHS[5:8]:
        changes[path] = '# Disabled in fileboot: no factory, recovery flashing or boot-slot service.\n'
    for path in PATHS[8:10]:
        changes[path] = '#!/vendor/bin/sh\n# Trial state must not update persistent boot environment.\nexit 0\n'
    changes['/bin/init.product.sh'] = (
        '#!/vendor/bin/sh\n# Only the read-only remote wake configuration is used during bring-up.\n'
        'if [ "$1" = btwake ]; then\n    BT_WAKE=$(uuenv get wake_up_rc)\n'
        '    BT_MAC=${BT_WAKE:13}\n'
        '    case "$BT_MAC" in *NULL*) ;; *) setprop bluetooth.wake_up_rc "${BT_MAC//@/;}" ;; esac\n'
        'fi\nexit 0\n')
    changes['/bin/dtv_svc.sh'] = (
        '#!/vendor/bin/sh\n'
        'while [ ! -f /mnt/vendor/tmp/mtk_rm_end ] || [ ! -c /dev/fusion0 ]; do sleep 0.1; done\n'
        'mkdir -p /dev/shm/shm_tmp\nchmod 755 /dev/shm/shm_tmp\n'
        '/vendor/bin/sh /mnt/vendor/linux_rootfs/basic/autorun.sh\nwhile true; do sleep 3600; done\n')
    usb = text['/etc/init/hw/init.mt9653.usb.rc']
    need(usb.count('\non boot\n') == 1, 'Unexpected USB boot trigger')
    changes['/etc/init/hw/init.mt9653.usb.rc'] = usb.split('\non boot\n')[0] + (
        '\non boot\n    setprop sys.usb.configfs 1\n    setprop sys.usb.controller 1c7a3e00.usb\n'
        '    setprop persist.sys.usb.config adb\n    setprop sys.usb.config adb\n')
    changes['/etc/init/init.insmod.rc'] = board.once(text['/etc/init/init.insmod.rc'],
                                                   'mtu3_dev_en=0', 'mtu3_dev_en=1')
    changes['/etc/selinux/vendor_sepolicy.cil'] = cil.decode('utf-8')
    changes['/build.prop'] = board.properties(text['/build.prop'], {
        'persist.sys.usb.config': 'adb', 'ro.virtual_ab.enabled': 'false', 'ro.adb.secure': '1',
        'service.adb.tcp.port': '-1', 'ro.secure': '0', 'ro.debuggable': '1',
        'ro.codex.fileboot': '1', 'ro.crypto.state': 'unsupported', 'ro.crypto.type': 'none'})
    path = '/etc/init/init.fusion.rc'
    changes[path] = board.once(changes[path], 'import /vendor/etc/init/hw/init.insmod.rc',
                               'import /vendor/etc/init/init.insmod.rc')
    need(set(changes) == set(PATHS), 'Incomplete vendor patch set')
    return {name: value.encode('utf-8') for name, value in changes.items()}


def digest_stream(stream):
    h = hashlib.sha256()
    for block in iter(lambda: stream.read(4 * 1024**2), b''):
        h.update(block)
    return h.hexdigest()


def inventory(fs):
    """Read each reachable file and metadata without following symlink targets."""
    result, pending = {}, [('/', fs.get('/'))]
    while pending:
        path, node = pending.pop()
        need(path not in result and len(result) < 50000, 'Repeated or oversized filesystem tree')
        mode = node.inode.i_mode
        row = {'mode': mode, 'uid': node.inode.i_uid, 'gid': node.inode.i_gid,
               'xattrs': {x.name: x.value.hex() for x in node.xattr}}
        if stat.S_ISDIR(mode):
            for child in node.iterdir():
                if child.filename in ('.', '..'):
                    continue
                need('/' not in child.filename and '\0' not in child.filename, 'Invalid directory entry')
                pending.append((path.rstrip('/') + '/' + child.filename, child))
        elif stat.S_ISREG(mode):
            need(node.size <= 2 * 1024**3, 'File exceeds inventory bound')
            row.update(bytes=node.size, sha256=digest_stream(node.open()))
        elif stat.S_ISLNK(mode):
            row['link'] = node.link
        else:
            raise ValueError('Unexpected special filesystem entry')
        result[path] = row
    return result


def verify_inventory(before, after, changes):
    need(set(before) == set(after), 'Filesystem paths changed')
    for name, prior in before.items():
        expected = dict(prior)
        if name in changes:
            expected.update(bytes=len(changes[name]), sha256=hashlib.sha256(changes[name]).hexdigest())
        need(expected == after[name], 'Unexpected content or metadata: ' + name)


def check_output_space(output, image_bytes):
    # Inputs may be on a different disk from the new workspace.
    parent = output.resolve().parent
    while not parent.exists():
        parent = parent.parent
    need(parent.is_dir() and shutil.disk_usage(parent).free > image_bytes + 2 * 1024**3,
         'Insufficient output workspace reserve')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stock-vendor', type=Path, required=True)
    p.add_argument('--vendor-cil', type=Path, required=True)
    p.add_argument('--vendor-cil-sha256', required=True)
    p.add_argument('--docker-image', required=True, help='Existing local image with debugfs and e2fsck')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    os.umask(0o077)
    need(not args.output.exists(), 'Use a new output directory')
    need(re.fullmatch('[A-Za-z0-9][A-Za-z0-9._/:@-]*', args.docker_image), 'Invalid local tool image')
    state = extract.check_image(args.stock_vendor, STOCK_SHA)
    cil = board.components.read_input({'path': str(args.vendor_cil.resolve()),
                                       'sha256': args.vendor_cil_sha256}, Path('/'))
    check_output_space(args.output, state[2])
    with args.stock_vendor.open('rb') as source:
        fs = ExtFS(source)
        inputs = {}
        for name in PATHS:
            node = fs.get(name)
            need(stat.S_ISREG(node.inode.i_mode) and node.inode.i_links_count == 1,
                 'Patch target must be a single regular file')
            need(node.size < 16 * 1024**2, 'Patch input exceeds bound')
            inputs[name] = node.open().read()
        changes = render(inputs, cil)
        before = inventory(fs)
    need(extract.fingerprint(args.stock_vendor) == state, 'Source changed during read')
    args.output.mkdir(parents=True)
    output = args.output.resolve()
    payload = output / 'patches'
    payload.mkdir()
    commands = []
    for i, (name, data) in enumerate(changes.items()):
        item = 'f' + str(i)
        (payload / item).write_bytes(data)
        meta = before[name]
        commands.extend(['rm ' + name, 'write /out/patches/' + item + ' ' + name,
            'set_inode_field ' + name + ' mode 0%o' % meta['mode'],
            'set_inode_field ' + name + ' uid ' + str(meta['uid']),
            'set_inode_field ' + name + ' gid ' + str(meta['gid'])])
        need(set(meta['xattrs']) == {'security.selinux'}, 'Unexpected patch target attributes')
        raw_label = bytes.fromhex(meta['xattrs']['security.selinux'])
        label = raw_label.rstrip(b'\0').decode('ascii')
        need(re.fullmatch('[a-zA-Z0-9_:,.-]+', label), 'Invalid security label')
        # String-form ea_set drops the source label's trailing NUL. Preserve
        # the exact xattr bytes through debugfs's file input instead.
        (payload / (item + '.selinux')).write_bytes(raw_label)
        commands.append('ea_set -f /out/patches/' + item + '.selinux ' + name + ' security.selinux')
    (payload / 'commands').write_text('\n'.join(commands) + '\n')
    image = output / 'vendor-fileboot.img'
    shutil.copyfile(args.stock_vendor, image)
    with image.open('rb') as stream:
        need(digest_stream(stream) == STOCK_SHA, 'Copied input mismatch')
    prefix = ['docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
              '--mount', 'type=bind,source=' + str(output) + ',target=/out', args.docker_image]
    with (output / 'build.log').open('wb') as log:
        subprocess.run(prefix + ['debugfs', '-w', '-f', '/out/patches/commands', '/out/vendor-fileboot.img'],
                       stdout=log, stderr=log, check=True)
        subprocess.run(prefix + ['e2fsck', '-fn', '/out/vendor-fileboot.img'],
                       stdout=log, stderr=log, check=True)
    with image.open('rb') as stream:
        after = inventory(ExtFS(stream))
    verify_inventory(before, after, changes)
    need(extract.fingerprint(args.stock_vendor) == state, 'Original source changed')
    with image.open('rb') as stream:
        output_hash = digest_stream(stream)
    report = {'stockVendorSha256': STOCK_SHA, 'vendorCilSha256': hashlib.sha256(cil).hexdigest(),
              'outputSha256': output_hash, 'pathsVerified': len(after),
              'changedFiles': {k: hashlib.sha256(v).hexdigest() for k, v in changes.items()},
              'allReachableContentModesOwnersXattrsAndLinksVerified': True,
              'timestampsAndInodeNumbersCompared': False, 'originalImageChanged': False,
              'containsOemFiles': True, 'deviceAccess': False, 'runtimeTested': False,
              'deployEligible': False}
    (output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('pathsVerified', 'outputSha256', 'deployEligible')}))


if __name__ == '__main__':
    main()
