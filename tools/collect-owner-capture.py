#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Collect a completed early-root capture through ordinary stock ADB.

Reads only. Does not stage, activate, clean up, flash or install anything.
The completion record must belong to the current stock boot.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import zlib

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]


def load(name):
    s=importlib.util.spec_from_file_location(name,ROOT/('tools/'+name+'.py'))
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


backup,recovery=load('backup-data'),load('serial-recovery')
need=recovery.need
BASE='/data/local/tmp/freeguy-owner-capture-v1'
SIZES={'uenv.img':16777216,'uuenv.img':16777216,'persist.img':20971520,'tvcertificate.img':33554432}
sha=lambda b:hashlib.sha256(b).hexdigest()
UUID=r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'


def completion(raw,boot):
    need(len(raw)<256,'Oversized completion record')
    value=raw.decode('ascii')
    need(re.fullmatch(r'FREEGUY_OWNER_CAPTURE_V1\n'+UUID+r'\n(?:[0-9a-f]{8}\n){4}',value),
         'Incomplete or malformed capture marker')
    lines=value.splitlines()
    need(lines[1]==boot,'Capture belongs to a different boot; do not reuse a stale marker')
    return dict(zip(SIZES,lines[2:]))


def validate_images(images,allowed,binding,scheme,cid):
    from dissect.extfs import ExtFS
    for name,data in images.items():
        need(len(data)==SIZES[name] and data[1080:1082]==b'\x53\xef','Not a logical ext filesystem')
        need(stat.S_ISDIR(ExtFS(io.BytesIO(data)).get('/').inode.i_mode),'Filesystem root missing')
    env=images['uenv.img'];recovery.check_partition(env,allowed)
    fs=ExtFS(io.BytesIO(env));serials=[]
    for name in ('uboot.env','uboot.env.bak'):
        args=recovery.stored_bootargs(fs.get('/'+name).open().read())
        values=[t.split('=',1)[1] for t in args.split() if t.startswith('androidboot.serialno=')]
        need(len(values)==1,'Owner serial missing');serials.append(values[0])
    need(serials[0]==serials[1],'Environment owner differs between copies')
    if scheme=='freeguy-android-serial-v1':material=scheme+'\0'+serials[0]
    else:
        need(scheme=='freeguy-emmc-cid-serial-v1' and re.fullmatch('[0-9a-f]{32}',cid or ''),
             'Expected independently recorded CID binding')
        material='freeguy\0'+cid+'\0'+serials[0]
    need(sha(material.encode())==binding,'Capture owner does not match the independently recorded device')


class Device:
    def __init__(self,adb,selector,binding_source):
        need(re.fullmatch('[A-Za-z0-9_.:-]+',selector),'Explicit ADB selector required')
        self.adb,self.binding_source=[str(adb),'-s',selector],binding_source
    def shell(self,text):
        return subprocess.check_output(self.adb+['shell',text],stderr=subprocess.PIPE,timeout=60).decode().strip()
    def identity(self):
        props=[self.shell('getprop '+name) for name in backup.PROPS]
        need(self.shell('id -u')=='2000','Require ordinary HyperOS ADB shell')
        need(not self.shell('for p in /sys/block/loop*/loop/backing_file; do '
                            '[ -e "$p" ] || continue; cat "$p" || exit 1; done'),'Attached loop images')
        cid=self.shell('cat /sys/block/mmcblk0/device/cid').lower() if self.binding_source=='emmc-cid' else None
        return backup.stock_identity(props,self.shell('cat /proc/1/mounts'),
                    self.shell('cat /proc/sys/kernel/random/boot_id'),cid,
                    self.shell('getprop ro.serialno'),self.binding_source),cid
    def metadata(self,name,directory=False):
        path=BASE+('/'+name if name else '')
        kind='-d' if directory else '-f'
        value=self.shell(f'test {kind} {path} && test ! -L {path} && '
                         f'stat -c "%d %i %h %s %u %g %a %Y %Z" {path}')
        fields=value.split()
        need(len(fields)==9 and all(re.fullmatch('[0-9]+',x) for x in fields),'Metadata unavailable')
        need(fields[4:7]==['2000','2000','700' if directory else '600'],'Capture access metadata differs')
        if not directory:
            need(fields[2]=='1' and int(fields[3])==(98 if name=='COMPLETE' else SIZES[name]),
                 'Wrong capture file size or link count')
        return value
    def read(self,name):
        need(name in SIZES or name=='COMPLETE','Unknown capture file')
        return subprocess.check_output(self.adb+['exec-out','cat',BASE+'/'+name],
                                       stderr=subprocess.PIPE,timeout=90)
    def digest(self,name):
        value=self.shell('sha256sum '+BASE+'/'+name).split()[0]
        need(re.fullmatch('[0-9a-f]{64}',value),'Source digest missing');return value


def collect(device,expected,allowed,output):
    first,cid=device.identity()
    need(first['deviceBindingSha256']==expected,'Wrong device')
    directory=device.metadata('',True);marker_metadata=device.metadata('COMPLETE')
    marker=device.read('COMPLETE');crcs=completion(marker,first['bootId'])
    metadata={name:device.metadata(name) for name in SIZES}
    need(shutil.disk_usage(output.parent).free>sum(SIZES.values())+1024**3,'Insufficient local reserve')
    output.mkdir(mode=0o700)
    record=dict(**first,complete=False,files=[],deviceWritesIssued=False,stockBVerified=True,
                sameBootCapture=True,filesystemHealthVerified=False,installationAuthorized=False)
    save=lambda:backup.save(output/'manifest.json',record)
    save()
    images={}
    try:
        for name in SIZES:
            need(device.identity()[0]==first,'TV boot changed')
            digest=device.digest(name);raw=device.read(name)
            need(len(raw)==SIZES[name] and sha(raw)==digest and f'{zlib.crc32(raw):08x}'==crcs[name],
                 'Capture bytes or CRC changed')
            need(device.digest(name)==digest and device.metadata(name)==metadata[name],
                 'Source changed during transfer')
            target=output/name
            with target.open('xb') as sink:sink.write(raw);sink.flush();os.fsync(sink.fileno())
            need(sha(target.read_bytes())==digest,'Local readback failed')
            images[name]=raw;record['files'].append(dict(name=name,bytes=len(raw),sha256=digest));save()
        validate_images(images,allowed,expected,first['deviceBindingScheme'],cid)
        for row in record['files']:
            name=row['name']
            need(device.digest(name)==row['sha256'] and device.metadata(name)==metadata[name],
                 'Earlier capture source changed')
            need(sha((output/name).read_bytes())==row['sha256'],'Local capture changed')
        need(device.read('COMPLETE')==marker and device.metadata('COMPLETE')==marker_metadata
             and device.metadata('',True)==directory and device.identity()[0]==first,
             'Capture directory, marker or boot changed')
        record['complete']=True;save();return record
    except BaseException as exc:
        record['failure']=type(exc).__name__;save();raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('adb','profile','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--device',required=True);p.add_argument('--known-device-binding',required=True)
    p.add_argument('--binding-source',choices=['emmc-cid','android-serial'],default='emmc-cid')
    a=p.parse_args();os.umask(0o077);recovery.private_output(a.output)
    need(re.fullmatch('[0-9a-f]{64}',a.known_device_binding),'Known owner binding required')
    result=collect(Device(a.adb,a.device,a.binding_source),a.known_device_binding,
                   recovery.read_profile(a.profile),a.output)
    print(json.dumps({k:result[k] for k in ('complete','deviceWritesIssued','sameBootCapture','filesystemHealthVerified')}))


if __name__=='__main__':main()
