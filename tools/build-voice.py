#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compile the voice bridge using declarations read from an owner's local OEM APK.

No OEM method bodies are copied. Generated declarations are compile-only and
excluded from output DEX. APK/JNI dependencies remain private local inputs.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import zipfile
sys.dont_write_bytecode = True
from dex_abi import Dex

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'com.mi.bluetooth.services.voice.mitvvoice'
CALLBACK = b'Lcom/mi/bluetooth/services/voice/mitvvoice/MitvVoiceCallback;'
REQUIRED = {
    'MitvVoiceCallback': {
        (b'<init>', b'V', ()): 0x10001,
        (b'notifyAudioStart', b'V', ()): 0,
        (b'notifyAudioStop', b'V', ()): 0,
        (b'notifyEncodedFrameSize', b'V', (b'I',)): 0,
        (b'notifyStartSearch', b'V', ()): 0,
        (b'outputAudioData', b'V', (b'[S',)): 0,
        (b'sendCommand', b'V', (b'[B',)): 0,
    },
    'MitvVoice': {
        (b'<init>', b'V', (CALLBACK, b'Landroid/os/Looper;')): 0x10001,
        (b'onSystemKey', b'V', (b'[B',)): 1,
        (b'onStreamEnableRead', b'V', (b'[B',)): 1,
        (b'onAudioData', b'V', (b'[B',)): 1,
        (b'onExit', b'V', ()): 1,
    },
}
JAVA_TYPES = {b'V': 'void', b'I': 'int', b'[S': 'short[]', b'[B': 'byte[]',
              CALLBACK: PACKAGE + '.MitvVoiceCallback', b'Landroid/os/Looper;': 'android.os.Looper'}


