#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compile the local cross-version SELinux policy from pinned firmware CIL.

This reproduces the permissive compatibility setup, not an enforcing policy.
Firmware policy inputs and generated outputs stay private. No device access.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('components', ROOT / 'tools/assemble-runtime-components.py')
components = importlib.util.module_from_spec(spec)
spec.loader.exec_module(components)
need = components.need
ORDER = ('system-plat_sepolicy.cil', 'system-30.0.cil', 'system-30.0.compat.cil',
         'system_ext-system_ext_sepolicy.cil', 'system_ext-30.0.compat.cil',
         'product-product_sepolicy.cil', 'plat_pub_versioned.cil')


def reconcile(inputs, contexts, original_vendor, donor_vendor):
    need(set(inputs) == set(ORDER), 'Unexpected CIL inputs')
    duplicate = '(genfscon fuseblk / (u object_r ntfs ((s0) (s0))))'
    need(original_vendor.splitlines().count(duplicate) == 1, 'Unexpected fuseblk label')
    mirrors = []
    for line in original_vendor.splitlines():
        if re.match(r'^\(allowx?\s+\S+\s+ntfs\s+', line):
            mirrors.append(re.sub(r'^(\(allowx?\s+\S+\s+)ntfs\b', r'\1fuseblk', line))
    need(mirrors, 'Expected vendor NTFS permissions')
    vendor = original_vendor.replace(duplicate + '\n', '') + \
        '\n; Preserve original vendor permissions after fuseblk relabeling.\n' + '\n'.join(mirrors) + '\n'
    known, attributes = set(), set()
    for text in [*inputs.values(), vendor]:
        known.update(re.findall(r'\((?:type|typealias)\s+(\S+)\)', text))
        attributes.update(re.findall(r'\(typeattribute\s+(\S+)\)', text))
    missing = {}
    for name, text in contexts.items():
        for kind in set(re.findall(r'u:object_r:([^: ]+):s0', text)) - known:
            missing.setdefault(kind, []).append(name)
    need(set(missing) == {'vendor_mitv_type', 'hdmi_audio', 'pq_repository_service'}, 'Unexpected context type gaps')
    additions, memberships = [], {}
    for kind in sorted(missing):
        need('(type ' + kind + ')' in donor_vendor and '(roletype object_r ' + kind + ')' in donor_vendor,
             'Missing donor type declaration')
        additions += ['(type ' + kind + ')', '(roletype object_r ' + kind + ')']
        memberships[kind] = []
        for line in donor_vendor.splitlines():
            tokens = re.findall(r'[^ ()]+', line)
            if tokens[:1] == ['typeattributeset'] and kind in tokens[2:]:
                need(all(x not in tokens[2:] for x in ('and', 'or', 'not', 'xor')), 'Complex donor membership unsupported')
                attribute = tokens[1]
                need(attribute in attributes, 'Unknown donor attribute')
                additions.append('(typeattributeset ' + attribute + ' (' + kind + '))')
                memberships[kind].append(attribute)
    vendor += '\n; Required donor property/service context declarations, exact donor memberships.\n' + '\n'.join(additions) + '\n'
    return vendor, {'addedTypes': memberships, 'contextSources': missing,
                    'mirroredVendorNtfsRules': len(mirrors), 'newUnrelatedAllowRules': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    compiler = p.add_mutually_exclusive_group(required=True)
    compiler.add_argument('--secilc', type=Path)
    compiler.add_argument('--docker-compiler', help='Existing local image containing secilc')
    args = p.parse_args()
    os.umask(0o077)
    need(not args.output.exists(), 'Use a new output directory')
    need(args.profile.stat().st_size <= 65536, 'Profile exceeds bound')
    profile_bytes = args.profile.read_bytes()
    need(len(profile_bytes) <= 65536, 'Profile exceeds bound')
    profile = json.loads(profile_bytes)
    need(set(profile) == {'version', 'inputs', 'contexts', 'stockVendor', 'donorVendor'} and
         profile['version'] == 1, 'Invalid policy profile')
    need(set(profile['inputs']) == set(ORDER), 'Unexpected compile input list')
    need(1 <= len(profile['contexts']) <= 64 and all(re.fullmatch('[A-Za-z0-9_.-]+', n)
         for n in profile['contexts']), 'Invalid context list')
    def read(record):
        return components.read_input(record, args.profile.parent).decode('utf-8')
    inputs = {name: read(profile['inputs'][name]) for name in ORDER}
    contexts = {name: read(value) for name, value in profile['contexts'].items()}
    vendor, report = reconcile(inputs, contexts, read(profile['stockVendor']), read(profile['donorVendor']))
    if args.docker_compiler:
        need(re.fullmatch('[A-Za-z0-9][A-Za-z0-9._/:@-]*', args.docker_compiler), 'Invalid compiler image reference')
    out = args.output.resolve()
    out.mkdir(parents=True)
    for name, text in {**inputs, 'vendor-fileboot.cil': vendor}.items():
        (out / name).write_text(text)
    names = [*ORDER, 'vendor-fileboot.cil']
    options = ['-m', '-M', 'true', '-G', '-N', '-c', '31']
    if args.docker_compiler:
        command = ['docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
                   '--mount', 'type=bind,source=' + str(out) + ',target=/work', args.docker_compiler,
                   'secilc', *['/work/' + name for name in names], *options,
                   '-o', '/work/candidate-policy', '-f', '/work/file_contexts']
    else:
        command = [str(args.secilc.resolve()), *[str(out / name) for name in names], *options,
                   '-o', str(out / 'candidate-policy'), '-f', str(out / 'file_contexts')]
    with (out / 'compile.log').open('wb') as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    binary = (out / 'candidate-policy').read_bytes()
    need(binary[:4] == bytes.fromhex('8cff7cf9') and 100000 < len(binary) < 1048576, 'Unexpected policy output')
    report.update(policySha256=hashlib.sha256(binary).hexdigest(), bytes=len(binary), policyVersion=31,
                  vendorCilSha256=hashlib.sha256(vendor.encode()).hexdigest(),
                  inputs={name: hashlib.sha256(text.encode()).hexdigest() for name, text in inputs.items()},
                  sourceProfileSha256=hashlib.sha256(profile_bytes).hexdigest(),
                  neverallowChecksEnabled=False, enforcingSecurityValidated=False,
                  containsFirmwareDerivedPolicy=True, deviceAccess=False, runtimeTested=False, deployEligible=False)
    (out / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('policySha256', 'bytes', 'mirroredVendorNtfsRules', 'deployEligible')}))


if __name__ == '__main__':
    main()
