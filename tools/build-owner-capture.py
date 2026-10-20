#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compile the early owner-data capture candidate. Does not stage or boot it."""
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
spec = importlib.util.spec_from_file_location('recovery', ROOT/'tools/serial-recovery.py')
recovery = importlib.util.module_from_spec(spec); spec.loader.exec_module(recovery)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--clang', type=Path, required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--ld-lld', type=Path)
    group.add_argument('--docker-linker')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); os.umask(0o077)
    recovery.private_output(a.output)
    source = ROOT/'runtime/owner-capture.c'; raw = source.read_bytes()
    a.output.mkdir(mode=0o700)
    run = lambda args: subprocess.run(list(map(str,args)),check=True,capture_output=True)
    run([a.clang,'--target=armv7a-linux-gnueabi','-marm','-Oz','-ffreestanding',
         '-fno-builtin','-fno-stack-protector','-fno-unwind-tables',
         '-fno-asynchronous-unwind-tables','-mfloat-abi=soft','-mfpu=none',
         '-Wall','-Wextra','-Werror','-c',source,'-o',a.output/'capture.o'])
    if a.docker_linker:
        recovery.need(re.fullmatch('[A-Za-z0-9][A-Za-z0-9._/:@-]*',a.docker_linker),
                      'Invalid local linker image')
        run(['docker','run','--rm','--pull=never','--network=none','--read-only',
             '--mount',f'type=bind,source={a.output.resolve()},target=/work',
             a.docker_linker,'ld.lld-18','-static','-m','armelf_linux_eabi','-e','_start',
             '/work/capture.o','-o','/work/owner-capture'])
    else:
        run([a.ld_lld,'-static','-m','armelf_linux_eabi','-e','_start',
             a.output/'capture.o','-o',a.output/'owner-capture'])
    recovery.need(source.read_bytes()==raw,'Capture source changed during build')
    digest = lambda b: hashlib.sha256(b).hexdigest()
    report = dict(sourceSha256=digest(raw),sha256=digest((a.output/'owner-capture').read_bytes()),
                  deviceAccess=False,deployEligible=False,runtimeTested=False,
                  scope='Early stock-root capture candidate; needs outer self-clearing loader')
    (a.output/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__': main()
