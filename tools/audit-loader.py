#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run actual ARM loader with isolated syscall model, never touch television."""
import argparse,hashlib,io,json,struct,zlib,sys
sys.dont_write_bytecode = True
from pathlib import Path
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_ARM,UC_HOOK_INTR
from unicorn.arm_const import *
R=Path(__file__).resolve().parents[1]
ap=argparse.ArgumentParser();ap.add_argument('--build',type=Path,required=True);args=ap.parse_args()
args.google_daily=True;args.google_wifi=True;args.google=True;args.restore_env=True;args.inspect_env=False
success_end='accepted-dispatcher-exec';any_env=True;D=args.build.resolve()
google_entry=b'envdaily' if args.google_daily else b'envwifi' if args.google_wifi else b'envgoogle'
m=json.loads((D/'manifest.json').read_text());raw=(D/'loader').read_bytes()
assert hashlib.sha256(raw).hexdigest()==m['sha256']
elf=ELFFile(io.BytesIO(raw));assert not any(s['p_type']=='PT_INTERP' for s in elf.iter_segments())
segments=[s for s in elf.iter_segments() if s['p_type']=='PT_LOAD']
expected={e['name']:e for e in m['entries']};reports=[]
cases=['normal','partial-writes','not-pid1','extra-arg','wrong-fs','writable-root','tmpfs-failed',
       'create-failed','write-failed','symlink-failed','proc-failed','cmdline-failed','pivot-failed',
       'oldroot-detach-failed','exec-failed']
if args.inspect_env:cases+=['sysfs-failed','wrong-partition','env-mount-failed','bad-env-crc','env-too-long','backup-nonstock','partial-env-reads','writable-env']
if args.restore_env:
 cases+=['sysfs-failed','wrong-partition','env-mount-failed','bad-env-crc','bad-backup-crc','backup-other-trial','env-open-failed','env-not-regular','env-wrong-size','partial-env-reads','readonly-env','backup-write-failed','main-write-failed','backup-fsync-failed','main-fsync-failed','backup-readback-failed','main-readback-failed']
 cases+=['powercut-'+str(i) for i in range(1,33)]
 from internal_env_rollback import REPLACE,pack,rollback,unpack
 del REPLACE[b'init=/local/tmp/rd-root/envread'];REPLACE[b'init=/local/tmp/rd-root/'+(google_entry if args.google else b'envonce')]=b'init=/init'
def envfile(stock=True):
 b=b'bootargs=root=/dev/ram rw init=/init\0makedata=true\0\0' if stock else b'bootargs=root=/dev/mmcblk0p42 ro init=/local/tmp/rd-root/envread rdinit=/codex-no-rdinit\0makedata=true\0\0'
 b=b.ljust(65532,b'\0');return struct.pack('<I',zlib.crc32(b))+b
