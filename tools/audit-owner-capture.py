#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Execute candidate ARM code in a syscall model. No TV, mount or device access."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import zlib
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_INTR, UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3
from unicorn.arm_const import UC_ARM_REG_R4, UC_ARM_REG_R7, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC

ROOT = Path(__file__).resolve().parents[1]
STACK, RETURN = 0x70030000, 0x60001000
SIZES = {'/owner-p3':16777216, '/owner-p4':16777216,
         '/owner-p18':20971520, '/owner-p5':33554432}
OUTPUTS = dict(zip(('uenv','uuenv','persist','tvcertificate'),SIZES))
BASE = '/owner-host/local/tmp/freeguy-owner-capture-v1'
BOOT_ID = b'12345678-1234-1234-1234-123456789abc\n'
EXPECTED_SYS = {
 'mmcblk0p3':('179:3\n','45056\n','32768\n'),
 'mmcblk0p4':('179:4\n','77824\n','32768\n'),
 'mmcblk0p18':('259:10\n','1310720\n','40960\n'),
 'mmcblk0p5':('179:5\n','110592\n','65536\n'),
 'mmcblk0p42':('259:34\n','9277440\n','112865240\n'),
}


def need(value, message):
    if not value: raise ValueError(message)


def machine(raw):
    elf = ELFFile(io.BytesIO(raw))
    need(elf['e_machine']=='EM_ARM' and elf['e_type']=='ET_EXEC','Expected static ARM executable')
    need(not any(s['p_type']=='PT_INTERP' for s in elf.iter_segments()),'Unexpected dynamic loader')
    segments=[s for s in elf.iter_segments() if s['p_type']=='PT_LOAD']
    low=min(s['p_vaddr'] for s in segments)&~4095
    high=max(s['p_vaddr']+s['p_memsz'] for s in segments)
    u=Uc(UC_ARCH_ARM,UC_MODE_ARM); u.mem_map(low,(high-low+4095)&~4095)
    for s in segments:u.mem_write(s['p_vaddr'],s.data())
    u.mem_map(0x70000000,0x40000);u.mem_map(0x60000000,0x2000)
    u.mem_write(STACK,struct.pack('<4I',1,0,0,0));u.reg_write(UC_ARM_REG_SP,STACK)
    symbols={s.name:s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
    return u,elf['e_entry'],symbols


def crc_vectors(raw):
    u,_,symbols=machine(raw);checks=0
    for size,seed in [(0,0xffffffff),(1,0xffffffff),(256,0xffffffff),(4096,0xffffffff),(517,0x12345678)]:
        data=bytes((i*37+11)&255 for i in range(size));u.mem_write(0x70000000,data or b'\0')
        u.reg_write(UC_ARM_REG_R0,seed);u.reg_write(UC_ARM_REG_R1,0x70000000)
        u.reg_write(UC_ARM_REG_R2,size);u.reg_write(UC_ARM_REG_LR,RETURN)
        u.emu_start(symbols['crc'],RETURN,count=500000)
        need(u.reg_read(UC_ARM_REG_PC)==RETURN,'CRC did not return')
        need(u.reg_read(UC_ARM_REG_R0)==(zlib.crc32(data,seed^0xffffffff)^0xffffffff),'ARM CRC mismatch')
        checks+=1
    return checks


def source_bytes(path, offset, count):
    n=min(count,max(0,SIZES[path]-offset));data=bytearray(n)
    for pos,value in [(1080,0x53),(1081,0xef)]:
        if offset<=pos<offset+n:data[pos-offset]=value
    return bytes(data)


def run_case(raw,case):
    u,entry,symbols=machine(raw)
    fds={};created=set();mounts=set();writes={};opened={};logs=[];done=[]
    synced=set();labelled=set();source_reads=0;calls=0;crc_calls=0;renamed=False
    if case=='extra-arg':u.mem_write(STACK,struct.pack('<I',2))
    def string(address):
        return bytes(u.mem_read(address,256)).split(b'\0')[0].decode() if address else None
    def checksum(uc,address,size,_):
        nonlocal crc_calls
        seed=uc.reg_read(UC_ARM_REG_R0);p=uc.reg_read(UC_ARM_REG_R1);n=uc.reg_read(UC_ARM_REG_R2)
        need(n<=65536,'Unbounded CRC input')
        data=bytes(uc.mem_read(p,n))
        uc.reg_write(UC_ARM_REG_R0,zlib.crc32(data,seed^0xffffffff)^0xffffffff)
        uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR));crc_calls+=1
    def syscall(uc,irq,_):
        nonlocal source_reads,calls,renamed
        need(irq==2,'Unexpected interrupt');calls+=1
        nr=uc.reg_read(UC_ARM_REG_R7)
        a=[uc.reg_read(r) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_R4)]
        ret=0
        if nr==20:ret=2000 if case=='not-pid1' else 1
        elif nr==1:
            need(case=='not-pid1' and a[0]==90 and not created,'Non-PID1 side effects')
            done.append('refused');uc.emu_stop();return
        elif nr==99:
            path=string(a[0]);need(path in ('/','/owner-host'),'Unexpected statfs')
            fields=[0]*32;fields[0]=0xf2f52010 if path=='/owner-host' or case=='wrong-root' else 0x01021994
            fields[1]=4096;fields[4]=1 if case=='no-space' else 1024*1024
            uc.mem_write(a[1],struct.pack('<32I',*fields))
        elif nr==39:
            path=string(a[0]);need(path in ('/owner-sys','/owner-host','/owner-proc',BASE) and a[1]==0o700,'Unexpected mkdir')
            if path==BASE and case=='existing-output':ret=-17
            else:need(path not in created,'Existing directory overwritten');created.add(path)
        elif nr==21:
            source,target,kind=map(string,a[:3]);need(a[4]==0,'Unexpected mount options')
            if kind=='sysfs':
                need((source,target,a[3])==('sysfs','/owner-sys',15),'Wrong sysfs mount')
            elif kind=='proc':
                need((source,target,a[3])==('proc','/owner-proc',15),'Wrong proc mount')
            else:
                need((source,target,kind,a[3])==('/owner-p42','/owner-host','f2fs',6),
                     'Only userdata may be mounted writable')
            if case=='mount-failed' and kind=='f2fs':ret=-22
            else:mounts.add(target)
        elif nr==14:
            path=string(a[0]);expected={'/owner-p3':0xb303,'/owner-p4':0xb304,
                '/owner-p18':0x1030a,'/owner-p5':0xb305,'/owner-p42':0x10322,'/owner-kmsg':0x10b}
            need(path in expected and a[2]==expected[path],'Unexpected device number')
            need(a[1]==(0o20600 if path=='/owner-kmsg' else 0o60600),'Wrong node mode')
            created.add(path)
        elif nr==196:
            path=string(a[0]);need(path in ('/owner-host/local','/owner-host/local/tmp'),'Unexpected lstat')
            data=bytearray(104);struct.pack_into('<I',data,16,0o120777 if case=='symlink-parent' else 0o40771)
            uc.mem_write(a[1],bytes(data))
        elif nr==5:
            path=string(a[0]);flags=a[1]
            if path.startswith('/owner-sys/'):
                need('/owner-sys' in mounts and flags==131072,'Wrong sysfs read')
            elif path=='/owner-proc/sys/kernel/random/boot_id':
                need('/owner-proc' in mounts and flags==131072,'Wrong boot ID read')
            elif path in SIZES:
                need(path in created and flags==131072,'Partition FD must be read-only')
                opened[path]=opened.get(path,0)+1
                if case=='source-open-failed':ret=-13
            elif path==BASE:
                need(path in created and flags==196608,'Unexpected directory open')
            elif path=='/owner-kmsg':
                need(path in created and flags==131073,'Unexpected log FD')
            else:
                need(path.startswith(BASE+'/') and '/owner-host' in mounts and BASE in created
                     and flags==131265 and a[2]==0o600,'Unexpected or overwriting write')
                name=path[len(BASE)+1:]
                need(name in [n+'.img' for n in OUTPUTS]+['COMPLETE'] and path not in writes,
                     'Unknown output or overwrite')
                if case=='output-open-failed':ret=-28
                else:writes[path]={'bytes':0,'crc':0,'marker':b''}
            if ret==0:
                ret=max([2,*fds])+1;fds[ret]=[path,0]
        elif nr==3:
            path,offset=fds[a[0]];need(a[2]<=65536,'Unbounded read')
            if path=='/owner-proc/sys/kernel/random/boot_id':
                block=(b'invalid\n' if case=='bad-boot-id' else BOOT_ID)[offset:offset+a[2]]
            elif path.startswith('/owner-sys/'):
                _,part,field=path.rsplit('/',2)
                data=EXPECTED_SYS[part][('dev','start','size').index(field)].encode()
                if case=='wrong-layout':data=b'0\n'
                block=data[offset:offset+a[2]]
            else:
                need(path in SIZES,'Unknown read source');source_reads+=1
                block=source_bytes(path,offset,min(a[2],4096) if case=='partial-io' else a[2])
                if case=='bad-superblock' and offset==0:
                    block=block[:1080]+b'xx'+block[1082:]
                if case=='source-changed' and opened[path]==2 and offset==0:
                    block=b'x'+block[1:]
                if case=='read-failed':ret=-5
            if ret==0:
                ret=len(block)
                if ret:uc.mem_write(a[1],block);fds[a[0]][1]+=ret
        elif nr==4:
            path,offset=fds[a[0]];data=bytes(uc.mem_read(a[1],a[2]));ret=len(data)
            if path=='/owner-kmsg':logs.append(data)
            else:
                need(path in writes,'Write attempted outside ordinary new files')
                if case=='write-failed':ret=-5
                else:
                    if case=='partial-io':ret=min(ret,1024)
                    data=data[:ret];row=writes[path]
                    if path.endswith('/COMPLETE'):row['marker']+=data
                    else:
                        source=OUTPUTS[path.rsplit('/',1)[1][:-4]]
                        need(data==source_bytes(source,offset,len(data)),'Copied bytes differ')
                    row['bytes']+=ret;row['crc']=zlib.crc32(data,row['crc']);fds[a[0]][1]+=ret
        elif nr==207:
            path=fds[a[0]][0];need((path==BASE or path in writes) and a[1:3]==[2000,2000],'Wrong ownership change')
            if case=='chown-failed':ret=-1
        elif nr==228:
            path=fds[a[0]][0]
            need(path==BASE or path in writes,'Relabelling an existing or source file')
            need(string(a[1])=='security.selinux' and
                 bytes(uc.mem_read(a[2],a[3]))==b'u:object_r:shell_data_file:s0\0'
                 and a[4]==0,'Wrong output label')
            if case=='xattr-failed':ret=-95
            else:labelled.add(path)
        elif nr==118:
            path=fds[a[0]][0];need(path==BASE or path in writes,'Unexpected fsync')
            if case=='fsync-failed':ret=-5
            else:synced.add(path)
        elif nr==6:
            need(a[0] in fds,'Unknown descriptor');path=fds.pop(a[0])[0]
            if case=='close-failed' and path in writes:ret=-5
        elif nr==52:
            path=string(a[0]);need(path in mounts and a[1]==0,'Wrong unmount')
            if case=='unmount-failed' and path=='/owner-host':ret=-16
            else:mounts.remove(path)
        elif nr==10:
            need(string(a[0])=='/owner-kmsg','Deleting anything outside temporary log node')
        elif nr==38:
            need((string(a[0]),string(a[1]))==('/system/bin/stock-init','/system/bin/init')
                 and not mounts,'Init replacement outside clean RAM root')
            if case=='rename-failed':ret=-5
            else:renamed=True
        elif nr==11:
            need(string(a[0])=='/init' and not mounts and renamed,'Unsafe stock handoff')
            ptr,null=struct.unpack('<II',bytes(uc.mem_read(a[1],8)))
            need(string(ptr)=='/init' and null==0,'Wrong init argv')
            if case=='exec-failed':ret=-2
            else:done.append('stock-init');uc.emu_stop();return
        elif nr==88:
            need(a[:3]==[0xfee1dead,0x28121969,0x01234567],'Unexpected reboot call')
            done.append('restored-entry-reboot');uc.emu_stop();return
        else:raise ValueError('Unexpected syscall '+str(nr))
        uc.reg_write(UC_ARM_REG_R0,ret&0xffffffff)
    u.hook_add(UC_HOOK_CODE,checksum,begin=symbols['crc'],end=symbols['crc'])
    u.hook_add(UC_HOOK_INTR,syscall)
    u.emu_start(entry,0,count=20000000)
    need(done,'Candidate did not finish in bound')
    if case in ('wrong-root','extra-arg'):need(not created and not writes,'Writes before root/argc check')
    successful=case in ('normal','partial-io','exec-failed','rename-failed')
    marked=b'<6>FREEGUY_OWNER_CAPTURE_COMPLETE\n' in logs
    need(marked==successful,'Incorrect capture completion signal')
    if successful:
        marker=b'FREEGUY_OWNER_CAPTURE_V1\n'+BOOT_ID
        for name,path in OUTPUTS.items():
            row=writes[BASE+'/'+name+'.img'];need(row['bytes']==SIZES[path],'Short output')
            need(BASE+'/'+name+'.img' in synced&labelled,'Unsynced or unlabelled output')
            marker+=(f"{row['crc']:08x}\n").encode()
        need(writes[BASE+'/COMPLETE']['marker']==marker and
             {BASE,BASE+'/COMPLETE'} <= synced&labelled,'Completion marker mismatch')
    return dict(case=case,end=done[0],syscalls=calls,sourceReads=source_reads,
                checksumModelCalls=crc_calls,partitionWriteDescriptors=0)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build',type=Path,required=True)
    a=p.parse_args();raw=(a.build/'owner-capture').read_bytes()
    manifest=json.loads((a.build/'manifest.json').read_text())
    need(hashlib.sha256(raw).hexdigest()==manifest['sha256'],'Binary changed')
    need(hashlib.sha256((ROOT/'runtime/owner-capture.c').read_bytes()).hexdigest()==manifest['sourceSha256'],
         'Source changed')
    need(not (a.build/'audit.json').exists(),'Retain existing audit; use a separate build directory')
    vectors=crc_vectors(raw)
    cases=['normal','partial-io','not-pid1','wrong-root','extra-arg','bad-boot-id','no-space','existing-output',
           'mount-failed','wrong-layout','symlink-parent','source-open-failed',
           'output-open-failed','bad-superblock','source-changed','read-failed',
           'write-failed','chown-failed','xattr-failed','fsync-failed','close-failed','unmount-failed','rename-failed','exec-failed']
    reports=[]
    for case in cases:
        reports.append(run_case(raw,case));print(case,'passed',flush=True)
    result=dict(binarySha256=manifest['sha256'],sourceSha256=manifest['sourceSha256'],
                crcArmVectors=vectors,cases=reports,deviceAccess=False,deployEligible=False,
                checksumModel='Real ARM CRC separately checked; zlib substitutes it in syscall fault cases',
                filesystemModel='Synthetic files; no storage-controller or power-loss proof')
    (a.build/'audit.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
