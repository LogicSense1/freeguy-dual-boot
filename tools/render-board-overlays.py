#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate private board overlays from pinned, locally extracted firmware files.

No device access. OEM inputs and generated derivative files are not distributed.
The panel module is copied unchanged; no register or panel timing edits occur.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('components', ROOT / 'tools/assemble-runtime-components.py')
components = importlib.util.module_from_spec(spec)
spec.loader.exec_module(components)
need, once = components.need, components.replace_once
KEYS = {'stock_vendor_props', 'stock_board_init', 'stock_audio_policy', 'stock_ssl_init',
        'stock_keylayout', 'google_system_init', 'stock_panel_module'}
PANEL_SHA256 = 'acbf25d9b926a3959ded6967506dd8b3ca0f9745b6ad57c056c425b7def483ca'


def properties(text, values):
    for key, value in values.items():
        text = re.sub(r'^' + re.escape(key) + r'=.*\n?', '', text, flags=re.M)
        text += '\n' + key + '=' + value + '\n'
    return text


def strip_services(text, names):
    out, skip = [], False
    for line in text.splitlines(True):
        if line and not line[0].isspace() and line.strip() and not line.startswith('#'):
            skip = line.startswith('service ') and line.split()[1] in names
        if skip:
            continue
        if re.match(r'^\s*(start|stop|restart|enable|exec_start)\s+(' +
                    '|'.join(re.escape(x) for x in sorted(names)) + r')\s*$', line):
            continue
        out.append(line)
    return ''.join(out)