def declaration(dex, name):
    descriptor = ('L' + PACKAGE.replace('.', '/') + '/' + name + ';').encode()
    flags, methods = dex.class_methods(descriptor)
    if flags != (17 if name == 'MitvVoice' else 1):
        raise ValueError('OEM class modifiers differ from validated ABI')
    lines = ['// Generated from local declaration metadata. Compile only; never distribute.',
             'package ' + PACKAGE + ';', 'public ' + ('final ' if flags & 16 else '') + 'class ' + name + ' {']
    for signature, expected_flags in REQUIRED[name].items():
        if methods.get(signature) != expected_flags:
            raise ValueError('OEM method declaration differs from validated ABI')
        method, result, parameters = signature
        heading = ' public ' if expected_flags & 1 else ' '
        heading += name if method == b'<init>' else JAVA_TYPES[result] + ' ' + method.decode('ascii')
        arguments = ','.join(JAVA_TYPES[t] + ' arg' + str(n) for n, t in enumerate(parameters))
        lines.append(heading + '(' + arguments + ') { throw new UnsupportedOperationException("compile-only"); }')
    return '\n'.join(lines) + '\n}\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('oem-apk', 'oem-jni', 'android-jar', 'd8-jar', 'java-home', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--oem-sha256', required=True)
    p.add_argument('--jni-sha256', required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error('Use a new output directory')
    if not re.fullmatch('[0-9a-f]{64}', args.oem_sha256):
        p.error('Invalid expected OEM SHA-256')
    if args.oem_apk.is_symlink() or not args.oem_apk.is_file() or args.oem_apk.stat().st_size > 64 * 1024 * 1024:
        p.error('Invalid local APK')
    apk = args.oem_apk.read_bytes()
    apk_hash = hashlib.sha256(apk).hexdigest()
    if apk_hash != args.oem_sha256:
        p.error('Local APK does not match expected SHA-256')
    if not re.fullmatch('[0-9a-f]{64}', args.jni_sha256) or args.oem_jni.is_symlink() or not args.oem_jni.is_file() or args.oem_jni.stat().st_size > 16 * 1024 * 1024:
        p.error('Invalid local JNI input')
    jni = args.oem_jni.read_bytes()
    if hashlib.sha256(jni).hexdigest() != args.jni_sha256:
        p.error('Local JNI does not match expected SHA-256')
    if len(jni) < 52 or jni[:6] != b'\x7fELF\x01\x01' or struct.unpack_from('<HH', jni, 16) != (3, 40):
        p.error('Expected ARM32 JNI shared library')
    with zipfile.ZipFile(io.BytesIO(apk)) as archive:
        dex_files = [n for n in archive.namelist() if re.fullmatch(r'classes[0-9]*\.dex', n)]
        if dex_files != ['classes.dex'] or archive.getinfo('classes.dex').file_size > 64 * 1024 * 1024:
            p.error('Expected validated single-DEX OEM input')
        dex = Dex(archive.read('classes.dex'))
    # Validate both classes before writing output.
    generated = {name: declaration(dex, name) for name in REQUIRED}
    os.umask(0o077)
    out = args.output.resolve()
    for name in ('stubs', 'classes', 'dex'):
        (out / name).mkdir(parents=True)
    for name, source in generated.items():
        (out / 'stubs' / (name + '.java')).write_text(source)
    sources = [ROOT / 'voice/src' / (name + '.java') for name in ('RemoteVoiceCaptureProbe', 'GoogleVoiceRoute')]
    java = args.java_home / 'bin'

    def run(command):
        subprocess.run([str(v) for v in command], check=True, capture_output=True)

    run([java / 'javac', '--release', '8', '-cp', args.android_jar, '-d', out / 'classes',
         *sorted((out / 'stubs').glob('*.java')), *sources])
    real = [f for f in sorted((out / 'classes').rglob('*.class'))
            if f.name.startswith(('RemoteVoiceCaptureProbe', 'GoogleVoiceRoute'))]
    run([java / 'java', '-cp', args.d8_jar, 'com.android.tools.r8.D8', '--min-api', '30',
         '--lib', args.android_jar, '--classpath', out / 'classes', '--output', out / 'dex', *real])
    output = (out / 'dex/classes.dex').read_bytes()
    built = Dex(output)
    names = [built.type(built.u32(built.item('classes', n))).decode('ascii')
             for n in range(built.tables['classes'][0])]
    prefixes = ['L' + PACKAGE.replace('.', '/') + '/' + name
                for name in ('RemoteVoiceCaptureProbe', 'GoogleVoiceRoute')]
    if not names or any(not any(n.startswith(prefix) for prefix in prefixes) and
                        n != 'Lcom/android/tools/r8/annotations/LambdaMethod;' for n in names):
        raise ValueError('Unexpected classes or OEM implementation included in DEX')
    payload = {'classes.dex': output, 'oem.apk': apk,
               'libmibluetooth_jni.so': jni}
    for name, target in (('RemoteReconnectProbe', 'reconnect.dex'), ('RemoteWakeAddress', 'wake.dex')):
        component = out / name
        (component / 'classes').mkdir(parents=True)
        (component / 'dex').mkdir()
        run([java / 'javac', '--release', '8', '-cp', args.android_jar, '-d', component / 'classes',
             ROOT / 'voice/support' / (name + '.java')])
        run([java / 'java', '-cp', args.d8_jar, 'com.android.tools.r8.D8', '--min-api', '30',
             '--lib', args.android_jar, '--output', component / 'dex',
             *sorted((component / 'classes').glob('*.class'))])
        body = (component / 'dex/classes.dex').read_bytes()
        support = Dex(body)
        support_names = [support.type(support.u32(support.item('classes', n))).decode('ascii')
                         for n in range(support.tables['classes'][0])]
        if any(n != 'L' + name + ';' and not n.startswith('L' + name + '$') and
               n != 'Lcom/android/tools/r8/annotations/LambdaMethod;' for n in support_names):
            raise ValueError('Unexpected support implementation')
        if b'createBond' in body or b'removeBond' in body:
            raise ValueError('Pairing mutation API in runtime support')
        payload[target] = body
    for name in ('start.sh', 'lifecycle.sh', 'supervisor.sh', 'register-wake.sh'):
        source = ROOT / 'voice/scripts' / name
        run(['sh', '-n', source])
        payload[name] = source.read_bytes()
    (out / 'payload').mkdir()
    for name, body in payload.items():
        (out / 'payload' / name).write_bytes(body)
    hashes = {n: hashlib.sha256(b).hexdigest() for n, b in payload.items()}
    (out / 'payload/payload.sha256').write_text(''.join(h + '  ' + n + '\n' for n, h in sorted(hashes.items())))
    report = {'sha256': hashlib.sha256(output).hexdigest(), 'localOemSha256': apk_hash,
              'sourceSha256': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sources},
              'requiredDeclarationsChecked': sum(len(v) for v in REQUIRED.values()),
              'definedClasses': names, 'bridgeDexContainsOemClasses': False,
              'syntheticSpeechProbeExcluded': True, 'runtimePairingMutationApisAbsent': True,
              'privatePayloadContainsOemFiles': True, 'payloadSha256': hashes,
              'deployed': False, 'runtimeVerified': False}
    (out / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('definedClasses', 'sourceSha256', 'payloadSha256')}))


if __name__ == '__main__':
    main()
