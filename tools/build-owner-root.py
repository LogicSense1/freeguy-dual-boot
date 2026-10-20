#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Assemble an offline owner-capture RAM root, retaining unmodified stock init."""
import argparse
import copy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def load(name):
    s=importlib.util.spec_from_file_location(name,ROOT/('tools/'+name+'.py'))
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


bootstrap=load('build-bootstrap-root');recovery=load('serial-recovery')
need=recovery.need
sha=lambda b:hashlib.sha256(b).hexdigest()


def prepare(stock, capture_build):
    need(sha(stock)==bootstrap.STOCK_SHA256,'Wrong original B boot image')
    manifest=json.loads((capture_build/'manifest.json').read_text())
    audit=json.loads((capture_build/'audit.json').read_text())
    binary=(capture_build/'owner-capture').read_bytes()
    need(manifest['sha256']==audit['binarySha256']==sha(binary),'Capture binary or audit differs')
    need(manifest['sourceSha256']==audit['sourceSha256']==sha((ROOT/'runtime/owner-capture.c').read_bytes()),
         'Capture source differs')
    expected={'normal','partial-io','not-pid1','wrong-root','extra-arg','bad-boot-id','no-space','existing-output',
              'mount-failed','wrong-layout','symlink-parent','source-open-failed',
              'output-open-failed','bad-superblock','source-changed','read-failed',
              'write-failed','chown-failed','xattr-failed','fsync-failed','close-failed','unmount-failed','rename-failed','exec-failed'}
    cases=audit.get('cases',[])
    need(len(cases)==len(expected) and {r['case'] for r in cases}==expected
         and all(r['partitionWriteDescriptors']==0 for r in cases)
         and audit.get('crcArmVectors')==5 and audit.get('deviceAccess') is False,
         'Incomplete capture audit')
    items=copy.deepcopy(bootstrap.ramdisk_from_boot(stock))
    byname={e['name']:e for e in items}
    need(byname['init']['data']==b'/system/bin/init' and stat.S_ISLNK(byname['init']['fields'][1]),
         'Unexpected original init symlink')
    need('system/bin/stock-init' not in byname and
         not any(n.startswith(('owner-','.codex-')) for n in byname),
         'Input is not the original stock root')
    original=copy.deepcopy(byname['system/bin/init'])
    need(stat.S_ISREG(original['fields'][1]) and original['fields'][4]==1,'Unexpected stock init')
    original['name']='system/bin/stock-init'
    original['fields'][0]=max(e['fields'][0] for e in items)+1
    items.append(original)
    byname['system/bin/init']['data']=binary
    bootstrap.archive.table(items)
    return items,dict(stockBootSha256=sha(stock),stockInitSha256=sha(original['data']),
                      captureSha256=sha(binary),captureSourceSha256=manifest['sourceSha256'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stock-boot','capture-build','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();os.umask(0o077);recovery.private_output(a.output)
    items,info=prepare(a.stock_boot.read_bytes(),a.capture_build)
    raw=bootstrap.components.pack(items);compressed=gzip.compress(raw,mtime=0)
    need(len(bootstrap.archive.entries(raw))==len(items),'CPIO readback failed')
    a.output.mkdir(mode=0o700);(a.output/'ramdisk.cpio.gz').write_bytes(compressed)
    report=dict(**info,ramdiskSha256=sha(compressed),entries=len(items),
                originalInitUnmodified=True,deviceAccess=False,runtimeTested=False,
                deployEligible=False,containsOemFiles=True)
    (a.output/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