for case in cases:
 u=Uc(UC_ARCH_ARM,UC_MODE_ARM);lo=min(s['p_vaddr'] for s in segments)&~4095
 hi=max(s['p_vaddr']+s['p_memsz'] for s in segments)
 u.mem_map(lo,(hi-lo+4095)&~4095)
 for s in segments:u.mem_write(s['p_vaddr'],s.data())
 u.mem_map(0x70000000,0x40000);sp=0x70030000
 u.mem_write(sp,struct.pack('<4I',2 if case=='extra-arg' else 1,0,0,0));u.reg_write(UC_ARM_REG_SP,sp)
 state={'tmpfs':False,'cwd':False,'pivot':False,'detached':False};nodes={};fds={};positions={};logs=[];done=[];calls=[]
 if args.restore_env:
  value=b'bootargs=root=/dev/mmcblk0p42 ro init=/local/tmp/rd-root/envonce loglevel=7 rdinit=/codex-no-rdinit rootfstype=f2fs rootflags=norecovery lastblval=75'
  if args.google:value=value.replace(b'/envonce',b'/'+google_entry)
  envfiles={'.codex-env/uboot.env':pack([value,b'unchanged=main']),'.codex-env/uboot.env.bak':pack([value,b'unchanged=backup'])}
  clean={p:rollback(v) for p,v in envfiles.items()}
  if case=='bad-env-crc':envfiles['.codex-env/uboot.env']=b'xxxx'+envfiles['.codex-env/uboot.env'][4:]
  if case=='bad-backup-crc':envfiles['.codex-env/uboot.env.bak']=b'xxxx'+envfiles['.codex-env/uboot.env.bak'][4:]
  if case=='backup-other-trial':envfiles['.codex-env/uboot.env.bak']=pack([value.replace(b'/'+google_entry if args.google else b'/envonce',b'/other'),b'unchanged=backup'])
  original_env=envfiles.copy();state['envwrites']=0;state['synced']=set();state['env_written']=set()
 commandline=b'root=/dev/mmcblk0p42 ro private_device_token=fixture-only '+b'x'*1870+b'\n'
 def cs(a):return bytes(u.mem_read(a,512)).split(b'\0')[0].decode() if a else None
 def hook(uc,irq,_):
  assert irq==2
  nr=uc.reg_read(UC_ARM_REG_R7);a=[uc.reg_read(r) for r in [UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_R4]];ret=0;calls.append(nr)
  if nr==20:ret=2000 if case=='not-pid1' else 1
  elif nr==1:
   assert case=='not-pid1' and a[0]==90;done.append('unprivileged-exit');uc.emu_stop();return
  elif nr==99:
   p=cs(a[0]);assert p=='/' or any_env and p=='.codex-env' and state.get('env')
   fs=[0]*32;fs[0]=0xef53 if p=='.codex-env' else 0x01021994 if state['pivot'] else 0xef53 if case=='wrong-fs' else 0xf2f52010
   fs[11]=0 if p=='.codex-env' and case=='writable-env' or p=='/' and (case=='writable-root' or state['pivot']) else 1
   if args.restore_env and p=='.codex-env':fs[11]=int(case=='readonly-env')
   uc.mem_write(a[1],struct.pack('<32I',*fs))
  elif nr==21:
   s,t,f=map(cs,a[:3])
   if f=='tmpfs':
    assert (s,t,a[3],cs(a[4]))==('tmpfs','/local/tmp/rd-root/scratch',2,'mode=0755,size=192m' if args.google else 'mode=0755,size=64m')
    ret=-13 if case=='tmpfs-failed' else 0;state['tmpfs']=ret==0
   elif f=='proc':
    assert state['cwd'] and (s,t,a[3],a[4])==('proc','.codex-proc',15,0)
    ret=-13 if case=='proc-failed' else 0;state['proc']=ret==0
   elif f=='sysfs':
    assert any_env and state['cwd'] and (s,t,a[3],a[4])==('sysfs','.codex-sys',15,0)
    ret=-13 if case=='sysfs-failed' else 0;state['sys']=ret==0
   elif f=='ext4':
    assert any_env and state['cwd'] and (s,t,a[3],cs(a[4]))==('.codex-env-block','.codex-env',14 if args.restore_env else 15,None if args.restore_env else 'noload')
    assert state.get('envnode')
    ret=-22 if case=='env-mount-failed' else 0;state['env']=ret==0
   else:assert state['cwd'] and s is None and t=='/' and a[3:]==[278528,0]
  elif nr==12:
   if not state['pivot']:assert cs(a[0])=='/local/tmp/rd-root/scratch' and state['tmpfs'];state['cwd']=True
   else:assert cs(a[0])=='/'
  elif nr==39:
   p=cs(a[0]);assert state['cwd'] and not p.startswith('/') and p not in nodes and a[1]==0o700
   assert p in ('.codex-proc','.codex-oldroot','.codex-sys','.codex-env') or expected[p]['mode']&0o170000==0o40000
   nodes[p]={'kind':'dir'}
  elif nr==5:
   p=cs(a[0]);assert state['cwd'] and not p.startswith('/')
   if p=='dev/kmsg':assert state['kmsg'] and a[1:3]==[1,0]
   elif p=='.codex-proc/cmdline':assert state['proc'] and a[1:3]==[0,0]
   elif p.startswith('.codex-sys/'):
    assert any_env and state['sys'] and a[1:3]==[0,0] and p.rsplit('/',1)[1] in ('dev','start','size')
   elif p.startswith('.codex-env/'):
    assert any_env and state['env'] and a[1:3]==[131074 if args.restore_env else 131072,0] and p.rsplit('/',1)[1] in ('uboot.env','uboot.env.bak')
    if case=='env-open-failed':ret=-13
   else:
    assert p in expected and expected[p]['mode']&0o170000==0o100000
    assert a[1:3]==[1|64|128|131072,0o600] and p not in nodes
    if case=='create-failed':ret=-28
    else:nodes[p]={'kind':'file','data':bytearray()}
   if ret==0:ret=max([2,*fds])+1;fds[ret]=p;positions[ret]=0
  elif nr==4:
   p=fds[a[0]];b=bytes(uc.mem_read(a[1],a[2]));assert state['cwd']
   if p=='dev/kmsg':logs.append(b);ret=len(b)
   elif args.restore_env and p in envfiles:
    if not p.endswith('.bak'):
     bp='.codex-env/uboot.env.bak';assert bp in state['synced'] and envfiles[bp]==clean[bp]
     assert b'<6>CTV_ENV_ONCE_BACKUP_FLUSHED_READBACK_VERIFIED\n' in logs
    if case==('backup-write-failed' if p.endswith('.bak') else 'main-write-failed'):
     ret=-5
    else:
     ret=min(len(b),4096);offset=positions[a[0]]
     assert offset+ret<=65536
     envfiles[p]=envfiles[p][:offset]+b[:ret]+envfiles[p][offset+ret:]
     positions[a[0]]+=ret;state['envwrites']+=1;state['env_written'].add(p)
     if case=='powercut-'+str(state['envwrites']):done.append('simulated-powercut');uc.emu_stop();return
   else:
    if case=='write-failed':ret=-28
    else:
     ret=min(len(b),8192) if case=='partial-writes' else len(b);nodes[p]['data'].extend(b[:ret])
  elif nr==3:
   p=fds[a[0]]
   if p.startswith('.codex-sys/'):
    assert any_env and a[2]==79
    data={'dev':b'179:3\n','start':b'45056\n','size':b'32768\n'}[p.rsplit('/',1)[1]]
    if case=='wrong-partition':data=b'1\n'
   elif p.startswith('.codex-env/'):
    assert any_env
    if args.restore_env:
     data=envfiles[p]
     if p in state['env_written'] and case==('backup-readback-failed' if p.endswith('.bak') else 'main-readback-failed'):data=data[:99]+bytes([data[99]^1])+data[100:]
    else:
     data=envfile(p.endswith('.bak') and case!='backup-nonstock')
     if case=='bad-env-crc':data=data[:4]+b'X'+data[5:]
     if case=='env-too-long':data+=b'x'
   else:
    assert p=='.codex-proc/cmdline' and a[2]==4095;data=commandline
   offset=positions[a[0]];part=data[offset:offset+min(a[2],1024 if case=='partial-env-reads' else a[2])]
   ret=-5 if p=='.codex-proc/cmdline' and case=='cmdline-failed' else len(part)
   if ret>0:uc.mem_write(a[1],part);positions[a[0]]+=ret
  elif nr==6:assert a[0] in fds;del fds[a[0]]
  elif nr==19:
   assert args.restore_env and fds[a[0]] in envfiles and a[1:3]==[0,0];positions[a[0]]=0
  elif nr==197:
   assert args.restore_env and fds[a[0]] in envfiles
   st=bytearray(128);struct.pack_into('<II',st,16,0o040700 if case=='env-not-regular' else 0o100600,1)
   struct.pack_into('<Q',st,48,100 if case=='env-wrong-size' else 65536);uc.mem_write(a[1],bytes(st))
  elif nr==118:
   assert args.restore_env and fds[a[0]] in envfiles;p=fds[a[0]]
   if case==('backup-fsync-failed' if p.endswith('.bak') else 'main-fsync-failed'):ret=-5
   else:assert envfiles[p]==clean[p];state['synced'].add(p)
  elif nr==83:
   target,p=cs(a[0]),cs(a[1]);assert state['cwd'] and p in expected and p not in nodes
   assert expected[p]['mode']&0o170000==0o120000
   ret=-13 if case=='symlink-failed' else 0
   if ret==0:nodes[p]={'kind':'symlink','data':target.encode()}
  elif nr==198:
   p=cs(a[0]);assert state['cwd'] and p in nodes and a[1:3]==[expected[p]['uid'],expected[p]['gid']]
   nodes[p]['ownership']=True
  elif nr==15:
   p=cs(a[0]);assert state['cwd'] and nodes[p]['kind']!='symlink' and a[1]==expected[p]['mode']&0o7777
   nodes[p]['mode']=True
  elif nr==14:
   assert state['cwd']
   if cs(a[0])=='dev/kmsg':assert a[1:3]==[0x2180,0x10b];state['kmsg']=True
   else:
    assert any_env and cs(a[0])=='.codex-env-block' and a[1:3]==[0x6180,0xb303];state['envnode']=True
  elif nr==218:
   assert state['cwd'] and cs(a[0])=='.' and cs(a[1])=='.codex-oldroot'
   ret=-22 if case=='pivot-failed' else 0;state['pivot']=ret==0
  elif nr==52:
   p=cs(a[0])
   if p=='.codex-proc':assert state['proc'] and a[1]==0;state['proc']=False
   elif p=='.codex-sys':assert any_env and state['sys'] and a[1]==0;state['sys']=False
   elif p=='.codex-env':assert any_env and state['env'] and a[1]==0;state['env']=False
   else:
    assert p=='/.codex-oldroot' and state['pivot'] and a[1]==2
    ret=-16 if case=='oldroot-detach-failed' else 0;state['detached']=ret==0
  elif nr==40:
   p=cs(a[0]);assert p in ('.codex-proc','/.codex-oldroot','.codex-sys','.codex-env')
   if p.startswith('/'):assert state['detached']
   del nodes[p.lstrip('/')]
  elif nr==10:
   if cs(a[0])=='/dev/kmsg':assert state['pivot'];state['kmsg']=False
   else:assert any_env and cs(a[0])=='.codex-env-block' and state['envnode'] and not state.get('env');state['envnode']=False
  elif nr==11:
   assert state['detached'] and cs(a[0])=='/init' and not fds
   assert struct.unpack('<II',uc.mem_read(a[1],8))[1]==0 and a[2]==sp+12
   assert set(nodes)==set(expected)
   for p,n in nodes.items():
    e=expected[p];assert n['ownership'] and (n['kind']=='symlink' or n['mode'])
    data=n.get('data',b'');assert len(data)==e['bytes'] and hashlib.sha256(data).hexdigest()==e['sha256']
   if case=='exec-failed':ret=-2
   else:done.append(success_end);uc.emu_stop();return
  elif nr==162:assert struct.unpack('<ii',uc.mem_read(a[0],8))==(5,0)
  elif nr==88:
   assert case not in ('normal','partial-writes','not-pid1') and a[:4]==[0xfee1dead,0x28121969,0x01234567,0]
   done.append('reboot-for-uart');uc.emu_stop();return
  else:raise AssertionError('Unexpected syscall '+str(nr))
  uc.reg_write(UC_ARM_REG_R0,ret&0xffffffff)
 u.hook_add(UC_HOOK_INTR,hook);u.emu_start(elf['e_entry'],lo+0x4000000,count=30000000)
 assert len(done)==1,(case,done)
 if case in ('normal','partial-writes'):
  assert ('<6>CTV_CMDLINE_BYTES=%04d\n'%len(commandline)).encode() in logs
  assert not any(b'CTV_STOCK_CMDLINE_' in v or b'private_device_token' in v or b'fixture-only' in v for v in logs)
  assert len(logs)<=10
 if args.inspect_env and done==[success_end]:
  assert not any(state.get(k) for k in ['sys','env','envnode'])
  if case in ('normal','partial-writes','partial-env-reads'):assert b'<6>CTV_ENV_RO_BOTH_CRC_VALID_BACKUP_STOCK\n' in logs
  if case=='backup-nonstock':assert b'<6>CTV_ENV_RO_BOTH_CRC_VALID_BACKUP_NONSTOCK\n' in logs
  if case in ('bad-env-crc','env-too-long'):assert b'<6>CTV_ENV_RO_FILE_VALIDATION_FAILED\n' in logs
  if case in ('sysfs-failed','wrong-partition','env-mount-failed'):assert b'<6>CTV_ENV_RO_INSPECTION_UNAVAILABLE\n' in logs
 if args.restore_env:
  if done==[success_end]:
   assert envfiles==clean and len(state['synced'])==2 and not any(state.get(k) for k in ['sys','env','envnode'])
  if state['envwrites']:
   valid=[]
   for p,v in envfiles.items():
    try:unpack(v);valid.append(p)
    except ValueError:pass
   assert valid,'Both environment copies invalid'
  if case in ('sysfs-failed','wrong-partition','env-mount-failed','bad-env-crc','bad-backup-crc','backup-other-trial','env-open-failed','env-not-regular','env-wrong-size','readonly-env'):
   assert not state['envwrites'] and envfiles==original_env
 reports.append({'case':case,'end':done[0],'syscalls':len(calls),'writesConfinedToNewTmpfs':not args.restore_env,'environmentWrites':state.get('envwrites',0)})
report={'passed':True,'sha256':m['sha256'],'sourceSha256':m['sourceSha256'],'cases':reports,'originalEntriesVerified':len(expected),'liveTested':False,'scope':'Actual ARM instructions; mocked filesystem and exec/reboot; no hardware proof'}
if args.inspect_env:
 assert hashlib.sha256((R/'tools/internal-env-inspect.h').read_bytes()).hexdigest()==m['inspectorSourceSha256']
 report['inspectorSourceSha256']=m['inspectorSourceSha256']
if args.restore_env:
 for n,h in m['componentSources'].items():assert hashlib.sha256((R/'runtime'/n).read_bytes()).hexdigest()==h
 report['componentSources']=m['componentSources']
 report['powercutScope']='Simulated partial file writes with successful fsync assumed durable; does not model storage/controller failure or ext4 recovery'
(D/'audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':True,'cases':len(reports),'originalEntriesVerified':len(expected)}))
