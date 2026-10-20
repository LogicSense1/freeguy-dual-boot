#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build a fixed four-file updater. It never connects to a device.

The probe build substitutes disposable files in the same three directories.
Both profiles and every generated output stay local.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = '/data/local/tmp/codex-gtv-fileboot-v1'
LOADER = '/data/local/tmp/rd-root'
CANDIDATE = '/data/local/tmp/codex-gtv-independent-candidate-01'
CHANGED = [BASE+'/'+n for n in ('system.img','vendor.img','system_ext.img')] + [LOADER+'/envdaily']


def need(ok, message):
    if not ok:
        raise ValueError(message)


def private_output(path):
    resolved = path.resolve()
    need(ROOT != resolved and ROOT not in resolved.parents,
         'Private updater output must stay outside the repository')
    need(not path.exists() and not path.is_symlink() and path.parent.is_dir(),
         'Use a new build directory with an existing parent')


def read_profile(path):
    need(path.is_file() and not path.is_symlink() and path.stat().st_size < 65536,
         'Expected a private profile file')
    result = json.loads(path.read_text())
    need(isinstance(result, dict), 'Profile must be an object')
    return result


def plan(old, new, probe):
    spec = importlib.util.spec_from_file_location('app_builder', Path(__file__).with_name('build-switch-app.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.pins(old, False)
    module.pins(new, False)
    need(old['normalizedBootargsSha256'] == new['normalizedBootargsSha256'], 'Boot profile changed')
    before, after = old['immutableFiles'], new['immutableFiles']
    need({p for p in before if before[p] != after[p]} == set(CHANGED), 'Only the four planned files may change')
    identity = hashlib.sha256(json.dumps([[(p,before[p],after[p]) for p in CHANGED],probe], separators=(',', ':')).encode()).hexdigest()[:16]
    rows = []
    for i,p in enumerate(CHANGED):
        name = 'loader' if p == LOADER+'/envdaily' else p.rsplit('/',1)[1]
        active = p if not probe else str(Path(p).parent / ('.update-probe-'+name))
        candidate = CANDIDATE+'/'+(('.update-probe-' if probe else '')+name)
        old_hash = hashlib.sha256(bytes([65+i])*4096).hexdigest() if probe else before[p]
        new_hash = hashlib.sha256(bytes([97+i])*4096).hexdigest() if probe else after[p]
        rows.append(dict(active=active, candidate=candidate, backup=active+'.previous-'+identity,
                         oldSha256=old_hash, newSha256=new_hash))
    return dict(id=identity, probe=probe, directories=[BASE,LOADER,CANDIDATE], rows=rows)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--old-profile',type=Path,required=True)
    p.add_argument('--new-profile',type=Path,required=True)
    p.add_argument('--android-jar',type=Path,required=True)
    p.add_argument('--d8-jar',type=Path,required=True)
    p.add_argument('--java-home',type=Path,required=True)
    p.add_argument('--probe',action='store_true')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();os.umask(0o077)
    private_output(a.output)
    result=plan(read_profile(a.old_profile),read_profile(a.new_profile),a.probe)
    need(all(path.is_file() for path in
             (a.java_home/'bin/javac',a.java_home/'bin/java',a.android_jar,a.d8_jar)),
         'JDK, Android platform jar and D8 jar must exist locally')
    out=a.output.resolve();out.mkdir(mode=0o700)
    for n in ('classes','dex','generated'):(out/n).mkdir()
    q=lambda s:json.dumps(s)
    source='package local.codex.tvswitchprobe;\nfinal class UpdatePins {\n'
    source+=' static final String ID='+q(result['id'])+';\n'
    source+=' static final String[] DIRECTORIES={'+','.join(map(q,result['directories']))+'};\n'
    source+=' static final ImageTransaction.Row[] ROWS={\n'
    source+=',\n'.join('  new ImageTransaction.Row('+','.join(q(r[k]) for k in ('active','candidate','backup','oldSha256','newSha256'))+')' for r in result['rows'])
    source+='\n };\n}\n';(out/'generated/UpdatePins.java').write_text(source)
    # Reuse the pure state and environment codecs; neither performs device I/O.
    app=ROOT/'app/src/local/codex/tvswitchprobe'
    selected=[app/(n+'.java') for n in ('DefaultBootPolicy','BootEnvironment')]
    cmd=[a.java_home/'bin/javac','--release','8','-cp',a.android_jar,'-d',out/'classes',*selected,
         *sorted((ROOT/'updater/src').rglob('*.java')),*sorted((out/'generated').glob('*.java'))]
    subprocess.run(list(map(str,cmd)),check=True)
    classes=sorted((out/'classes').rglob('*.class'))
    subprocess.run(list(map(str,[a.java_home/'bin/java','-cp',a.d8_jar,'com.android.tools.r8.D8','--min-api','30','--lib',a.android_jar,
                     '--classpath',out/'classes','--output',out/'dex',*classes])),check=True)
    result.update(dexSha256=hashlib.sha256((out/'dex/classes.dex').read_bytes()).hexdigest(),deployed=False,
                  bootWrites=False,mutableImageWrites=False)
    (out/'plan.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'id':result['id'],'probe':a.probe,'dexSha256':result['dexSha256']}))


if __name__=='__main__':
    main()