def render(inputs, loops):
    need(set(inputs) == KEYS, 'Unexpected firmware input set')
    need(set(loops) == {'data', 'metadata', 'cache'} and
         all(type(n) is int and 0 <= n < 256 for n in loops.values()) and
         len(set(loops.values())) == 3, 'Invalid data loop mapping')
    need(hashlib.sha256(inputs['stock_panel_module']).hexdigest() == PANEL_SHA256, 'Unsupported original panel module')
    text = {key: value.decode('utf-8') for key, value in inputs.items() if key != 'stock_panel_module'}
    out = {}
    prop = properties(text['stock_vendor_props'], {'persist.sys.usb.config': 'adb',
        'ro.virtual_ab.enabled': 'false', 'ro.adb.secure': '1', 'service.adb.tcp.port': '-1',
        'ro.secure': '0', 'ro.debuggable': '1', 'ro.codex.fileboot': '1',
        'ro.crypto.state': 'unsupported', 'ro.crypto.type': 'none'})
    out['vendor-build.prop'] = properties(prop, {'ro.apex.updatable': 'true'})
    rc = text['stock_board_init']
    for name in ('set_mcu_ota', 'ubootlog'):
        need(len(re.findall(r'^service ' + name + r' ', rc, re.M)) == 1, 'Unexpected board services')
    rc = strip_services(rc, {'makeuserdata', 'start_vab_update', 'stop_vab_update',
                             'set_mcu_ota', 'bt-wakeuprc', 'f2fs-svc', 'ubootlog'})
    lines, skip = [], False
    for line in rc.splitlines(True):
        if line.startswith('on '):
            skip = line.strip() == 'on mkfs_userdata'
        if skip or re.search(r'^\s*(mount_all |exec_start wait_for_keymaster|symlink /dev/block/|load_data_props\b|saverecoverylog\b|savepanicinfo\b|chmod 755 /vendor/factory/)', line):
            continue
        lines.append(line)
    rc = once(''.join(lines), 'loglevel 3', 'loglevel 7')
    out['init.mt9653.rc'] = rc + ('\n# Authenticated local network ADB for this owner-authorized trial.\n'
        'on boot\n    setprop service.adb.tcp.port 5555\n    restart adbd\n')
    policy = once(text['stock_audio_policy'], 'engine_library="configurable"', 'engine_library="default"')
    for begin, end in (('<mixPort name="primary input"', '</mixPort>'),
                       ('<devicePort tagName="Built-In Mic"', '</devicePort>')):
        need(policy.count(begin) == 1, 'Unexpected microphone policy')
        start = policy.index(begin)
        finish = policy.index(end, start) + len(end)
        section = once(policy[start:finish], 'AUDIO_CHANNEL_IN_STEREO', 'AUDIO_CHANNEL_INDEX_MASK_4')
        policy = policy[:start] + section + policy[finish:]
    ET.fromstring(policy)
    out['audio_policy_configuration.xml'] = policy
    ssl = text['stock_ssl_init']
    need(ssl.count('on early-init &&') == 2, 'Unexpected SSL startup triggers')
    ssl = ssl.replace('on early-init &&', 'on property:apexd.status=activated &&')
    out['boringssl_self_test.rc'] = '\n'.join(l for l in ssl.splitlines() if not l.strip().startswith('reboot_on_failure ')) + '\n'
    out['remote-32b8.kl'] = once(text['stock_keylayout'], 'DIRECT_INPUT', 'TV_INPUT')
    native = '\n'.join(l for l in text['google_system_init'].splitlines() if not any(x in l for x in
        ('exec_start update_verifier', 'vdc checkpoint markBootAttempt', 'import /vendor/etc/init/hw/init.bsp-mtk.rc'))) + '\n'
    native = '\n'.join(l for l in native.splitlines() if not l.strip().startswith('reboot_on_failure ')) + '\n'
    native = once(native, '    exec - system system -- /system/bin/vdc checkpoint prepareCheckpoint',
                  '    # No OTA checkpoint for isolated file-backed trial data.')
    native += '\n# File-backed ext4 is already mounted without device encryption.\non boot\n    trigger nonencrypted\n'
    need('bluetooth.profile.' not in native and 'bluetooth.core.gap.le.privacy.enabled' not in native,
         'Bluetooth profile already configured')
    native += ('\n# OEM-supported Bluetooth profiles for Android 14.\non init\n'
        '    setprop bluetooth.profile.hid.host.enabled true\n'
        '    setprop bluetooth.profile.a2dp.source.enabled true\n'
        '    setprop bluetooth.profile.avrcp.target.enabled true\n'
        '\n# Temporary fixed-address remote interoperability trial; reconnect not yet accepted.\non init\n'
        '    setprop bluetooth.core.gap.le.privacy.enabled false\n')
    out['native-init.rc'] = native
    out['dtv_svc.sh'] = ('#!/vendor/bin/sh\n'
        'while [ ! -f /mnt/vendor/tmp/mtk_rm_end ] || [ ! -c /dev/fusion0 ]; do sleep 0.1; done\n'
        'umask 000\n'
        'if [ "$(cat /sys/class/graphics/fb0/name)" = "MStar FB" ] && [ "$(cat /sys/class/graphics/fb1/name)" = "MStar FB0" ]; then\n'
        '    watcher=$(cat /dev/codex-watchdog.pid)\n'
        '    /proc/$watcher/exe bind-file /dev/graphics/fb1 /dev/graphics/fb0 || exit 1\n'
        'fi\nmkdir -p /dev/shm/shm_tmp\nchmod 755 /dev/shm/shm_tmp\n'
        '/vendor/bin/sh /mnt/vendor/linux_rootfs/basic/autorun.sh\nwhile true; do sleep 3600; done\n')
    out['fstab.mt9653'] = '# Independent file-backed filesystems, already mounted by RAM bootstrap.\n' + ''.join(
        '/dev/block/loop%d /%s ext4 noatime,nosuid,nodev wait\n' % (loops[name], name)
        for name in ('data', 'metadata', 'cache'))
    out['boot-control.xml'] = '<manifest version="1.0" type="device"/>\n'
    out['CaptureRecorderServer.rc'] = '# Donor capture service requires an unavailable policy domain/HAL.\n'
    out['hdmi-etc/mitv.config.xml'] = '<?xml version="1.0" encoding="utf-8"?>\n<config>\n  <item key="support_one_touch_play" value="YES"/>\n</config>\n'
    out['hdmi-permissions/codex-builtin-microphone.xml'] = '<?xml version="1.0" encoding="utf-8"?>\n<permissions>\n    <feature name="android.hardware.microphone" />\n</permissions>\n'
    result = {key: value.encode('utf-8') for key, value in out.items()}
    result['mik-logo-detection.ko'] = inputs['stock_panel_module']
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    os.umask(0o077)
    need(not args.output.exists(), 'Use a new output directory')
    need(args.profile.stat().st_size <= 32768, 'Profile exceeds bound')
    profile = json.loads(args.profile.read_text())
    need(set(profile) == {'version', 'inputs', 'dataLoops'} and profile['version'] == 1, 'Invalid profile')
    need(set(profile['inputs']) == KEYS, 'Unexpected input set')
    inputs = {key: components.read_input(value, args.profile.parent) for key, value in profile['inputs'].items()}
    outputs = render(inputs, profile['dataLoops'])
    args.output.mkdir(parents=True)
    for name, value in outputs.items():
        target = args.output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    report = {'inputs': {n: hashlib.sha256(v).hexdigest() for n, v in inputs.items()},
              'outputs': {n: hashlib.sha256(v).hexdigest() for n, v in outputs.items()},
              'panelModuleUnchanged': True, 'deviceAccess': False, 'runtimeTested': False,
              'containsOemDerivedFiles': True, 'deployEligible': False}
    (args.output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'files': len(outputs), 'panelModuleUnchanged': True, 'deployEligible': False}))


if __name__ == '__main__':
    main()
