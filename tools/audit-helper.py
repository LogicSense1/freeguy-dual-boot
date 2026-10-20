#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Execute actual ARM daily helper with isolated syscall/clock/failure fixtures."""
import argparse,hashlib,io,json,struct
from pathlib import Path
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM,UC_HOOK_INTR
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_R7,UC_ARM_REG_SP
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--helper',type=Path,required=True)
p.add_argument('--report',type=Path,required=True)
a=p.parse_args()
if a.report.exists(): p.error('Use a new report path')
raw=a.helper.read_bytes();elf=ELFFile(io.BytesIO(raw));segments=[s for s in elf.iter_segments() if s['p_type']=='PT_LOAD']
cases=['clock','clock-failed','healthy-hour','healthy-month','startup-expired','health-expired','clock-regressed','clock-error','missing-dir','wrong-dir-owner','wrong-file-mode','wrong-file-owner','symlink-file','already-latched','short-fault-read','latch-write-failed','latch-fsync-failed','latch-readback-failed','trial-cap','dispatch-stock','dispatch-second-stage']
results=[]
for case in cases:
    u=Uc(UC_ARCH_ARM,UC_MODE_ARM);lo=min(s['p_vaddr'] for s in segments)&~4095;hi=max(s['p_vaddr']+s['p_memsz'] for s in segments)
    u.mem_map(lo,(hi-lo+4095)&~4095)
    for s in segments:u.mem_write(s['p_vaddr'],s.data())
    u.mem_map(0x70000000,0x40000);sp=0x70030000
    arg='monotonic-seconds' if case in ('clock','clock-failed') else 'watchdog' if case=='trial-cap' else 'second_stage' if case=='dispatch-second-stage' else 'watchdog-daily'
    u.mem_write(0x70020000,b'/codex/helper\0');u.mem_write(0x70020100,arg.encode()+b'\0')
    u.mem_write(sp,struct.pack('<5I',1 if case=='dispatch-stock' else 2,0x70020000,0 if case=='dispatch-stock' else 0x70020100,0,0));u.reg_write(UC_ARM_REG_SP,sp)
    fs={'/fileboot-host/local/codex-system-default-v1/fault':bytearray(b'FK\n' if case=='already-latched' else b'OK\n')}
    fds={};pos={};state={'clocks':0,'heartbeatReads':0,'fsync':False,'reboot':False,'done':None,'logs':b''}
    def cs(p):return bytes(u.mem_read(p,256)).split(b'\0',1)[0].decode()
    def stop(reason):state['done']=reason;u.emu_stop()
    def now():
        n=state['clocks']
        if n<=1:return 10
        if case=='healthy-hour':return 3610
        if case=='healthy-month':return 31*86400
        if case=='clock-regressed':return 9
        if case=='health-expired':return 10 if n==2 else 200
        return 610
    def hook(uc,irq,_):
        assert irq==2;nr=u.reg_read(UC_ARM_REG_R7);a=[u.reg_read(r) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3)];ret=0
        if nr==263:
            assert a[0]==1;state['clocks']+=1
            if case=='clock-failed' or case=='clock-error' and state['clocks']>1:ret=-5
            else:u.mem_write(a[1],struct.pack('<2I',now(),900000000))
        elif nr==5:
            p=cs(a[0]);assert p in ('/fileboot-host/local/codex-system-default-v1','/proc/self/oom_score_adj','/dev/codex-daily-watchdog-ready','/dev/codex-health-heartbeat','/proc/uptime')
            if p=='/fileboot-host/local/codex-system-default-v1' and case=='missing-dir':ret=-2
            else:
                if p=='/dev/codex-health-heartbeat':
                    state['heartbeatReads']+=1
                    value=now() if case.startswith('healthy-') else 10 if case=='health-expired' else -1
                    fs[p]=bytearray(str(value).encode()+b'\n')
                    if case.startswith('healthy-') and state['heartbeatReads']>=3:stop('healthy-without-deadline')
                if p=='/proc/uptime':
                    state['clocks']+=1;fs[p]=bytearray((str(10 if state['clocks']==1 else 3610)+'.00 0.00\n').encode())
                ret=max([2,*fds])+1;fds[ret]=p;pos[ret]=0;fs.setdefault(p,bytearray())
        elif nr==322:
            assert fds[a[0]]=='/fileboot-host/local/codex-system-default-v1' and cs(a[1])=='fault' and a[2]==2|0o100000
            if case=='symlink-file':ret=-40
            else:ret=max([2,*fds])+1;fds[ret]='/fileboot-host/local/codex-system-default-v1/fault';pos[ret]=0
        elif nr==197:
            p=fds[a[0]];d=p.endswith('-v1');st=[0]*32;st[4]=0o40700 if d else 0o100600;st[5]=2 if d else 1;st[6]=st[7]=1000;st[12]=4096 if d else 3
            if d and case=='wrong-dir-owner' or not d and case=='wrong-file-owner':st[6]=2000
            if not d and case=='wrong-file-mode':st[4]=0o100666
            u.mem_write(a[1],struct.pack('<32I',*st))
        elif nr==3:
            fd=a[0];p=fds[fd];v=bytes(fs[p][pos[fd]:pos[fd]+a[2]])
            if p.endswith('/fault') and (case=='short-fault-read' or case=='latch-readback-failed' and state['fsync']):v=v[:1]
            u.mem_write(a[1],v);pos[fd]+=len(v);ret=len(v)
        elif nr==4:
            v=bytes(u.mem_read(a[1],a[2]));ret=len(v)
            if a[0] in (1,2):state['logs']+=v
            else:
                fd=a[0];p=fds[fd]
                if p.endswith('/fault'):
                    assert v==b'F' and pos[fd]==0
                    if case=='latch-write-failed':ret=-5
                if ret>=0:fs[p][pos[fd]:pos[fd]+len(v)]=v;pos[fd]+=len(v)
        elif nr==19:assert a[1:3]==[0,0];pos[a[0]]=0
        elif nr==118:
            assert fds[a[0]].endswith('/fault')
            if case=='latch-fsync-failed':ret=-5
            else:state['fsync']=True
        elif nr==6:assert a[0] in fds
        elif nr==162:
            sec,nsec=struct.unpack('<2I',bytes(u.mem_read(a[0],8)))
            if sec==30:stop('reboot-suppressed')
        elif nr==36:pass
        elif nr==88:
            assert a[:3]==[0xfee1dead,672274793,0x01234567]
            if case!='trial-cap':assert fs['/fileboot-host/local/codex-system-default-v1/fault']==b'FK\n' and state['fsync']
            state['reboot']=True;stop('reboot')
        elif nr==1:state['exit']=a[0];stop('exit')
        elif nr==11:
            assert case.startswith('dispatch-')
            assert cs(a[0])==('/system/bin/stock-init' if case=='dispatch-stock' else '/system/bin/sh');stop('exec')
        else:raise AssertionError((case,nr,a))
        u.reg_write(UC_ARM_REG_R0,ret&0xffffffff)
    u.hook_add(UC_HOOK_INTR,hook);u.emu_start(elf.header['e_entry'],0,count=3000000)
    if case in ('healthy-hour','healthy-month'):assert state['done']=='healthy-without-deadline' and not state['reboot']
    elif case in ('startup-expired','health-expired','clock-regressed','clock-error','trial-cap'):assert state['done']=='reboot'
    elif case.startswith('latch-'):assert state['done']=='reboot-suppressed' and not state['reboot']
    elif case.startswith('dispatch-'):assert state['done']=='exec'
    elif case=='clock':assert state['exit']==0 and state['logs']==b'10\n'
    elif case=='clock-failed':assert state['exit']==78
    else:assert state['exit']==79 and not state['reboot']
    results.append({'case':case,'result':state['done'],'passed':True})
report={'passed':True,'helperSha256':hashlib.sha256(raw).hexdigest(),'cases':results,'realPowerLossProven':False,'clockModel':'ARM CLOCK_MONOTONIC; fixtures cannot prove actual suspend behavior'}
a.report.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':True,'cases':len(results)}))
